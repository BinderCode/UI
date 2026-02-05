"""
图集：仅对 task_list.csv 中 status 为 success 的任务建单任务图，再得到图列表。
在项目根目录运行：python graph_build/graph_collection.py [--merge]
默认使用项目根下的 autoglm_runs 目录。图集生成与展示请在 Web 控制台「回滚攻击」页点击「生成图集」。
"""
import argparse
import csv
import json
import sys
from pathlib import Path

# 以脚本方式运行时保证项目根在 path 中，才能 import graph_build
_root = Path(__file__).resolve().parent.parent
if _root not in sys.path:
    sys.path.insert(0, str(_root))

import networkx as nx

from graph_build.models import (
    KEY_ACTION,
    KEY_APP_NAME,
    KEY_DIFF_TO_EDGE_MAP,
    KEY_EDGE_ID,
    KEY_EXISTENCE_PROBABILITY,
    KEY_INITIAL_STEP_NUMBERS,
    KEY_LAST_UPDATED,
    KEY_NODE_TYPE,
    KEY_PAGE_DESCRIPTION,
    KEY_PAGE_NAME,
    KEY_DIFFERENTIATED_CONTENT,
    KEY_SUCCESS_COUNT,
    KEY_TASK_ID,
    NODE_TYPE_END,
    NODE_TYPE_MID,
    NODE_TYPE_START,
    node_id_to_str,
    posterior_node_existence_probability,
)
from graph_build.similarity import node_three_field_similarity as _node_three_field_similarity
from graph_build.task_graph import load_task_graph, merge_duplicate_edges_same_action

# 项目根下的 autoglm_runs 目录（与 graph_collection.py 所在位置相对）
_DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "autoglm_runs"
# 相似度缓存：放在「项目文件根目录」下的 cache/node_similarity_cache.json；未传 project_root 时退化为源码根目录
_SIMILARITY_CACHE_FILENAME = "node_similarity_cache.json"
_FALLBACK_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

# task_list.csv 中视为成功的 status 值（大小写不敏感）
_STATUS_SUCCESS = "success"

# image_list.csv 中视为处理失败的状态
_IMAGE_STATUS_FAILED = "failed"


# -----------------------------------------------------------------------------
# Runs / CSV：按任务目录、CSV 读取（最底层）
# -----------------------------------------------------------------------------


