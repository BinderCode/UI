"""
批量处理 autoglm_runs 下所有任务步骤截图：维护 image_list.csv，断点续处理。
依赖 process_single_image 中的上传与工作流调用。
"""
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 兼容从项目根目录执行：python post_processing/process_all_images.py 或 python -m post_processing.process_all_images
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 运行后处理脚本前输出运行环境路径，便于排查 ModuleNotFoundError 等解释器不一致问题
print("[process_all_images] Python 解释器:", sys.executable, file=sys.stderr)
print("[process_all_images] 工作目录:", os.getcwd(), file=sys.stderr)
print("[process_all_images] sys.path[0]:", (sys.path[0] if sys.path else ""), file=sys.stderr)

try:
    from interaction.project_config import load_config_from_project_root
except ImportError:
    load_config_from_project_root = None

from post_processing.process_single_image import process_single_image

_DEFAULT_RUNS_DIR = _PROJECT_ROOT / "autoglm_runs"
IMAGE_LIST_CSV_NAME = "image_list.csv"
CSV_HEADERS = [
    "task_id", "step_number", "image_path", "status",
    "app_name", "page_name", "page_description", "differentiated_content",
    "error_message", "start_time", "end_time",
]
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def scan_tasks_steps(runs_dir: Path) -> list[dict]:
    """扫描 runs_dir 下 task_xxx/steps/*.png，生成待处理行（不读已有 csv）。"""
    rows = []
    for task_dir in sorted(runs_dir.iterdir()):
        if not task_dir.is_dir() or not task_dir.name.startswith("task_"):
            continue
        task_id = task_dir.name
        steps_dir = task_dir / "steps"
        if not steps_dir.is_dir():
            continue
        for png in sorted(steps_dir.glob("step_*.png")):
            step_num = png.stem.replace("step_", "")
            try:
                step_num_int = int(step_num)
            except ValueError:
                continue
            json_path = png.with_suffix(".json")
            if not json_path.is_file():
                continue
            # 存相对路径（相对 runs_dir），便于拷贝工程目录
            rows.append({
                "task_id": task_id,
                "step_number": step_num_int,
                "image_path": str(png.relative_to(runs_dir)),
                "status": STATUS_PENDING,
                "app_name": "",
                "page_name": "",
                "page_description": "",
                "differentiated_content": "",
                "error_message": "",
                "start_time": "",
                "end_time": "",
            })
    return rows


def _normalize_row(r: dict) -> None:
    """CSV 读入的列全是 str，将 step_number 转为 int；补全缺失列。"""
    for h in CSV_HEADERS:
        if h not in r:
            r[h] = ""
    if "step_number" in r and r["step_number"] != "":
        r["step_number"] = int(r["step_number"])


def load_or_build_csv(csv_path: Path, runs_dir: Path) -> list[dict]:
    """若 csv 存在则加载（保留已处理的 status/app_name/page_name 等）；否则扫描并创建。支持 UTF-8 与 GBK。"""
    if csv_path.is_file():
        existing = []
        for encoding in ("utf-8", "gbk"):
            try:
                with open(csv_path, "r", encoding=encoding, newline="") as f:
                    reader = csv.DictReader(f)
                    existing = list(reader)
                break
            except UnicodeDecodeError:
                existing = []
                continue
        if existing:
            for r in existing:
                _normalize_row(r)
            return existing
    rows = scan_tasks_steps(runs_dir)
    return rows


def save_csv(csv_path: Path, rows: list[dict]) -> None:
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(rows)


