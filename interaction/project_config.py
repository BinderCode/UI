# 工程配置：单一对象管理当前工程根目录 + 配置，更新即自动保存；只对外暴露接口，不暴露 json 结构。

import json
import sys
from pathlib import Path

CONFIG_FILENAME = "project_config.json"

# 工程根目录即任务数据目录，所有工程数据（project_config.json、task_list.csv、task_xxx/ 等）均在此目录下
# update_data_directory：增量数据工程目录路径，仅保存在基础工程的 project_config.json 中
DEFAULT_CONFIG = {
    "autoglm": {"api_key": "", "base_url": ""},
    "post_processing": {
        "workflow_key": "",
        "workflow_id": "",
        "path_generation_workflow_id": "",
        "coze_api_token": "",
        "upload_url": "https://api.coze.cn/v1/files/upload",
    },
    "update_data_directory": "",
}


def _default_config() -> dict:
    return dict(DEFAULT_CONFIG)


def load_config_from_project_root(project_root: str | Path) -> dict:
    """
    从工程根目录读取 project_config.json，与 DEFAULT_CONFIG 合并后返回。
    供脚本（run_androidworld_task、run_custom_task、process_all_images）使用，不依赖单例。
    """
    return _load_config_from_path(Path(project_root).resolve())


def _load_config_from_path(project_root: Path) -> dict:
    path = project_root / CONFIG_FILENAME
    if not path.is_file():
        return _default_config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = _default_config()
        for key in out:
            if key in data and isinstance(data[key], dict) and isinstance(out[key], dict):
                for k in out[key]:
                    if k in data[key]:
                        out[key][k] = data[key][k]
            elif key in data:
                out[key] = data[key]
        return out
    except (json.JSONDecodeError, OSError):
        return _default_config()


def _sync_incremental_config(project_root: Path, base_config: dict) -> None:
    """
    若 base_config 中配置了 update_data_directory 且与 project_root 不同，
    则在该目录下创建或更新 project_config.json，并将 API/Workflow 等字段与基础工程保持一致。
    """
    raw = base_config.get("update_data_directory") or ""
    s = str(raw).strip()
    if not s:
        return
    incremental_root = Path(s).resolve()
    if incremental_root == project_root:
        return
    incremental_root.mkdir(parents=True, exist_ok=True)
    incr_config_path = incremental_root / CONFIG_FILENAME
    if incr_config_path.is_file():
        incr_cfg = _load_config_from_path(incremental_root)
    else:
        incr_cfg = _default_config()
    # 从基础工程同步：AutoGLM、Post Processing 相关字段
    incr_cfg.setdefault("autoglm", {}).update({
        "api_key": base_config.get("autoglm", {}).get("api_key", ""),
        "base_url": base_config.get("autoglm", {}).get("base_url", ""),
    })
    incr_cfg.setdefault("post_processing", {}).update({
        "coze_api_token": base_config.get("post_processing", {}).get("coze_api_token", ""),
        "workflow_id": base_config.get("post_processing", {}).get("workflow_id", ""),
        "path_generation_workflow_id": base_config.get("post_processing", {}).get("path_generation_workflow_id", ""),
    })
    try:
        with open(incr_config_path, "w", encoding="utf-8") as f:
            json.dump(incr_cfg, f, ensure_ascii=False, indent=2)
    except (OSError, TypeError, ValueError) as e:
        print(f"Warning: Failed to save incremental config to {incr_config_path}: {e}", file=sys.stderr)


class ProjectConfigManager:
    """
    工程配置管理器：持有当前工程根目录与配置，选根目录后即可用，更新即自动保存。
    """

    def __init__(self) -> None:
        self._project_root: Path | None = None
        self._config: dict = _default_config()

    def set_project_root(self, path: str | Path) -> None:
        """
        设置当前工程根目录。若目录不存在则创建；若该目录下无 project_config.json 则新建默认配置并保存。
        """
        p = Path(path).resolve()
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        if not p.is_dir():
            raise ValueError("路径不是目录")
        self._project_root = p
        config_path = p / CONFIG_FILENAME
        if not config_path.is_file():
            self._config = _default_config()
            self._save()
        else:
            self._config = self._load(p)

    def get_project_root(self) -> Path | None:
        """当前工程根目录，未设置时为 None。"""
        return self._project_root

    def get_update_data_directory(self) -> Path | None:
        """增量数据工程目录（从配置读取）；未设置或路径无效时为 None。"""
        raw = self._config.get("update_data_directory") or ""
        s = str(raw).strip()
        if not s:
            return None
        p = Path(s).resolve()
        return p if p.is_dir() else None

    def get_config(self) -> dict:
        """当前配置；未设置工程根目录时返回默认配置副本。"""
        if self._project_root is None:
            return _default_config()
        return dict(self._config)

    def update_config(self, partial: dict) -> None:
        """
        用 partial 合并更新当前配置并自动保存。
        若未设置工程根目录则不做任何操作（调用方应先检查 get_project_root()）。
        """
        if self._project_root is None:
            return
        if "autoglm" in partial and isinstance(partial["autoglm"], dict):
            self._config.setdefault("autoglm", {})
            self._config["autoglm"].update(partial["autoglm"])
        if "post_processing" in partial and isinstance(partial["post_processing"], dict):
            self._config.setdefault("post_processing", {})
            self._config["post_processing"].update(partial["post_processing"])
        if "update_data_directory" in partial:
            val = partial["update_data_directory"]
            self._config["update_data_directory"] = str(val).strip() if isinstance(val, str) else ""
        # 将基础工程的 API/Workflow 等配置同步到增量数据目录的 project_config.json
        _sync_incremental_config(self._project_root, self._config)
        self._save()

    def _load(self, project_root: Path) -> dict:
        return _load_config_from_path(project_root)

    def _save(self) -> None:
        if self._project_root is None:
            return
        self._project_root.mkdir(parents=True, exist_ok=True)
        path = self._project_root / CONFIG_FILENAME
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)


# 单例，app 与其它调用方统一使用同一个管理器
_manager: ProjectConfigManager | None = None


def get_config_manager() -> ProjectConfigManager:
    """返回工程配置管理器单例。"""
    global _manager
    if _manager is None:
        _manager = ProjectConfigManager()
    return _manager
