"""
用自定义 CSV 驱动 AutoGLM（AutoGLM PhoneAgent）跑任务：不建 android_world env，
每个任务前 adb home + 等 5s，按 step 执行并写 step_xxx.json / step_xxx.png、task_info.json、task_params.json，
result 只记大模型最后返回，不做成功判定。适用于真实设备。

用法（项目根目录）:
  python task_collection/run_custom_task.py --csv <任务CSV路径> [--output-dir <输出目录>] [--device-id <id>]
"""
import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 把 AutoGLM 加入 path，直接使用 PhoneAgent
_ROOT = Path(__file__).resolve().parent.parent
_AUTOGLM = _ROOT / "AutoGLM"
if _AUTOGLM.is_dir() and str(_AUTOGLM) not in sys.path:
    sys.path.insert(0, str(_AUTOGLM))

from phone_agent import PhoneAgent
from phone_agent.adb import get_current_app, get_screenshot, home
from phone_agent.agent import AgentConfig, StepResult
from phone_agent.model import ModelConfig


# 需要强制停止的应用包名列表（任务启动前清空）
APPS_TO_FORCE_STOP = [
    "com.tencent.mm",           # 微信
    "com.taobao.taobao",        # 淘宝
    "com.xingin.xhs",           # 小红书
    "com.ss.android.ugc.aweme", # 抖音
    "com.sankuai.meituan",      # 美团
    "com.android.deskclock",    # 闹钟
]

# 微信包名（保留以兼容旧代码）
WECHAT_PACKAGE = "com.tencent.mm"


def _force_stop_app(device_id: str | None, package: str) -> None:
    adb_prefix = ["adb"]
    if device_id:
        adb_prefix = ["adb", "-s", device_id]
    subprocess.run(
        adb_prefix + ["shell", "am", "force-stop", package],
        capture_output=True,
        timeout=5,
    )


def _get_first_device() -> str | None:
    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="ignore",
        )
        if r.returncode != 0:
            return None
        for line in r.stdout.strip().splitlines()[1:]:
            line = line.strip()
            if line and "\tdevice" in line:
                return line.split("\t")[0].strip()
    except Exception:
        pass
    return None


def _save_screenshot_png(screenshot, path: Path) -> None:
    raw = base64.b64decode(screenshot.base64_data)
    path.write_bytes(raw)