def _runs_failed_steps_by_task(runs_dir: Path) -> dict[str, list[int]]:
    """
    读取 runs_dir/image_list.csv，返回每个 task_id 下 status 为 failed 的 step_number 列表。
    若文件不存在或无 failed 行，返回空 dict 或空列表。支持 UTF-8 与 GBK 编码。
    """
    path = Path(runs_dir) / "image_list.csv"
    out: dict[str, list[int]] = {}
    if not path.is_file():
        return out
    for encoding in ("utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                if "task_id" not in (reader.fieldnames or []) or "status" not in (reader.fieldnames or []):
                    return out
                for row in reader:
                    status = (row.get("status") or "").strip().lower()
                    if status != _IMAGE_STATUS_FAILED:
                        continue
                    tid = (row.get("task_id") or "").strip()
                    if not tid:
                        continue
                    try:
                        step_num = int(row.get("step_number") or "0")
                    except ValueError:
                        continue
                    out.setdefault(tid, []).append(step_num)
            break
        except UnicodeDecodeError:
            out = {}
            continue
    for tid in out:
        out[tid] = sorted(set(out[tid]))
    return out


def get_success_task_ids(runs_dir: Path, task_list_csv: str = "task_list.csv") -> list[str]:
    """
    从 runs_dir/task_list.csv 中读取 status 为 success 的 task_id 列表，按原表顺序返回。
    若文件不存在或无法解析，返回空列表。
    """
    path = Path(runs_dir) / task_list_csv
    if not path.is_file():
        return []
    task_ids = []
    for encoding in ("gbk", "utf-8"):
        try:
            with open(path, newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                if "status" not in (reader.fieldnames or []):
                    return []
                for row in reader:
                    tid = (row.get("task_id") or "").strip()
                    status = (row.get("status") or "").strip().lower()
                    if tid and status == _STATUS_SUCCESS:
                        task_ids.append(tid)
            break
        except UnicodeDecodeError:
            continue
    return task_ids


# -----------------------------------------------------------------------------
# 两图（graph）：比对产出 op_list，再按 op_list 合并（仅涉及两张图）
# -----------------------------------------------------------------------------
# 操作表项：比对阶段产出，合并阶段按序执行。可扩展多种 op 类型（结点/边等）。
# 当前仅 "merge_node"：{"op": "merge_node", "id": node_id} 表示两图中同 id 结点合并属性。


def _attrs_triple(G: nx.MultiDiGraph, nid) -> tuple[str, str, str]:
    """从结点属性读取 (app_name, page_name, page_description)。仅用于比对/相似度，不包含差异化内容。"""
    d = G.nodes.get(nid, {})
    return (
        d.get(KEY_APP_NAME, "") or "",
        d.get(KEY_PAGE_NAME, "") or "",
        d.get(KEY_PAGE_DESCRIPTION, "") or "",
    )


def _graph_compare_two_exact_id(
    G: nx.MultiDiGraph, R: nx.MultiDiGraph, cache: dict[str, float] | None = None
) -> list[dict]:
    """
    按「通用三字段 (app_name, page_name, page_description) 完全相同」比对两张图，产出 merge_node 操作列表。
    """
    op_list: list[dict] = []
    for nid_g in G.nodes():
        triple_g = _attrs_triple(G, nid_g)
        for nid_r in R.nodes():
            if triple_g != _attrs_triple(R, nid_r):
                continue
            # start 与 end 不能合并
            t_g = G.nodes[nid_g].get(KEY_NODE_TYPE) or NODE_TYPE_MID
            t_r = R.nodes[nid_r].get(KEY_NODE_TYPE) or NODE_TYPE_MID
            if (t_g == NODE_TYPE_START and t_r == NODE_TYPE_END) or (t_g == NODE_TYPE_END and t_r == NODE_TYPE_START):
                continue
            if nid_g == nid_r:
                op_list.append({"op": "merge_node", "id": nid_g})
            else:
                op_list.append({"op": "merge_node", "id": nid_r, "id_in_G_a": nid_g})
    return op_list


# -----------------------------------------------------------------------------
# 相似度匹配：三属性相似度 + 最相似结点 + 上下文 JSON + 缓存
# -----------------------------------------------------------------------------


def _similarity_cache_path(project_root: Path | None = None) -> Path:
    """相似度缓存文件路径：project_root/cache/node_similarity_cache.json；未传时用源码根目录下的 cache。"""
    if project_root is not None:
        return Path(project_root) / "cache" / _SIMILARITY_CACHE_FILENAME
    return _FALLBACK_CACHE_DIR / _SIMILARITY_CACHE_FILENAME


def _cache_key(id_a: str, id_b: str) -> str:
    """两结点 ID 的缓存键：小 ID 在前、大 ID 在后，中间 '-'，保证 (a,b) 与 (b,a) 同一键。"""
    a, b = str(id_a), str(id_b)
    return f"{min(a, b)}-{max(a, b)}"


def _load_similarity_cache(project_root: Path | None = None) -> dict[str, float]:
    """从「项目文件根目录/cache」加载相似度缓存。project_root 为 None 时用源码根目录。"""
    path = _similarity_cache_path(project_root)
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_similarity_cache(cache: dict[str, float], project_root: Path | None = None) -> None:
    """将相似度缓存写入「项目文件根目录/cache」。project_root 为 None 时用源码根目录。"""
    if not cache:
        return
    path = _similarity_cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=0)


def _cached_similarity(
    nid_a,
    nid_b,
    triple_a: tuple[str, str, str],
    triple_b: tuple[str, str, str],
    cache: dict[str, float] | None,
) -> float:
    """两结点相似度：有 cache 时先查缓存，未命中再算并写入。
    若任一方 app_name 含 Unknown（不区分大小写），或两结点 app_name 不一致，直接返回 0。
    """
    app_a, _, _ = triple_a
    app_b, _, _ = triple_b
    if "unknown" in (app_a or "").lower() or "unknown" in (app_b or "").lower():
        return 0.0
    if (app_a or "").strip() != (app_b or "").strip():
        return 0.0
    key = _cache_key(str(nid_a), str(nid_b)) if cache is not None else None
    if cache is not None and key is not None and key in cache:
        return cache[key]
    score = _node_three_field_similarity(triple_a, triple_b)
    if cache is not None and key is not None:
        cache[key] = score
    return score


def _find_best_match_in_graph(
    G_new: nx.MultiDiGraph,
    nid_new,
    R: nx.MultiDiGraph,
    cache: dict[str, float] | None = None,
) -> tuple[tuple, float] | None:
    """
    在图 R 中找出与「G_new 中结点 nid_new」三属性相似度最高的结点。
    返回 (best_node_id_in_R, similarity_score)，R 为空时返回 None。
    cache 不为 None 时使用相似度缓存（key=两结点 ID 拼接，value=相似度）。
    """
    triple_new = _attrs_triple(G_new, nid_new)
    best_nid = None
    best_score = -1.0
    for nid in R.nodes():
        triple = _attrs_triple(R, nid)
        score = _cached_similarity(nid_new, nid, triple_new, triple, cache)
        if score > best_score:
            best_score = score
            best_nid = nid
    if best_nid is None:
        return None
    return (best_nid, best_score)


def _node_snapshot_for_json(G: nx.MultiDiGraph, nid) -> dict:
    """结点信息转为可 JSON 序列化的字典（id + app_name/page_name/page_description/differentiated_content 等）。differentiated_content 为数组。"""
    attrs = dict(G.nodes.get(nid, {}))
    raw_diff = attrs.get(KEY_DIFFERENTIATED_CONTENT)
    diff_val = raw_diff if isinstance(raw_diff, list) else ([raw_diff] if raw_diff else [])
    out = {
        "id": node_id_to_str(nid),
        KEY_APP_NAME: attrs.get(KEY_APP_NAME, "") or "",
        KEY_PAGE_NAME: attrs.get(KEY_PAGE_NAME, "") or "",
        KEY_PAGE_DESCRIPTION: attrs.get(KEY_PAGE_DESCRIPTION, "") or "",
        KEY_DIFFERENTIATED_CONTENT: diff_val,
        "diff_to_edge_map": attrs.get(KEY_DIFF_TO_EDGE_MAP, {}),
    }
    if attrs.get(KEY_SUCCESS_COUNT) is not None:
        out["success_count"] = attrs[KEY_SUCCESS_COUNT]
    return out


def _edges_snapshot_for_json(G: nx.MultiDiGraph, u, v) -> list[dict]:
    """两结点之间的所有边转为可 JSON 序列化的列表。"""
    if u not in G or v not in G:
        return []
    out = []
    for key in G[u][v]:
        edge_attrs = dict(G[u][v][key])
        rec = {
            "from": node_id_to_str(u),
            "to": node_id_to_str(v),
            KEY_ACTION: edge_attrs.get(KEY_ACTION),
            "edge_id": edge_attrs.get(KEY_EDGE_ID, ""),
        }
        out.append(rec)
    return out


def _build_pair_context_json(
    G_new: nx.MultiDiGraph,
    n_new,
    G_existing: nx.MultiDiGraph,
    n_existing,
    similarity_score: float,
) -> dict:
    """
    将「新图中的一个结点」与「已有图中最相似结点」的上下文组成 JSON，供大模型判定是否同一结点。
    包含：两图中各自的「前驱结点、入边、当前结点、出边、后继结点」。
    TODO: 将此 JSON 发送给大模型，根据返回决定是否加入 merge_node；当前仅输出用于调试。
    """
    def _side(G: nx.MultiDiGraph, nid) -> dict:
        prev_nodes = []
        edges_in = []
        next_nodes = []
        edges_out = []
        for pred in G.predecessors(nid):
            prev_nodes.append(_node_snapshot_for_json(G, pred))
            edges_in.extend(_edges_snapshot_for_json(G, pred, nid))
        for succ in G.successors(nid):
            next_nodes.append(_node_snapshot_for_json(G, succ))
            edges_out.extend(_edges_snapshot_for_json(G, nid, succ))
        return {
            "current_node": _node_snapshot_for_json(G, nid),
            "prev_nodes": prev_nodes,
            "edges_in": edges_in,
            "next_nodes": next_nodes,
            "edges_out": edges_out,
        }

    return {
        "similarity_score": round(similarity_score, 4),
        "graph_new": _side(G_new, n_new),
        "graph_existing": _side(G_existing, n_existing),
    }


def _decide_merge_by_similarity(pair_ctx: dict, merge_threshold: float = 0.9) -> bool:
    """
    根据候选对上下文决定是否合并该结点对。
    TODO: 将 pair_ctx 发给大模型，根据返回决定；暂时：相似度 >= merge_threshold 则合并。
    """
    # print(json.dumps(pair_ctx, ensure_ascii=False, indent=2), file=sys.stderr)
    score = pair_ctx.get("similarity_score", 0.0)
    return score >= merge_threshold


def _graph_compare_two_similarity(
    G: nx.MultiDiGraph,
    R: nx.MultiDiGraph,
    cache: dict[str, float] | None = None,
    merge_threshold: float = 0.9,
) -> list[dict]:
    """
    图 vs 图：新图 G 中每个结点在旧图 R 中找相似度最大结点，封装 JSON 后由 _decide_merge_by_similarity 决定是否合并。
    输入输出与 _graph_compare_two_exact_id 一致：(G, R) -> op_list。
    cache 不为 None 时两两相似度会查/写缓存。merge_threshold：相似度 >= 该值才合并，默认 0.9。
    """
    op_list: list[dict] = []
    for n_new in G.nodes():
        match = _find_best_match_in_graph(G, n_new, R, cache)
        if match is None:
            continue
        n_best, score = match
        pair_ctx = _build_pair_context_json(G, n_new, R, n_best, score)
        if not _decide_merge_by_similarity(pair_ctx, merge_threshold=merge_threshold):
            continue
        # start 与 end 不能合并
        t_new = G.nodes[n_new].get(KEY_NODE_TYPE) or NODE_TYPE_MID
        t_best = R.nodes[n_best].get(KEY_NODE_TYPE) or NODE_TYPE_MID
        if (t_new == NODE_TYPE_START and t_best == NODE_TYPE_END) or (t_new == NODE_TYPE_END and t_best == NODE_TYPE_START):
            continue
        if n_new == n_best:
            op_list.append({"op": "merge_node", "id": n_new})
        else:
            op_list.append({"op": "merge_node", "id": n_best, "id_in_G_a": n_new})
    return op_list


def _mix_hex_colors(hex_list: list[str]) -> str:
    """多个 #RRGGBB 取平均 RGB，返回 #RRGGBB。空列表返回空串。"""
    if not hex_list:
        return ""
    rgbs = []
    for h in hex_list:
        s = (h or "").strip().lstrip("#")
        if len(s) != 6:
            continue
        try:
            rgbs.append((int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)))
        except ValueError:
            continue
    if not rgbs:
        return hex_list[0].strip() if hex_list else ""
    n = len(rgbs)
    r = sum(x[0] for x in rgbs) // n
    g = sum(x[1] for x in rgbs) // n
    b = sum(x[2] for x in rgbs) // n
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
    )


