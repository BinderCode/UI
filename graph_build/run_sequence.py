"""
按给定执行序列依次执行每一步操作，每步间隔可配（默认 2s）。
直接使用 AutoGLM 的 PhoneAgent，不依赖 android_world（支持真实设备）。
在项目根目录运行：python graph_build/run_sequence.py <sequence.json> [--delay 2]

sequence.json 为 JSON 数组，每项为一步的 action：
  - 若为对象且含 "action" 键（等同 step_xxx.json 一步），则使用该项的 action 字段；
  - 否则该项视为 action 对象本身。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if _root not in sys.path:
    sys.path.insert(0, str(_root))

# AutoGLM 需在其路径下
_autoglm_path = _root / "AutoGLM"
if _autoglm_path.is_dir() and str(_autoglm_path) not in sys.path:
    sys.path.insert(0, str(_autoglm_path))

from phone_agent.actions.handler import ActionHandler


def _find_adb() -> str | None:
    """查找 adb 可执行文件路径。"""
    # 首先尝试使用系统 PATH 中的 adb
    adb_in_path = shutil.which("adb")
    if adb_in_path:
        return adb_in_path
    
    # 然后尝试常见路径
    cands = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        os.path.expanduser("~/Library/Android/sdk"),
        os.path.expanduser("~/Android/Sdk"),
    ]
    if os.name == "nt":
        cands.extend([
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
            os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Android", "Sdk"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Android", "android-sdk"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Android", "android-sdk"),
        ])
    
    for base in cands:
        if not base:
            continue
        adb = os.path.join(base, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
        if os.path.isfile(adb):
            return adb
    
    return None


def _get_device_id(adb_path: str) -> str | None:
    """获取第一个连接的设备 ID。"""
    try:
        result = subprocess.run(
            [adb_path, "devices"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().split('\n')[1:]  # 跳过标题行
        for line in lines:
            if line.strip() and '\tdevice' in line:
                device_id = line.split('\t')[0].strip()
                return device_id
    except Exception:
        pass
    return None


def _get_display_size(adb_path: str, device_id: str | None = None) -> tuple[int, int]:
    """通过 adb 获取屏幕尺寸。"""
    args = [adb_path]
    if device_id:
        args.extend(['-s', device_id])
    args.extend(['shell', 'wm', 'size'])
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=5, encoding='utf-8', errors='ignore'
        )
        out = (result.stdout or '') + (result.stderr or '')
        # "Physical size: 1080x2400" or "Override size: 1080x2400"
        match = re.search(r'(\d+)\s*x\s*(\d+)', out)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 1080, 2400  # 默认值


def main() -> None:
    parser = argparse.ArgumentParser(description="按执行序列逐步执行 action，每步间隔 delay 秒。")
    parser.add_argument("sequence", type=Path, help="JSON 文件：action 对象数组")
    parser.add_argument("--delay", type=float, default=2.0, help="每步间隔秒数，默认 2")
    parser.add_argument("--adb-path", type=Path, default=None, help="adb 可执行文件路径（可选）")
    parser.add_argument("--device-id", type=str, default=None, help="设备 ID（可选，默认使用第一个连接的设备）")
    args = parser.parse_args()

    path = args.sequence
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        print("JSON 需为数组", file=sys.stderr)
        sys.exit(1)
    sequence = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            print(f"第 {i+1} 项非对象，已跳过", file=sys.stderr)
            continue
        
        # 处理 action 格式：
        # 1. 如果 item 有 "action" 键且值是字典，使用 item["action"]（step_xxx.json 格式）
        # 2. 如果 item 有 "action" 键但值是字符串，整个 item 就是 action（直接 action 对象）
        # 3. 否则，整个 item 就是 action
        if "action" in item:
            action_value = item.get("action")
            if isinstance(action_value, dict):
                # step_xxx.json 格式：{"action": {...}}
                action = action_value
            else:
                # 直接 action 对象格式：{"action": "Tap", "element": [...]}
                action = item
        else:
            # 没有 "action" 键，整个 item 就是 action
            action = item
        
        if isinstance(action, dict) and len(action) > 0:
            sequence.append(action)
        else:
            print(f"第 {i+1} 项无有效 action，已跳过: {action}", file=sys.stderr)
    if not sequence:
        print("没有可执行的 action", file=sys.stderr)
        sys.exit(1)

    # 与跑任务一致：默认用命令名 "adb"（由 PATH 解析），不解析、不打印完整路径，避免路径含不可见字符时编码报错
    if args.adb_path:
        adb_cmd = str(args.adb_path)
        if not os.path.isfile(adb_cmd):
            print(f"错误: 指定的 adb 路径不存在: {adb_cmd}", file=sys.stderr)
            sys.exit(1)
        print(f"使用 adb: (指定路径)", flush=True)
    else:
        if not shutil.which("adb"):
            print("错误: 找不到 adb，请确保 adb 在系统 PATH 中，或使用 --adb-path 指定路径。", file=sys.stderr)
            sys.exit(1)
        adb_cmd = "adb"
        print("使用 adb: (来自 PATH)", flush=True)
    
    # 检查设备连接并获取设备 ID
    print("检查设备连接...", flush=True)
    device_id = args.device_id
    if not device_id:
        device_id = _get_device_id(adb_cmd)
        if not device_id:
            print("[ERROR] 错误: 未检测到连接的设备", file=sys.stderr, flush=True)
            print("请确保设备已连接并启用 USB 调试", file=sys.stderr, flush=True)
            sys.exit(1)
    
    print(f"使用设备: {device_id}", flush=True)
    
    # 获取屏幕尺寸
    print("获取屏幕尺寸...", flush=True)
    screen_width, screen_height = _get_display_size(adb_cmd, device_id)
    print(f"屏幕尺寸: {screen_width}x{screen_height}", flush=True)
    
    # 初始化 ActionHandler（直接使用，不需要 android_world）
    print("正在初始化 ActionHandler...", flush=True)
    try:
        action_handler = ActionHandler(device_id=device_id)
        print("ActionHandler 初始化完成", flush=True)
    except Exception as e:
        print(f"ActionHandler 初始化失败: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    delay = max(0.0, args.delay)
    n = len(sequence)
    print(f"\n开始执行重放序列，共 {n} 个步骤...", flush=True)
    print(f"使用设备: {device_id}", flush=True)
    print(f"屏幕尺寸: {screen_width}x{screen_height}", flush=True)
    
    for i, action in enumerate(sequence):
        action_type = action.get('action', 'unknown')
        print(f"\n[{i+1}/{n}] 执行 action: {action_type}", flush=True)
        print(f"  完整 action: {action}", flush=True)
        
        # 验证 action 格式
        if not isinstance(action, dict):
            print(f"  [ERROR] 错误: action 不是字典类型", file=sys.stderr, flush=True)
            continue
        
        if action.get("_metadata") != "do":
            print(f"  [WARN] 警告: _metadata 不是 'do': {action.get('_metadata')}", flush=True)
        
        try:
            print(f"  调用 action_handler.execute...", flush=True)
            result = action_handler.execute(action, screen_width, screen_height)
            print(f"  [OK] 执行成功", flush=True)
            if result:
                print(f"  返回结果: success={result.success}, should_finish={result.should_finish}", flush=True)
                if result.message:
                    print(f"    message: {result.message}", flush=True)
                if result.requires_confirmation:
                    print(f"    [WARN] 需要确认", flush=True)
        except Exception as e:
            print(f"  [ERROR] 执行失败: {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            # 继续执行下一个 action，不中断
        
        if i < n - 1 and delay > 0:
            print(f"  等待 {delay} 秒...", flush=True)
            time.sleep(delay)

    print("执行结束.", flush=True)


if __name__ == "__main__":
    main()