def load_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def run_one_task(
    row: dict,
    output_dir: Path,
    device_id: str | None,
    model_config: ModelConfig,
    max_steps: int,
    lang: str,
    home_wait: float,
) -> str | None:
    task_id = (row.get("task_id") or "").strip()
    task_goal = (row.get("task_goal") or "").strip()
    task_name = (row.get("task_name") or "").strip()
    difficulty = (row.get("difficulty") or "1").strip()

    if not task_id or not task_goal:
        return None

    task_dir = output_dir / task_id
    steps_dir = task_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    task_params = {
        "task_goal": task_goal,
        "task_name": task_name,
        "difficulty": difficulty,
    }
    (task_dir / "task_params.json").write_text(
        json.dumps(task_params, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    home(device_id, delay=0)
    time.sleep(home_wait)
    # 清空所有指定的应用（微信、淘宝、小红书、抖音、美团、闹钟），每个应用间隔1秒
    for package in APPS_TO_FORCE_STOP:
        _force_stop_app(device_id, package)
        time.sleep(1)  # 每个应用清空后等待1秒

    agent_config = AgentConfig(
        device_id=device_id,
        max_steps=max_steps,
        lang=lang,
        verbose=True,
    )
    agent = PhoneAgent(model_config=model_config, agent_config=agent_config)

    start_time = datetime.now().isoformat()
    step_num = 1
    final_message: str | None = None
    finished = False

    while step_num <= max_steps:
        # 在操作执行前获取截图（与输入给大模型的截图一致）
        current_app = get_current_app(device_id)
        screenshot = get_screenshot(device_id)
        ts = datetime.now().isoformat()

        step_path_json = steps_dir / f"step_{step_num:03d}.json"
        step_path_png = steps_dir / f"step_{step_num:03d}.png"
        _save_screenshot_png(screenshot, step_path_png)

        # 执行操作（agent.step 内部会再次获取截图用于模型推理）
        goal_input = task_goal if step_num == 1 else None
        try:
            result: StepResult = agent.step(goal_input)
        except ValueError:
            result = agent.step(task_goal)

        action = result.action if result.action is not None else {}
        step_data = {
            "step_number": step_num,
            "timestamp": ts,
            "current_app": current_app,
            "action": action,
            "thinking": result.thinking or "",
            "is_first_step": step_num == 1,
            "task_goal": task_goal,
            "app_name": "",
            "page_name": "",
            "page_description": "",
            "differentiated_content": "",
        }
        step_path_json.write_text(
            json.dumps(step_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        final_message = result.message
        finished = result.finished
        if finished:
            break
        step_num += 1

    end_time = datetime.now().isoformat()
    try:
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
        duration_seconds = (end_dt - start_dt).total_seconds()
    except Exception:
        duration_seconds = 0.0

    task_info = {
        "task_id": task_id,
        "task_goal": task_goal,
        "start_time": start_time,
        "end_time": end_time,
        "max_steps": max_steps,
        "device_id": device_id or "",
        "lang": lang,
        "total_steps": step_num,
        "finished": finished,
        "final_message": final_message or "",
        "duration_seconds": duration_seconds,
    }
    (task_dir / "task_info.json").write_text(
        json.dumps(task_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return final_message


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用自定义 CSV 驱动 AutoGLM 跑任务（真实设备，不建 env）"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="任务 CSV 路径（必填）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="输出目录，默认与 CSV 同目录",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default=None,
        help="ADB device id，不填则用第一个已连接设备",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="",
        help="模型 API base URL，未传时从工程目录 project_config.json 读取",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("AUTOGLM_API_KEY", ""),
        help="API key，也可用环境变量 AUTOGLM_API_KEY；未传时从工程目录 project_config.json 读取",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="autoglm-phone",
        help="模型名",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="每任务最大步数",
    )
    parser.add_argument(
        "--lang",
        type=str,
        choices=["cn", "en"],
        default="cn",
        help="界面语言",
    )
    parser.add_argument(
        "--home-wait",
        type=float,
        default=5.0,
        help="任务前按 Home 后等待秒数",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="跳过 CSV 中 status 已为 success 的任务",
    )
    args = parser.parse_args()

    csv_path = args.csv.resolve()
    if not csv_path.is_file():
        print(f"CSV 不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = csv_path.parent
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    device_id = args.device_id
    if not device_id:
        device_id = _get_first_device()
        if not device_id:
            print("未找到 ADB 设备，请连接设备或指定 --device-id", file=sys.stderr)
            sys.exit(1)
    print(f"使用设备: {device_id}")

    model_config = ModelConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        model_name=args.model_name,
    )

    rows = load_csv(csv_path)
    if not rows:
        print("CSV 为空", file=sys.stderr)
        sys.exit(0)

    for i, row in enumerate(rows):
        task_id = (row.get("task_id") or "").strip()
        if not task_id:
            continue
        if args.skip_completed and (row.get("status") or "").strip().lower() == "success":
            print(f"跳过已完成: {task_id}")
            continue

        print(f"\n[{i + 1}/{len(rows)}] 运行 {task_id} ...")
        start_iso = datetime.now().isoformat()
        row["start_time"] = start_iso
        try:
            final_message = run_one_task(
                row,
                output_dir,
                device_id,
                model_config,
                args.max_steps,
                args.lang,
                args.home_wait,
            )
        except Exception as e:
            print(f"  ❌ 异常: {e}", file=sys.stderr)
            row["status"] = "failed"
            row["result"] = ""
            row["error_message"] = str(e)
            row["end_time"] = datetime.now().isoformat()
            if row.get("start_time"):
                try:
                    row["duration_seconds"] = str(
                        (datetime.fromisoformat(row["end_time"]) - datetime.fromisoformat(row["start_time"])).total_seconds()
                    )
                except Exception:
                    pass
            save_csv(csv_path, rows)
            continue

        end_iso = datetime.now().isoformat()
        row["status"] = "success"
        row["result"] = final_message or ""
        row["error_message"] = ""
        row["end_time"] = end_iso
        try:
            row["duration_seconds"] = str(
                (datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds()
            )
        except Exception:
            row["duration_seconds"] = ""
        save_csv(csv_path, rows)
        print(f"  完成，result: {(final_message or '')[:80]}...")

    print("\n全部完成。")


if __name__ == "__main__":
    main()
