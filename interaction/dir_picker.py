# 本地目录选择：用系统对话框选目录，返回绝对路径；无 GUI 时返回 None 并设 error。

from pathlib import Path
from typing import Optional


def pick_directory(initial_dir: Optional[Path] = None) -> tuple[Optional[str], Optional[str]]:
    """
    弹出系统目录选择对话框，返回 (选中路径, 错误信息)。
    若用户取消或环境无 GUI，返回 (None, error_message)。
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None, "当前环境不支持图形界面（无 tkinter）"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial = str(initial_dir) if initial_dir and initial_dir.is_dir() else None
    path = filedialog.askdirectory(parent=root, title="选择工程目录", initialdir=initial)
    root.destroy()
    if path and path.strip():
        return path.strip(), None
    return None, "未选择目录" if path is not None else "已取消"