def _graph_merge_by_ops(
    G_a: nx.MultiDiGraph,
    G_b: nx.MultiDiGraph,
    op_list: list[dict],
) -> nx.MultiDiGraph | None:
    """
    按操作表合并两张图；无操作则不合并，返回 None。
    支持 "merge_node"：op 为 {"op": "merge_node", "id": id_in_G_b} 表示两图同 id 结点合并；
    若有 "id_in_G_a" 则表示 G_a 中该结点与 G_b 中 id 合并（两结点可不同 id）。
    """
    if not op_list:
        return None
    merge_ops = [op for op in op_list if op.get("op") == "merge_node"]
    if not merge_ops:
        return None
    # (id_in_G_a, id_in_G_b) 列表，多个 id_a 可指向同一 id_b
    pairs = [(op.get("id_in_G_a", op["id"]), op["id"]) for op in merge_ops]
    # 仅保留在两图中均存在的结点
    pairs = [(a, b) for a, b in pairs if a in G_a.nodes() and b in G_b.nodes()]
    if not pairs:
        return None
    canonical: dict = {}  # id_a -> id_b（键可为全局 ID 或三元组）
    for a, b in pairs:
        canonical[a] = b

    def _task_id_list(g: nx.MultiDiGraph, node_attrs: dict) -> list[str]:
        t = node_attrs.get(KEY_TASK_ID)
        if t is not None:
            return list(t) if isinstance(t, list) else [t]
        gt = g.graph.get("task_id")
        if gt is None:
            return []
        return list(gt) if isinstance(gt, list) else [gt]

    def _merge_node_attrs(attrs_list: list[dict]) -> dict:
        out: dict = {}
        all_steps: list[int] = []
        total_success = 0
        merged_tasks: list[str] = []
        seen_task = set()
        diff_parts: list[str] = []  # 差异化内容合并为数组，追加
        for attrs in attrs_list:
            out.update(attrs)
            all_steps.extend(attrs.get(KEY_INITIAL_STEP_NUMBERS) or [])
            total_success += attrs.get(KEY_SUCCESS_COUNT, 0)
            if attrs.get(KEY_LAST_UPDATED):
                out[KEY_LAST_UPDATED] = attrs[KEY_LAST_UPDATED]
            val = attrs.get(KEY_TASK_ID)
            tasks = list(val) if isinstance(val, list) else ([val] if val else [])
            for t in tasks:
                t = t if isinstance(t, str) else str(t)
                if t and t not in seen_task:
                    seen_task.add(t)
                    merged_tasks.append(t)
            raw = attrs.get(KEY_DIFFERENTIATED_CONTENT)
            if isinstance(raw, list):
                for item in raw:
                    s = (item if isinstance(item, str) else str(item)).strip()
                    if s:
                        diff_parts.append(s)
            elif raw is not None and str(raw).strip():
                diff_parts.append(str(raw).strip())
        all_steps = sorted(set(all_steps))
        out[KEY_INITIAL_STEP_NUMBERS] = all_steps
        out[KEY_SUCCESS_COUNT] = total_success
        out[KEY_EXISTENCE_PROBABILITY] = posterior_node_existence_probability(
            total_success, total_visits=None
        )
        out[KEY_TASK_ID] = merged_tasks
        # 前三个属性取其中一个（第一个非空）即可
        for key in (KEY_APP_NAME, KEY_PAGE_NAME, KEY_PAGE_DESCRIPTION):
            for attrs in attrs_list:
                v = attrs.get(key)
                if isinstance(v, str) and (v or "").strip():
                    out[key] = (v or "").strip()
                    break
                elif v is not None and v != "":
                    out[key] = v
                    break
        out[KEY_DIFFERENTIATED_CONTENT] = diff_parts  # 数组，追加到一起
        # 合并差异化内容到边的映射：如果同一个差异化内容对应不同的边，只保留第一个
        merged_map = {}
        for attrs in attrs_list:
            diff_map = attrs.get(KEY_DIFF_TO_EDGE_MAP, {})
            if isinstance(diff_map, dict):
                for diff, edge_id in diff_map.items():
                    if diff not in merged_map:
                        merged_map[diff] = edge_id
        out[KEY_DIFF_TO_EDGE_MAP] = merged_map
        # 结点颜色：多个则混色，一个则保留，无则不留
        colors = [a.get("color") for a in attrs_list if (a.get("color") or "").strip()]
        if len(colors) > 1:
            out["color"] = _mix_hex_colors(colors)
        elif len(colors) == 1:
            out["color"] = (colors[0] or "").strip()
        # 合并 type：start+mid->start，end+mid->end（start+end 已在比对阶段禁止合并）
        has_start = any((a.get(KEY_NODE_TYPE) or NODE_TYPE_MID) == NODE_TYPE_START for a in attrs_list)
        has_end = any((a.get(KEY_NODE_TYPE) or NODE_TYPE_MID) == NODE_TYPE_END for a in attrs_list)
        if has_start:
            out[KEY_NODE_TYPE] = NODE_TYPE_START
        elif has_end:
            out[KEY_NODE_TYPE] = NODE_TYPE_END
        else:
            out[KEY_NODE_TYPE] = NODE_TYPE_MID
        return out

    G_new = nx.MultiDiGraph()
    # 每个 id_b 对应的所有 id_a（含 id_b 自身若在 G_a 中）
    id_b_to_group: dict = {}  # id_b -> list of id_a（键可为全局 ID）
    for _a, _b in pairs:
        id_b_to_group.setdefault(_b, []).append(_a)

    for nid in G_a.nodes():
        if nid in canonical:
            continue
        attrs = dict(G_a.nodes[nid])
        if KEY_TASK_ID not in attrs:
            attrs[KEY_TASK_ID] = _task_id_list(G_a, attrs)
        G_new.add_node(nid, **attrs)
    for id_b, group in id_b_to_group.items():
        attrs_list = []
        for a in group:
            if a in G_a.nodes():
                d = dict(G_a.nodes[a])
                if KEY_TASK_ID not in d:
                    d[KEY_TASK_ID] = _task_id_list(G_a, d)
                attrs_list.append(d)
        gb_attrs = dict(G_b.nodes[id_b])
        if KEY_TASK_ID not in gb_attrs:
            gb_attrs[KEY_TASK_ID] = _task_id_list(G_b, gb_attrs)
        attrs_list.append(gb_attrs)
        merged = _merge_node_attrs(attrs_list)
        G_new.add_node(id_b, **merged)
    for nid in G_b.nodes():
        if nid in id_b_to_group:
            continue
        attrs = dict(G_b.nodes[nid])
        if KEY_TASK_ID not in attrs:
            attrs[KEY_TASK_ID] = _task_id_list(G_b, attrs)
        G_new.add_node(nid, **attrs)

    def _map(u: tuple) -> tuple:
        return canonical.get(u, u)

    for u, v, key in G_a.edges(keys=True):
        u2, v2 = _map(u), _map(v)
        if u2 not in G_new or v2 not in G_new:
            raise RuntimeError(
                f"合并 G_a 边时映射后的端点不在 G_new 中: 边 ({u!r}, {v!r}) -> ({u2!r}, {v2!r})"
            )
        G_new.add_edge(u2, v2, **dict(G_a[u][v][key]))
    for u, v, key in G_b.edges(keys=True):
        G_new.add_edge(u, v, **dict(G_b[u][v][key]))
    t_a = G_a.graph.get("task_id")
    t_b = G_b.graph.get("task_id")
    task_ids: list[str] = []
    if t_a is not None:
        task_ids.extend(t_a if isinstance(t_a, list) else [t_a])
    if t_b is not None:
        task_ids.extend(t_b if isinstance(t_b, list) else [t_b])
    G_new.graph["task_id"] = task_ids if task_ids else (t_a or t_b or "")
    return G_new


