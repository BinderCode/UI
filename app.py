# 本地网站主程序：工程配置 + 顶部四页（环境信息、任务收集、后处理、回滚攻击）
# 配置由 interaction 的 ProjectConfigManager 统一管理，app 只做 HTTP 转发。

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from interaction.project_config import get_config_manager
from interaction.dir_picker import pick_directory
from interaction import tasks_api
from interaction import post_processing_api
from interaction import rollback_api
from rollback_attack.call_path_generation_workflow import run_path_generation_workflow

_config_manager = get_config_manager()
# 默认工程根目录 = 程序所在目录下的 autoglm_runs，所有工程数据均在此目录
_config_manager.set_project_root(_APP_DIR / "autoglm_runs")
_INDEX_HTML_PATH = _APP_DIR / "interaction" / "index.html"


def _resolve_root(data_mode: str) -> tuple[Path | None, str | None]:
    """根据 data_mode 解析工程目录。返回 (root, None) 或 (None, error_msg)。"""
    if data_mode == "incremental":
        root = _config_manager.get_update_data_directory()
        if not root:
            return None, "未设置或无效的增量数据目录，请在环境信息中填写 Update Data Directory。"
    else:
        root = _config_manager.get_project_root()
        if not root:
            return None, "未设置工程目录，请在环境信息中选择 Project Directory。"
    return Path(root), None


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body_json(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return None
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_GET(self) -> None:
        path = (urlparse(self.path).path or "/").rstrip("/") or "/"

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_INDEX_HTML_PATH.read_text(encoding="utf-8").encode("utf-8"))
            return

        if path == "/api/config":
            root = _config_manager.get_project_root()
            self._send_json({
                "project_root": str(root) if root else None,
                "config": _config_manager.get_config(),
            })
            return

        if path == "/api/pick-directory":
            root = _config_manager.get_project_root()
            chosen, err = pick_directory(root)
            if err:
                self._send_json({"ok": False, "error": err})
            else:
                self._send_json({"ok": True, "path": chosen})
            return

        if path == "/api/task-list":
            q = parse_qs(urlparse(self.path).query)
            data_mode = (q.get("data_mode") or ["base"])[0]
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            mode = (q.get("mode") or ["androidworld"])[0]
            if mode not in ("androidworld", "custom"):
                mode = "androidworld"
            out = tasks_api.get_task_list_response(root, mode)
            out["run_state"] = tasks_api.get_run_state()
            self._send_json(out)
            return

        if path == "/api/post-processing/list":
            q = parse_qs(urlparse(self.path).query)
            data_mode = (q.get("data_mode") or ["base"])[0]
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            out = post_processing_api.get_post_list_response(root)
            self._send_json(out)
            return

        if path == "/api/post-processing/image":
            q = parse_qs(urlparse(self.path).query)
            data_mode = (q.get("data_mode") or ["base"])[0]
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            task_id = (q.get("task_id") or [""])[0].strip()
            step_s = (q.get("step") or [""])[0].strip()
            if not task_id or not step_s or not task_id.startswith("task_"):
                self.send_response(400)
                self.end_headers()
                return
            try:
                step_num = int(step_s)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            import re
            if not re.match(r"^task_\d+$", task_id):
                self.send_response(400)
                self.end_headers()
                return
            img_path = Path(root) / task_id / "steps" / f"step_{step_num:03d}.png"
            if not img_path.is_file():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(img_path.read_bytes())
            return

        if path == "/api/rollback/graph":
            q = parse_qs(urlparse(self.path).query)
            data_mode = (q.get("data_mode") or ["base"])[0]
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"error": err, "graphList": [], "mergedNodes": [], "mergedEdges": [], "totalCount": 0}, 400)
                return
            variant = (q.get("variant") or ["unmerged"])[0]
            if variant not in ("unmerged", "merged"):
                variant = "unmerged"
            index = (q.get("index") or ["all"])[0]
            out = rollback_api.get_graph_collection_response(root, variant=variant, index=index)
            if out.get("error") and not out.get("graphList"):
                self._send_json(out, 400)
            else:
                self._send_json(out)
            return

        if path == "/api/rollback/stats":
            q = parse_qs(urlparse(self.path).query)
            data_mode = (q.get("data_mode") or ["base"])[0]
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            result = rollback_api.get_graph_stats(root)
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")

        if path == "/api/config":
            if _config_manager.get_project_root() is None:
                self._send_json(
                    {"ok": False, "error": "请先在「环境信息」页设置项目根目录"},
                    400,
                )
                return
            body = self._read_body_json()
            if body is None:
                self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
                return
            try:
                _config_manager.update_config(body)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/project":
            body = self._read_body_json()
            if not body or "project_root" not in body:
                self._send_json({"ok": False, "error": "缺少 project_root"}, 400)
                return
            raw = body.get("project_root")
            if not isinstance(raw, str) or not raw.strip():
                self._send_json({"ok": False, "error": "project_root 不能为空"}, 400)
                return
            try:
                _config_manager.set_project_root(raw.strip())
                self._send_json({"ok": True})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/tasks/create-androidworld":
            body = self._read_body_json() or {}
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            config = _config_manager.get_config()
            api_key = (config.get("autoglm") or {}).get("api_key") or ""
            base_url = (config.get("autoglm") or {}).get("base_url") or ""
            tasks = body.get("tasks")
            if isinstance(tasks, list):
                tasks = [str(t) for t in tasks]
            else:
                tasks = None
            n = int(body.get("n_task_combinations", 1))
            seed = int(body.get("task_random_seed", 30))
            ok, msg = tasks_api.create_androidworld_list(
                root, api_key, base_url, tasks=tasks, n_task_combinations=n, task_random_seed=seed
            )
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 400)
            return

        if path == "/api/tasks/import-custom-csv":
            body = self._read_body_json()
            if not body:
                self._send_json({"ok": False, "error": "请上传 CSV 内容"}, 400)
                return
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            csv_text = body.get("csv_text")
            if csv_text is not None:
                csv_content = csv_text.encode("utf-8")
            else:
                import base64
                b64 = body.get("csv_base64")
                if not b64:
                    self._send_json({"ok": False, "error": "需要 csv_text 或 csv_base64"}, 400)
                    return
                try:
                    csv_content = base64.b64decode(b64)
                except Exception as e:
                    self._send_json({"ok": False, "error": "csv_base64 解码失败: " + str(e)}, 400)
                    return
            ok, msg = tasks_api.save_custom_task_list(root, csv_content)
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 400)
            return

        if path == "/api/tasks/start":
            body = self._read_body_json() or {}
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            mode = (body.get("mode") or "androidworld").strip().lower()
            if mode not in ("androidworld", "custom"):
                self._send_json({"ok": False, "error": "mode 须为 androidworld 或 custom"}, 400)
                return
            config = _config_manager.get_config()
            ok, msg = tasks_api.start_run(root, mode, config)
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 400)
            return

        if path == "/api/post-processing/start":
            body = self._read_body_json() or {}
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            retry_failed = bool(body.get("retry_failed"))
            ok, msg = post_processing_api.start_post_processing(root, retry_failed=retry_failed)
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 400)
            return

        if path == "/api/rollback/generate":
            body = self._read_body_json() or {}
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            result = rollback_api.generate_graph_collection(root)
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        if path == "/api/rollback/merge":
            body = self._read_body_json() or {}
            data_mode = (body.get("data_mode") or "base").strip() or "base"
            root, err = _resolve_root(data_mode)
            if err:
                self._send_json({"ok": False, "error": err}, 400)
                return
            merge_threshold = float(body.get("merge_threshold", 0.9))
            if not (0 < merge_threshold <= 1):
                self._send_json({"ok": False, "error": "merge_threshold 须在 (0, 1] 之间"}, 400)
                return
            result = rollback_api.merge_graph_collection_api(root, merge_threshold=merge_threshold)
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        if path == "/api/rollback/incremental-merge":
            base_root = _config_manager.get_project_root()
            if not base_root:
                self._send_json({"ok": False, "error": "未设置工程目录"}, 400)
                return
            incr_root = _config_manager.get_update_data_directory()
            if not incr_root:
                self._send_json({"ok": False, "error": "未设置或无效的增量数据目录"}, 400)
                return
            body = self._read_body_json() or {}
            merge_threshold = float(body.get("merge_threshold", 0.9))
            if not (0 < merge_threshold <= 1):
                self._send_json({"ok": False, "error": "merge_threshold 须在 (0, 1] 之间"}, 400)
                return
            result = rollback_api.incremental_merge_graph_collection_api(
                Path(base_root), Path(incr_root), merge_threshold=merge_threshold
            )
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        if path == "/api/rollback/replay":
            body = self._read_body_json() or {}
            sequence = body.get("sequence", [])
            delay = float(body.get("delay", 2.0))
            if not isinstance(sequence, list):
                self._send_json({"success": False, "error": "sequence 须为数组"}, 400)
                return
            result = rollback_api.execute_replay_sequence(sequence, delay)
            if result.get("success"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        if path == "/api/rollback/save-path":
            root = _config_manager.get_project_root()
            if not root:
                self._send_json({"ok": False, "error": "未设置工程目录"}, 400)
                return
            body = self._read_body_json()
            if not body:
                self._send_json({"ok": False, "error": "请提供路径数据"}, 400)
                return
            result = rollback_api.save_path_data(Path(root), body)
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 400)
            return

        if path == "/api/rollback/path-generation":
            root = _config_manager.get_project_root()
            if not root:
                self._send_json({"ok": False, "error": "未设置工程目录"}, 400)
                return
            body = self._read_body_json()
            if not body:
                self._send_json({"ok": False, "error": "请提供 goal 与 json_content"}, 400)
                return
            goal = (body.get("goal") or "").strip()
            json_content = body.get("json_content")
            if isinstance(json_content, dict):
                import json as _json
                json_content = _json.dumps(json_content, ensure_ascii=False)
            json_content = (json_content or "").strip()
            if not goal:
                self._send_json({"ok": False, "error": "goal 不能为空"}, 400)
                return
            if not json_content:
                self._send_json({"ok": False, "error": "json_content 不能为空"}, 400)
                return
            config = _config_manager.get_config()
            post = (config.get("post_processing") or {})
            workflow_config = {
                "coze_api_token": post.get("coze_api_token") or "",
                "path_generation_workflow_id": post.get("path_generation_workflow_id") or post.get("workflow_id") or "",
            }
            try:
                result = run_path_generation_workflow(goal, json_content, workflow_config)
                self._send_json({"ok": True, "result": result})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/rollback/analyze-paths":
            root = _config_manager.get_project_root()
            if not root:
                self._send_json({"ok": False, "error": "未设置工程目录"}, 400)
                return
            try:
                result = rollback_api.analyze_complete_task_paths(root)
                if result.get("ok"):
                    self._send_json(result)
                else:
                    self._send_json(result, 400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass


def main() -> None:
    server = HTTPServer(("127.0.0.1", 8765), _Handler)
    print("本地网站已启动: http://127.0.0.1:8765/")
    server.serve_forever()


if __name__ == "__main__":
    main()
