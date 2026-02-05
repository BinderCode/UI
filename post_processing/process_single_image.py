"""
单图片处理：上传到 Coze -> 调用工作流 -> 将 app_name/page_name/page_description/differentiated_content 写回 step_xxx.json
"""
import json
import os
import re
import sys
import requests
from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth, Stream, WorkflowEvent, WorkflowEventType

_DEFAULT_UPLOAD_URL = "https://api.coze.cn/v1/files/upload"


def upload_image(image_path: str, config: dict) -> str:
    """上传图片到 Coze，返回 file_id。config 含 coze_api_token、upload_url。"""
    token = (config or {}).get("coze_api_token") or ""
    url = (config or {}).get("upload_url") or _DEFAULT_UPLOAD_URL
    with open(image_path, "rb") as f:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (os.path.basename(image_path), f)},
            timeout=60,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Upload failed: {data}")
    return data["data"]["id"]


def _extract_output_from_content(content: str) -> dict | None:
    """从 MESSAGE 的 content 解析 output（含 app_name, page_name, page_description, differentiated_content，四者必填且非空）。
    支持 output 为 dict 或为 JSON 字符串（工作流可能返回已转义的字符串）。"""
    if not content or not content.strip():
        return None
    try:
        obj = json.loads(content)
        output = obj.get("output") if isinstance(obj, dict) else None
        if output is None:
            return None
        # 工作流可能把 output 作为 JSON 字符串返回，需再解析一次
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return None
        if not isinstance(output, dict):
            return None
        app = (output.get("app_name") or "").strip()
        page = (output.get("page_name") or "").strip()
        desc = (output.get("page_description") or "").strip()
        diff = (output.get("differentiated_content") or "").strip()
        if not app or not page or not desc or not diff:
            return None
        return output
    except json.JSONDecodeError:
        pass
    return None


def run_workflow(file_id: str, config: dict) -> dict:
    """调用工作流，返回 output 字典（含 app_name, page_name, page_description, differentiated_content，四者必填）。config 含 coze_api_token、workflow_id。"""
    token = (config or {}).get("coze_api_token") or ""
    workflow_id = (config or {}).get("workflow_id") or ""
    coze = Coze(auth=TokenAuth(token=token), base_url=COZE_CN_BASE_URL)
    stream = coze.workflows.runs.stream(
        workflow_id=workflow_id,
        parameters={
            "input": json.dumps({"file_id": file_id}),
            "image": json.dumps({"file_id": file_id}),
        },
    )
    output = None
    all_contents: list[str] = []
    for event in stream:
        if event.event == WorkflowEventType.MESSAGE and getattr(event, "message", None):
            msg = event.message
            content = getattr(msg, "content", None) or getattr(msg, "data", None)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if content:
                all_contents.append(content)
                output = _extract_output_from_content(content)
                if output:
                    break
        elif event.event == WorkflowEventType.ERROR:
            err = getattr(event, "error", None) or str(event)
            raise RuntimeError(f"Workflow error: {err}")
    if not output:
        # 解析失败时把工作流原始输出全部打到 stderr，便于排查
        print("[workflow] 解析失败，工作流原始输出如下（共 {} 条）:".format(len(all_contents)), file=sys.stderr)
        for i, c in enumerate(all_contents):
            print("[workflow] --- 第 {} 条 ---".format(i + 1), file=sys.stderr)
            try:
                print(c if len(c) <= 2000 else c[:2000] + "\n... (截断，共 {} 字符)".format(len(c)), file=sys.stderr)
            except Exception:
                print(repr(c)[:2000], file=sys.stderr)
            print("", file=sys.stderr)
        if not all_contents:
            print("[workflow] 未收到任何 MESSAGE 内容。", file=sys.stderr)
        raise ValueError(
            "Workflow must return app_name, page_name, page_description and differentiated_content (all non-empty)."
        )
    return output


def update_step_json(
    step_json_path: str,
    app_name: str,
    page_name: str,
    page_description: str,
    differentiated_content: str,
) -> None:
    """把 app_name / page_name / page_description / differentiated_content 写入 step_xxx.json（保留其余字段）。"""
    with open(step_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["app_name"] = app_name or ""
    data["page_name"] = page_name or ""
    data["page_description"] = page_description or ""
    data["differentiated_content"] = differentiated_content or ""
    with open(step_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def process_single_image(image_path: str, step_json_path: str, config: dict | None = None) -> tuple[str, str, str, str]:
    """
    处理一张图：上传 -> 工作流 -> 写 step json。
    config 含 coze_api_token、workflow_id、upload_url，通常由 process_all_images 从工程目录 project_config.json 读取后传入。
    返回 (app_name, page_name, page_description, differentiated_content)。
    """
    cfg = config or {}
    file_id = upload_image(image_path, cfg)
    output = run_workflow(file_id, cfg)
    app_name = output.get("app_name", "") or ""
    page_name = output.get("page_name", "") or ""
    page_description = output.get("page_description", "") or ""
    differentiated_content = output.get("differentiated_content", "") or ""
    update_step_json(step_json_path, app_name, page_name, page_description, differentiated_content)
    return app_name, page_name, page_description, differentiated_content


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path", help="截图路径，如 .../task_0001/steps/step_001.png")
    parser.add_argument("--step-json", help="对应 step_xxx.json 路径；不填则根据 image_path 推断")
    args = parser.parse_args()
    image_path = os.path.abspath(args.image_path)
    step_json = args.step_json
    if not step_json:
        step_json = re.sub(r"\.png$", ".json", image_path, flags=re.IGNORECASE)
    if not os.path.isfile(image_path):
        raise SystemExit(f"Image not found: {image_path}")
    if not os.path.isfile(step_json):
        raise SystemExit(f"Step json not found: {step_json}")
    # 单独运行时从 image_path 推断工程根并读取 project_config.json
    config = None
    try:
        _root = Path(__file__).resolve().parent.parent
        if str(_root) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(_root))
        from interaction.project_config import load_config_from_project_root
        runs_dir = Path(image_path).resolve().parent.parent.parent
        cfg = load_config_from_project_root(runs_dir)
        post = cfg.get("post_processing") or {}
        config = {
            "coze_api_token": post.get("coze_api_token") or "",
            "workflow_id": post.get("workflow_id") or "",
            "upload_url": post.get("upload_url") or _DEFAULT_UPLOAD_URL,
        }
    except Exception:
        pass
    app_name, page_name, page_description, differentiated_content = process_single_image(image_path, step_json, config)
    print(
        json.dumps(
            {"app_name": app_name, "page_name": page_name, "page_description": page_description, "differentiated_content": differentiated_content},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
