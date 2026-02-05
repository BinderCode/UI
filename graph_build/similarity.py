"""
图合并用结点相似度：基于本地句子嵌入模型（sentence-transformers + BGE 中文模型）。
通用三字段（app_name / page_name / page_description）分别编码后算余弦相似度，再加权合并；不包含差异化内容。
"""
from __future__ import annotations

_MODEL = None
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def _get_model():
    """懒加载：首次调用时加载本地嵌入模型。优先仅用本地缓存，避免访问 HuggingFace 超时。"""
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "图合并相似度需要 sentence-transformers，请安装： pip install sentence-transformers"
            )
        try:
            _MODEL = SentenceTransformer(_MODEL_NAME, local_files_only=True)
        except (OSError, Exception):
            _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def _cos_to_01(cos_sim: float) -> float:
    """余弦相似度 [-1, 1] 线性映射到 [0, 1]。"""
    return max(0.0, min(1.0, (float(cos_sim) + 1.0) / 2.0))


def node_three_field_similarity(nid_a: tuple, nid_b: tuple) -> float:
    """
    两结点通用三字段 (app_name, page_name, page_description) 的语义相似度 [0, 1]。
    使用本地 BGE 中文嵌入模型分别编码三字段，余弦相似度后加权平均。不包含差异化内容。
    """
    app1 = (nid_a[0] or "").strip()
    p1 = (nid_a[1] if len(nid_a) > 1 else "").strip()
    d1 = (nid_a[2] if len(nid_a) > 2 else "").strip()
    app2 = (nid_b[0] or "").strip()
    p2 = (nid_b[1] if len(nid_b) > 1 else "").strip()
    d2 = (nid_b[2] if len(nid_b) > 2 else "").strip()

    model = _get_model()

    # 空串用占位避免编码异常，相似度视为 0
    def _embed(text: str) -> list[list[float]]:
        if not text:
            return model.encode([" "], normalize_embeddings=True)
        return model.encode([text], normalize_embeddings=True)

    import numpy as np
    from sentence_transformers import util

    def _scalar_sim(e1, e2):
        r = util.cos_sim(e1, e2)
        return float(np.asarray(r).flat[0])

    rapp = _cos_to_01(_scalar_sim(_embed(app1), _embed(app2)))
    rp = _cos_to_01(_scalar_sim(_embed(p1), _embed(p2)))
    rd = _cos_to_01(_scalar_sim(_embed(d1), _embed(d2)))

    return 0.4 * rapp + 0.4 * rp + 0.2 * rd


def main() -> None:
    """测试：几组结点 ID 的语义相似度。"""
    examples = [
        # (app_name, page_name, page_description) 结点 A, 结点 B, 说明
        (
            ("微信", "聊天列表", "显示与好友的对话列表"),
            ("微信", "聊天列表", "显示与好友的对话列表"),
            "完全相同",
        ),
        (
            ("微信", "聊天列表", "显示与好友的对话列表"),
            ("微信", "聊天界面", "与某好友的对话页面"),
            "同应用不同页面",
        ),
        (
            ("设置", "WLAN", "通用 WLAN 设置页描述"),
            ("设置", "WLAN", "通用 WLAN 设置页描述"),
            "同应用同页面同描述",
        ),
        (
            ("小红书", "搜索页", "搜索框为空，等待输入"),
            ("淘宝", "首页", "商品推荐流"),
            "不同应用不同页面",
        ),
    ]
    print("加载模型并计算相似度（首次运行会下载 BGE 模型）...\n")
    for nid_a, nid_b, desc in examples:
        score = node_three_field_similarity(nid_a, nid_b)
        print(f"[{desc}]")
        print(f"  A: {nid_a}")
        print(f"  B: {nid_b}")
        print(f"  相似度: {score:.4f}\n")


if __name__ == "__main__":
    main()