# -----------------------------------------------------------------------------
# 图集（collection）：单图入图集、图列表合并、从 runs 建图集（层次由低到高）
# -----------------------------------------------------------------------------


def _collection_merge_one_graph(
    collection: list[nx.MultiDiGraph],
    new_graph: nx.MultiDiGraph,
    compare_two=None,
    cache: dict[str, float] | None = None,
    merge_threshold: float = 0.9,
) -> list[nx.MultiDiGraph]:
    """
    将一张新图并入图集：与 collection 中图按 compare_two 比对得到 op_list，能合并则合并，直至无法再合并后加入并返回。
    compare_two(G, R, cache) -> op_list。cache 用于相似度比对时的两两结果缓存。
    merge_threshold 仅在使用默认相似度比对时生效，相似度 >= 该值才合并，默认 0.9。
    """
    if compare_two is None:
        compare_two = lambda G, R, c=None: _graph_compare_two_similarity(
            G, R, cache=c, merge_threshold=merge_threshold
        )
    current = new_graph.copy()
    collection = list(collection)
    while True:
        merged_this_round = False
        for i, R in enumerate(collection):
            op_list = compare_two(current, R, cache) if cache is not None else compare_two(current, R)
            if not op_list:
                continue
            merged = _graph_merge_by_ops(current, R, op_list)
            if merged is not None:
                current = merged
                collection.pop(i)
                merged_this_round = True
                break
        if not merged_this_round:
            break
    collection.append(current)
    return collection


