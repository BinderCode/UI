# 后处理 API 逻辑：读取/生成 image_list.csv，启动批量处理。
# 供 app.py 调用。

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parent.parent

IMAGE_LIST_CSV = "image_list.csv"

_post_run_state: dict[str, Any] = {"running": False}
_post_process: subprocess.Popen | None = None


def _on_post_finished(process: subprocess.Popen) -> None:
    process.wait()
    global _post_run_state, _post_process
    _post_run_state["running"] = False
    _post_process = None


def load_image_list(project_root: Path) -> list[dict[str, Any]]:
    """从工程根目录加载或生成 image_list.csv，返回行列表（每行含 task_id, step_number, status 等）。"""
    project_root = project_root.resolve()
    csv_path = project_root / IMAGE_LIST_CSV
    try:
        from post_processing.process_all_images import load_or_build_csv
        rows = load_or_build_csv(csv_path, project_root)
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_post_list_response(project_root: Path) -> dict[str, Any]:
    """返回 GET /api/post-processing/list 所需结构：rows, total, done_count, failed_count, pending_count, running_count, run_state。"""
    rows = load_image_list(project_root)
    total = len(rows)
    done_count = sum(1 for r in rows if (r.get("status") or "").lower() == "done")
    failed_count = sum(1 for r in rows if (r.get("status") or "").lower() == "failed")
    pending_count = sum(1 for r in rows if (r.get("status") or "").lower() == "pending")
    running_count = sum(1 for r in rows if (r.get("status") or "").lower() == "running")
    current = next((r for r in rows if (r.get("status") or "").lower() == "running"), None)
    return {
        "rows": rows,
        "total": total,
        "done_count": done_count,
        "failed_count": failed_count,
        "pending_count": pending_count,
        "running_count": running_count,
        "current_row": current,
        "run_state": {"running": _post_run_state.get("running", False)},
    }


def start_post_processing(project_root: Path, retry_failed: bool = False) -> tuple[bool, str]:
    """后台启动批量后处理：调用 process_all_images.py。返回 (成功, 消息)。"""
    global _post_run_state, _post_process
    if _post_run_state.get("running"):
        return False, "已有后处理在运行中"
    project_root = project_root.resolve()
    script = _APP_DIR / "post_processing" / "process_all_images.py"
    if not script.is_file():
        return False, "未找到 process_all_images.py"
    cmd = [
        sys.executable,
        str(script),
        "--runs-dir", str(project_root),
    ]
    if retry_failed:
        cmd.append("--retry-failed")
    # 启动前输出运行环境，便于排查子进程与当前解释器不一致的问题
    print(
        "[后处理] 即将用以下解释器启动子进程:",
        sys.executable,
        file=sys.stderr,
    )
    print("[后处理] 完整命令:", cmd, file=sys.stderr)
    try:
        _post_process = subprocess.Popen(
            cmd,
            cwd=str(_APP_DIR),
            stdout=None,  # 继承 stdout/stderr，便于在控制台看到后处理进度与报错
            stderr=None,
        )
        _post_run_state["running"] = True
        t = threading.Thread(target=_on_post_finished, args=(_post_process,), daemon=True)
        t.start()
        return True, "已启动后处理"
    except Exception as e:
        return False, str(e)