def process_one_row(
    rows: list[dict], index: int, csv_path: Path, runs_dir: Path, coze_config: dict | None = None
) -> bool:
    """处理一行：调用单图逻辑，更新该行并写回 csv 和 step json。coze_config 含 coze_api_token、workflow_id、upload_url。返回是否成功。"""
    r = rows[index]
    task_id = r["task_id"]
    step_number = r["step_number"]
    image_path_raw = r["image_path"]
    # 相对路径按 runs_dir 解析（CSV 中存的是相对路径）
    if not (str(image_path_raw).startswith("/") or (len(str(image_path_raw)) >= 2 and str(image_path_raw)[1] == ":")):
        image_path = str((runs_dir / image_path_raw).resolve())
    else:
        image_path = str(image_path_raw)
    step_json_path = Path(image_path).with_suffix(".json")

    if r["status"] == STATUS_DONE:
        return True

    rows[index]["status"] = STATUS_RUNNING
    rows[index]["start_time"] = rows[index]["start_time"] or datetime.now(tz=timezone.utc).isoformat()
    rows[index]["error_message"] = ""
    save_csv(csv_path, rows)

    try:
        app_name, page_name, page_description, differentiated_content = process_single_image(image_path, str(step_json_path), coze_config)
        rows[index]["status"] = STATUS_DONE
        rows[index]["app_name"] = app_name or ""
        rows[index]["page_name"] = page_name or ""
        rows[index]["page_description"] = page_description or ""
        rows[index]["differentiated_content"] = differentiated_content or ""
        rows[index]["end_time"] = datetime.now(tz=timezone.utc).isoformat()
        save_csv(csv_path, rows)
        return True
    except Exception as e:
        rows[index]["status"] = STATUS_FAILED
        rows[index]["error_message"] = str(e)[:500]
        rows[index]["end_time"] = datetime.now(tz=timezone.utc).isoformat()
        save_csv(csv_path, rows)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量处理截图并维护 image_list.csv，支持断点续跑")
    parser.add_argument("--runs-dir", type=Path, default=_DEFAULT_RUNS_DIR, help="工程根目录（含 project_config.json、image_list.csv）")
    parser.add_argument("--retry-failed", action="store_true", help="仅重试 status=failed 的行，不处理 pending/done，不影响已成功的")
    args = parser.parse_args()

    runs_dir = args.runs_dir.resolve()
    csv_path = runs_dir / IMAGE_LIST_CSV_NAME
    if not runs_dir.is_dir():
        print(f"Runs dir not found: {runs_dir}", file=sys.stderr)
        sys.exit(1)

    # 从工程目录 project_config.json 读取后处理配置（coze_api_token、workflow_id、upload_url）
    coze_config = None
    if load_config_from_project_root:
        try:
            cfg = load_config_from_project_root(runs_dir)
            post = cfg.get("post_processing") or {}
            coze_config = {
                "coze_api_token": post.get("coze_api_token") or "",
                "workflow_id": post.get("workflow_id") or "",
                "upload_url": post.get("upload_url") or "https://api.coze.cn/v1/files/upload",
            }
        except Exception:
            pass

    rows = load_or_build_csv(csv_path, runs_dir)
    if not rows:
        print("No step images found.", file=sys.stderr)
        return
    save_csv(csv_path, rows)

    if args.retry_failed:
        todo = [i for i, r in enumerate(rows) if r["status"] == STATUS_FAILED]
    else:
        todo = [i for i, r in enumerate(rows) if r["status"] in (STATUS_PENDING, STATUS_RUNNING)]

    # 关键进度打到 stderr，便于从网站启动时在控制台看到（子进程 stdout 可能被关闭）
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    _log(f"[后处理] 总行数: {len(rows)}, 待处理: {len(todo)}")
    if not todo:
        _log("[后处理] 无需处理：待处理行数为 0（全部已是 done，或未勾选「仅重试失败」时没有 pending/running 行）。")
        _log("[后处理] 若需重试失败行，请勾选「仅重试失败」后再次点击启动。")
        return
    if not (coze_config or {}).get("coze_api_token") or not (coze_config or {}).get("workflow_id"):
        _log("[后处理] 警告：未配置 coze_api_token 或 workflow_id，调用 Coze 会失败，行将标记为 failed。")

    for idx, i in enumerate(todo):
        r = rows[i]
        _log(f"[{idx + 1}/{len(todo)}] {r['task_id']} step_{r['step_number']:03d} ... ")
        ok = process_one_row(rows, i, csv_path, runs_dir, coze_config)
        _log("  -> ok" if ok else "  -> failed")
    _log("[后处理] Done.")


if __name__ == "__main__":
    main()