def merge_graph_collection(
    graph_list: list[nx.MultiDiGraph],
    on_progress=None,
    merge_threshold: float = 0.9,
    project_root: Path | None = None,
) -> list[nx.MultiDiGraph]:
    """
    图集合并：输入图集，输出图集。遍历图列表，逐张并入结果图集。
    相似度缓存放在 project_root/cache/node_similarity_cache.json；project_root 为 None 时用源码根目录。
    on_progress(current_index_1based, total, result_count) 可选；
    merge_threshold：相似度 >= 该值才合并结点，默认 0.9。
    """
    if not graph_list:
        return []
    total = len(graph_list)

    def _default_progress(cur: int, tot: int, result_count: int) -> None:
        print(
            f"[合并图集] {cur}/{tot} 正在并入第 {cur} 张图（当前结果集 {result_count} 张）",
            file=sys.stderr,
        )

    report = on_progress if on_progress is not None else _default_progress
    cache = _load_similarity_cache(project_root)
    result: list[nx.MultiDiGraph] = []
    for i, G in enumerate(graph_list):
        report(i + 1, total, len(result))
        result = _collection_merge_one_graph(
            result, G.copy(), cache=cache, merge_threshold=merge_threshold
        )
    # 形成图集后：同一起终且边上指令一模一样的边合并为一条
    for i in range(len(result)):
        result[i] = merge_duplicate_edges_same_action(result[i])
    if total and on_progress is None:
        print(f"[合并图集] 完成，共 {len(result)} 张图。", file=sys.stderr)
    _save_similarity_cache(cache, project_root)
    return result


