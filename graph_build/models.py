"""
图数据结构约定：结点键为全局唯一 ID（字符串）；结点属性含 app_name、page_name、page_description、differentiated_content。
ID 与属性分离，不再混用。

贝叶斯更新：结点/边存在概率采用 Beta-二项共轭，先验 Beta(α0, β0)，观测为成功次数 k、总次数 n（可选），
后验均值为 (α0 + k) / (α0 + β0 + n)；无总次数时用 (α0 + k) / (α0 + β0 + k)。
"""
import time
import uuid

# 贝叶斯先验：Beta(α0, β0)，均匀先验
BETA_PRIOR_ALPHA = 1.0
BETA_PRIOR_BETA = 1.0

# 边类型常量
EDGE_TYPE_SOLID = "solid"   # 跨页面
EDGE_TYPE_DASHED = "dashed" # 同页面不同状态

# 结点属性在 NetworkX 中的 key
KEY_APP_NAME = "app_name"
KEY_PAGE_NAME = "page_name"
KEY_PAGE_DESCRIPTION = "page_description"
KEY_DIFFERENTIATED_CONTENT = "differentiated_content"
KEY_DIFF_TO_EDGE_MAP = "diff_to_edge_map"
KEY_LAST_UPDATED = "last_updated"
KEY_SUCCESS_COUNT = "success_count"
KEY_EXISTENCE_PROBABILITY = "existence_probability"
KEY_INITIAL_STEP_NUMBERS = "initial_step_numbers"
KEY_TASK_ID = "task_id"
KEY_NODE_TYPE = "node_type"

# 结点 type：原始操作链的起点/终点，默认 mid
NODE_TYPE_START = "start"
NODE_TYPE_MID = "mid"
NODE_TYPE_END = "end"

# 边属性
KEY_EDGE_TYPE = "edge_type"
KEY_ACTION = "action"
KEY_EDGE_ID = "edge_id"
KEY_EDGE_SUCCESS_COUNT = "success_count"
KEY_EDGE_EXISTENCE_PROBABILITY = "existence_probability"
KEY_EDGE_INITIAL_STEP_NUMBER = "initial_step_number"
KEY_ACTIONS = "action"
KEY_INSTRUCTIONS = "instructions"  # 已弃用


def generate_global_id() -> str:
    """生成全局唯一结点 ID：时间戳（毫秒）+ 随机十六进制。"""
    return f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def node_id_to_str(node_id: str) -> str:
    """结点 ID 序列化为字符串（用于 JSON 等）。ID 即全局唯一字符串，原样返回。"""
    return str(node_id)


# -----------------------------------------------------------------------------
# 贝叶斯更新：Beta-二项共轭，后验均值作为存在概率
# -----------------------------------------------------------------------------


def posterior_node_existence_probability(
    success_count: int,
    total_visits: int | None = None,
    alpha0: float | None = None,
    beta0: float | None = None,
) -> float:
    """
    结点存在概率的贝叶斯后验均值。
    先验 p ~ Beta(α0, β0)，似然为 n 次访问中该结点出现 k 次（二项），
    后验 p | data ~ Beta(α0 + k, β0 + n - k)，返回后验均值 (α0 + k) / (α0 + β0 + n)。
    若 total_visits 为 None，视为仅观测到成功次数 k，无总次数，用 (α0 + k) / (α0 + β0 + k)。
    """
    a = alpha0 if alpha0 is not None else BETA_PRIOR_ALPHA
    b = beta0 if beta0 is not None else BETA_PRIOR_BETA
    k = max(0, int(success_count))
    if total_visits is not None:
        n = max(k, int(total_visits))
        return (a + k) / (a + b + n)
    return (a + k) / (a + b + k) if (a + b + k) > 0 else 0.0


def posterior_edge_existence_probability(
    success_count: int,
    total_edge_visits: int | None = None,
    alpha0: float | None = None,
    beta0: float | None = None,
) -> float:
    """
    边存在概率的贝叶斯后验均值。
    若给出 total_edge_visits（所有边被使用的总次数），后验均值 (α0 + k) / (α0 + β0 + n)。
    否则仅用成功次数 k：(α0 + k) / (α0 + β0 + k)。
    """
    a = alpha0 if alpha0 is not None else BETA_PRIOR_ALPHA
    b = beta0 if beta0 is not None else BETA_PRIOR_BETA
    k = max(0, int(success_count))
    if total_edge_visits is not None and total_edge_visits > 0:
        n = max(k, int(total_edge_visits))
        return (a + k) / (a + b + n)
    return (a + k) / (a + b + k) if (a + b + k) > 0 else 0.0
