"""
路径生成工作流调用：根据用户目标 + 路径简化 JSON 调用 Coze 工作流，返回是否支持及攻击序列。
使用与后处理相同的 Coze Token，工作流 ID 为配置中的 path_generation_workflow_id。

单脚本用法（在项目根目录）:
  python rollback_attack/call_path_generation_workflow.py --goal "我要用淘宝购买5双手套" --json-file path/path_20260204193106_simplify.json
  python rollback_attack/call_path_generation_workflow.py --goal "购买3顶帽子" --json "{\"path_summary\": [...]}"
  python rollback_attack/call_path_generation_workflow.py --goal "..." --json-file ... --project-root /path/to/project
"""
import json
import sys
from pathlib import Path

# 以脚本方式运行时保证项目根在 path 中
_root = Path(__file__).resolve().parent.parent
if _root not in sys.path:
    sys.path.insert(0, str(_root))

from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth, WorkflowEventType


def _extract_result_from_content(content: str) -> dict | None:
    """
    从工作流返回的文本中解析出 { supported, reason, attack_sequence }。
    支持：纯 JSON 字符串、或外层为 {"output": "..."} 且 output 为 JSON 字符串。
    """
    if not content or not content.strip():
        return None
    content = content.strip()
    try:
        obj = json.loads(content)
        if not isinstance(obj, dict):
            return None
        # 若外层有 output 且为字符串，再解析一层
        output = obj.get("output")
        if isinstance(output, str):
            try:
                obj = json.loads(output)
            except json.JSONDecodeError:
                return None
        elif output is not None and isinstance(output, dict):
            obj = output
        if not isinstance(obj, dict):
            return None
        supported = obj.get("supported")
        reason = obj.get("reason")
        attack_sequence = obj.get("attack_sequence")
        if "supported" not in obj:  # 必须存在
            return None
        if not isinstance(attack_sequence, list):
            attack_sequence = []
        return {
            "supported": bool(supported),
            "reason": (reason if isinstance(reason, str) else str(reason) if reason is not None else ""),
            "attack_sequence": [str(x) for x in attack_sequence],
        }
    except json.JSONDecodeError:
        pass
    return None


def run_path_generation_workflow(
    goal: str,
    path_json_str: str,
    config: dict,
) -> dict:
    """
    调用路径生成工作流。
    config 需含 coze_api_token、path_generation_workflow_id（或 workflow_id 作为 fallback）。
    工作流参数：input = 用户目标文本，json = 路径简化 JSON 字符串。
    返回 { "supported": bool, "reason": str, "attack_sequence": list[str] }。
    """
    token = (config or {}).get("coze_api_token") or ""
    workflow_id = (config or {}).get("path_generation_workflow_id") or (config or {}).get("workflow_id") or ""
    if not token or not workflow_id:
        raise ValueError("config must contain coze_api_token and path_generation_workflow_id (or workflow_id)")

    coze = Coze(auth=TokenAuth(token=token), base_url=COZE_CN_BASE_URL)
    stream = coze.workflows.runs.stream(
        workflow_id=workflow_id,
        parameters={
            "input": goal,
            "json": path_json_str,
        },
    )

    result = None
    all_contents: list[str] = []
    for event in stream:
        if event.event == WorkflowEventType.MESSAGE and getattr(event, "message", None):
            msg = event.message
            content = getattr(msg, "content", None) or getattr(msg, "data", None)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if content:
                all_contents.append(content)
                result = _extract_result_from_content(content)
                if result is not None:
                    break
        elif event.event == WorkflowEventType.ERROR:
            err = getattr(event, "error", None) or str(event)
            raise RuntimeError(f"Workflow error: {err}")

    if result is None:
        print("[workflow] Failed to parse result. Raw output ({} chunk(s)):".format(len(all_contents)), file=sys.stderr)
        for i, c in enumerate(all_contents):
            print("[workflow] --- chunk {} ---".format(i + 1), file=sys.stderr)
            try:
                print(c if len(c) <= 2000 else c[:2000] + "\n... (truncated, total {} chars)".format(len(c)), file=sys.stderr)
            except Exception:
                print(repr(c)[:2000], file=sys.stderr)
            print("", file=sys.stderr)
        if not all_contents:
            print("[workflow] No MESSAGE content received.", file=sys.stderr)
        raise ValueError("Workflow response could not be parsed to { supported, reason, attack_sequence }.")
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Call path generation workflow: goal + path simplify JSON -> supported + attack_sequence",
    )
    parser.add_argument("--goal", "-g", required=True, help="User attack goal, e.g. '我要用淘宝购买5双手套'")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Path simplify JSON as string")
    group.add_argument("--json-file", "-f", help="Path to path_*_simplify.json file")
    parser.add_argument(
        "--project-root",
        "-r",
        default=None,
        help="Project root to load project_config.json (coze_api_token, path_generation_workflow_id). Default: parent of rollback_attack.",
    )
    parser.add_argument("--token", help="Coze API token (overrides config)")
    parser.add_argument("--workflow-id", help="Path generation workflow ID (overrides config)")
    args = parser.parse_args()

    if args.json_file:
        path = Path(args.json_file)
        if not path.is_file():
            raise SystemExit("JSON file not found: {}".format(args.json_file))
        path_json_str = path.read_text(encoding="utf-8")
    else:
        path_json_str = args.json

    config = {}
    project_root = Path(args.project_root) if args.project_root else _root
    try:
        from interaction.project_config import load_config_from_project_root
        cfg = load_config_from_project_root(project_root)
        post = cfg.get("post_processing") or {}
        config["coze_api_token"] = post.get("coze_api_token") or ""
        config["path_generation_workflow_id"] = post.get("path_generation_workflow_id") or post.get("workflow_id") or ""
    except Exception as e:
        print("Warning: could not load project config: {}".format(e), file=sys.stderr)

    if args.token is not None:
        config["coze_api_token"] = args.token
    if args.workflow_id is not None:
        config["path_generation_workflow_id"] = args.workflow_id

    result = run_path_generation_workflow(args.goal, path_json_str, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
