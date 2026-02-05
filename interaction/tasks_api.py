# 任务收集 API 逻辑：读写 task_list.csv / custom_task_list.csv，创建列表、导入 CSV、启动运行。
# 不依赖 Flask，仅被 app.py 调用。

import csv
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

# 工程根目录由调用方传入；运行脚本所在目录 = app 所在目录（项目根）
_APP_DIR = Path(__file__).resolve().parent.parent

TASK_LIST_FILENAME = "task_list.csv"
CUSTOM_TASK_LIST_FILENAME = "custom_task_list.csv"

# 后台运行状态：start 时设 running=True，子进程结束后线程设 running=False
_run_state: dict[str, Any] = {"running": False, "mode": None}
_run_process: subprocess.Popen | None = None


def _run_list_path(project_root: Path, mode: str) -> Path:
    if mode == "custom":
        return project_root / CUSTOM_TASK_LIST_FILENAME
    return project_root / TASK_LIST_FILENAME


def load_task_list_csv(project_root: Path, mode: str) -> list[dict[str, Any]]:
    """从工程根目录读取 task_list.csv 或 custom_task_list.csv，返回行字典列表。支持 UTF-8 与 GBK。"""
    path = _run_list_path(project_root, mode)
    if not path.is_file():
        return []
    tasks = []
    for encoding in ("utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tasks.append(dict(row))
            break
        except UnicodeDecodeError:
            tasks = []
            continue
    return tasks


def get_task_list_response(project_root: Path, mode: str) -> dict[str, Any]:
    """
    返回 GET /api/task-list 所需结构：tasks, mode, total, done_count, current_task。
    current_task 为第一个 status=running 的项，无则为 None。
    """
    tasks = load_task_list_csv(project_root, mode)
    total = len(tasks)
    done_count = sum(1 for t in tasks if (t.get("status") or "").lower() in ("success", "failed"))
    current = next((t for t in tasks if (t.get("status") or "").lower() == "running"), None)
    return {
        "mode": mode,
        "tasks": tasks,
        "total": total,
        "done_count": done_count,
        "current_task": current,
    }


def create_androidworld_list(
    project_root: Path,
    api_key: str,
    base_url: str,
    tasks: list[str] | None = None,
    n_task_combinations: int = 1,
    task_random_seed: int = 30,
) -> tuple[bool, str]:
    """
    在工程根目录创建 task_list.csv（AndroidWorld 生成）。
    通过 subprocess 调用 run_androidworld_task.py --create_list_only。
    返回 (成功, 消息)。
    """
    script = _APP_DIR / "task_collection" / "run_androidworld_task.py"
    if not script.is_file():
        return False, "未找到 run_androidworld_task.py"
    project_root = project_root.resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        "--output_dir", str(project_root),
        "--api_key", api_key or "",
        "--base_url", base_url or "",
        "--n_task_combinations", str(n_task_combinations),
        "--task_random_seed", str(task_random_seed),
        "--create_list_only", "true",
    ]
    if tasks:
        cmd.extend(["--tasks", ",".join(tasks)])
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_APP_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout or "创建任务列表失败"
        return True, f"已创建 {len(load_task_list_csv(project_root, 'androidworld'))} 个任务"
    except subprocess.TimeoutExpired:
        return False, "创建超时"
    except Exception as e:
        return False, str(e)


def save_custom_task_list(project_root: Path, csv_content: bytes) -> tuple[bool, str]:
    """将用户上传的 CSV 内容写入工程根目录的 custom_task_list.csv。"""
    project_root = project_root.resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / CUSTOM_TASK_LIST_FILENAME
    try:
        path.write_bytes(csv_content)
        return True, f"已保存为 {CUSTOM_TASK_LIST_FILENAME}"
    except Exception as e:
        return False, str(e)


def _on_run_finished(process: subprocess.Popen) -> None:
    process.wait()
    global _run_state, _run_process
    _run_state["running"] = False
    _run_state["mode"] = None
    _run_process = None


def start_run(project_root: Path, mode: str, config: dict[str, Any]) -> tuple[bool, str]:
    """
    在后台启动任务运行：androidworld 调用 run_androidworld_task，custom 调用 run_custom_task。
    config 需含 autoglm.api_key、autoglm.base_url。
    返回 (成功, 消息)。
    """
    global _run_state, _run_process
    if _run_state.get("running"):
        return False, "已有任务在运行中"
    project_root = project_root.resolve()
    api_key = (config.get("autoglm") or {}).get("api_key") or ""
    base_url = (config.get("autoglm") or {}).get("base_url") or ""

    if mode == "androidworld":
        list_path = project_root / TASK_LIST_FILENAME
        if not list_path.is_file():
            return False, "请先「生成任务」再开始运行"
        script = _APP_DIR / "task_collection" / "run_androidworld_task.py"
        if not script.is_file():
            return False, "未找到 run_androidworld_task.py"
        cmd = [
            sys.executable,
            str(script),
            "--output_dir", str(project_root),
            "--api_key", api_key,
            "--base_url", base_url,
            "--create_list_only", "false",
        ]
    else:
        list_path = project_root / CUSTOM_TASK_LIST_FILENAME
        if not list_path.is_file():
            return False, "请先「导入 CSV」再开始运行"
        script = _APP_DIR / "task_collection" / "run_custom_task.py"
        if not script.is_file():
            return False, "未找到 run_custom_task.py"
        cmd = [
            sys.executable,
            str(script),
            "--csv", str(list_path),
            "--output-dir", str(project_root),
            "--api-key", api_key,
            "--base-url", base_url,
        ]

    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        _run_process = subprocess.Popen(
            cmd,
            cwd=str(_APP_DIR),
            stdout=None,
            stderr=None,
            env=env,
        )
        _run_state["running"] = True
        _run_state["mode"] = mode
        t = threading.Thread(target=_on_run_finished, args=(_run_process,), daemon=True)
        t.start()
        return True, "已启动运行"
    except Exception as e:
        return False, str(e)


def get_run_state() -> dict[str, Any]:
    """返回当前运行状态，供 GET /api/task-progress 或前端轮询。"""
    return {"running": _run_state.get("running", False), "mode": _run_state.get("mode")}