def build_graph_collection(runs_dir: Path) -> list[nx.MultiDiGraph]:
    """
    从 runs 目录建图集：仅对 task_list.csv 中 status 为 success 的任务建单任务图；
    返回图列表，顺序与 task_list.csv 中成功任务顺序一致；无有效步骤的任务不包含在内。
    """
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    success_ids = get_success_task_ids(runs_dir)
    if not success_ids:
        return []
    failed_by_task = _runs_failed_steps_by_task(runs_dir)
    graphs = []
    for task_id in success_ids:
        task_dir = runs_dir / task_id
        if not task_dir.is_dir() or not task_id.startswith("task_"):
            continue
        failed_steps = failed_by_task.get(task_id)
        if failed_steps:
            steps_str = ", ".join(f"step_{s:03d}" for s in failed_steps)
            print(
                f"[DEBUG] {task_id} has {len(failed_steps)} failed image(s) in image_list.csv: {steps_str}",
                file=sys.stderr,
            )
        G = load_task_graph(runs_dir, task_id)
        if G is not None:
            G.graph["task_id"] = task_id
            for nid in G.nodes():
                G.nodes[nid][KEY_TASK_ID] = [task_id]
            graphs.append(G)
    return graphs



def main() -> None:
    parser = argparse.ArgumentParser(description="建图集（仅 status=success 的任务）。生成图集请使用 Web 控制台「回滚攻击」页的「生成图集」按钮。")
    parser.add_argument("--merge", action="store_true", help="建图后合并图集")
    args = parser.parse_args()
    runs_dir = _DEFAULT_RUNS_DIR
    if not runs_dir.is_dir():
        print(f"Runs dir not found: {runs_dir}", file=sys.stderr)
        sys.exit(1)
    success_ids = get_success_task_ids(runs_dir)
    print(f"task_list.csv: {len(success_ids)} tasks with status={_STATUS_SUCCESS}")
    if not success_ids:
        print("No success tasks, nothing to build.", file=sys.stderr)
        return
    graphs = build_graph_collection(runs_dir)
    print(f"Built {len(graphs)} graphs from {runs_dir}")
    if args.merge and graphs:
        project_root = runs_dir.parent
        graphs = merge_graph_collection(graphs, project_root=project_root)
        print(f"After merge: {len(graphs)} graphs")
    print("生成图集 JSON 请使用 Web 控制台「回滚攻击」页的「生成图集」按钮。")


if __name__ == "__main__":
    main()
