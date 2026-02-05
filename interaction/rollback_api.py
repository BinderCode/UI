# 回滚攻击：图集加载、合并布局、重放执行。供 app 路由与 graph_collection/task_graph 调用。

import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import networkx as nx

from graph_build.models import (
    EDGE_TYPE_DASHED,
    EDGE_TYPE_SOLID,
    KEY_ACTION,
    KEY_APP_NAME,
    KEY_DIFF_TO_EDGE_MAP,
    KEY_EDGE_EXISTENCE_PROBABILITY,
    KEY_EDGE_ID,
    KEY_EDGE_INITIAL_STEP_NUMBER,
    KEY_EDGE_TYPE,
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
    NODE_TYPE_START,
    node_id_to_str,
)

NODE_COLORS = [
    "#97C2FC",
    "#FAA076",
    "#7AEFA0",
    "#E6A0C0",
    "#C5B3E6",
    "#FFE066",
    "#88D9E6",
    "#F5B7B1",
]

# 图集统计 JSON：工程目录下文件名，与 graph_collection.json 同目录
GRAPH_STATS_JSON = "graph_collection_stats.json"


def _stats_json_path(project_root: Path) -> Path:
    """图集统计 JSON 路径：工程目录下 graph_collection_stats.json。"""
    return Path(project_root) / GRAPH_STATS_JSON


def get_graph_stats(project_root: Path) -> dict:
    """
    读取工程目录下 graph_collection_stats.json，返回项目总体统计数据（供前端展示）。
    返回 { "ok": True, "data": {...} } 或 { "ok": False, "error": "..." }。
    """
    path = _stats_json_path(project_root)
    if not path.is_file():
        return {"ok": False, "error": "Statistics file not found. Generate graph collection first."}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "data": data}
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)}


def _action_type_from_edge(edge_dict: dict) -> str:
    """从边（dict，含 action）取操作类型，与 task_graph 一致：action.get('action') 或 action.get('action_type')。"""
    action = edge_dict.get("action")
    if not isinstance(action, dict):
        return ""
    t = (action.get("action") or action.get("action_type") or "")
    return (t if isinstance(t, str) else str(t)).strip() or ""


def _action_type_from_nx_edge(G: nx.MultiDiGraph, u: str, v: str, k: Any) -> str:
    """从 nx 边 G[u][v][k] 取操作类型。"""
    try:
        attrs = G[u][v][k]
    except (KeyError, TypeError):
        return ""
    action = attrs.get(KEY_ACTION) if isinstance(attrs, dict) else {}
    if not isinstance(action, dict):
        return ""
    t = (action.get("action") or action.get("action_type") or "")
    return (t if isinstance(t, str) else str(t)).strip() or ""


def _action_type_counts_from_graph_dict(g: dict) -> dict[str, int]:
    """从 graph_list 单图（dict，含 edges）统计各操作类型数量。"""
    counts: dict[str, int] = {}
    for e in g.get("edges") or []:
        t = _action_type_from_edge(e)
        if t:
            counts[t] = counts.get(t, 0) + 1
    return counts


def _action_type_counts_from_nx_graph(G: nx.MultiDiGraph) -> dict[str, int]:
    """从 nx.MultiDiGraph 统计各操作类型数量。"""
    counts: dict[str, int] = {}
    for u, v, k in G.edges(keys=True):
        t = _action_type_from_nx_edge(G, u, v, k)
        if t:
            counts[t] = counts.get(t, 0) + 1
    return counts


def _aggregate_action_counts(counts_list: list[dict[str, int]]) -> dict[str, int]:
    """合并多图的 action 类型计数为总体计数。"""
    out: dict[str, int] = {}
    for c in counts_list:
        for k, v in (c or {}).items():
            if k:
                out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _build_pre_merge_stats(graph_list: list[dict]) -> dict:
    """从 graph_list 生成预合并统计（项目总体 + 每任务 ATAD）。"""
    nodes_pre_total = sum(len(g.get("nodes") or []) for g in graph_list)
    edges_pre_total = sum(len(g.get("edges") or []) for g in graph_list)
    per_task: list[dict] = []
    all_pre_counts: list[dict[str, int]] = []
    for g in graph_list:
        task_id = g.get("task_id")
        if task_id is not None and isinstance(task_id, list):
            task_id = task_id[0] if task_id else ""
        task_id = str(task_id or "").strip() or ""
        nodes = g.get("nodes") or []
        edges = g.get("edges") or []
        scene = (nodes[0].get("app_name") or "").strip() if nodes else ""
        counts = _action_type_counts_from_graph_dict(g)
        all_pre_counts.append(counts)
        row: dict = {
            "task_id": task_id,
            "scene": scene,
            "nodes_pre": len(nodes),
            "edges_pre": len(edges),
        }
        row.update(counts)
        per_task.append(row)
    action_type_counts_pre = _aggregate_action_counts(all_pre_counts)
    return {
        "task_count": len(graph_list),
        "nodes_pre_total": nodes_pre_total,
        "edges_pre_total": edges_pre_total,
        "action_type_counts_pre": action_type_counts_pre,
        "per_task": per_task,
        "nodes_merged_total": None,
        "edges_merged_total": None,
        "NCR": None,
        "ECR": None,
        "merge_time_s": None,
        "action_type_counts_merged": None,
        "per_merged_graph": None,
    }


def _build_merge_stats(
    graphs_merged: list,
    total_merged_nodes: int,
    total_merged_edges: int,
    merge_time_s: float,
    ncr: float,
    ecr: float,
) -> dict:
    """合并后统计：每图节点/边数 + 每图操作类型数量。"""
    per_merged_graph: list[dict] = []
    all_merged_counts: list[dict[str, int]] = []
    for G in graphs_merged:
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()
        counts = _action_type_counts_from_nx_graph(G)
        all_merged_counts.append(counts)
        row: dict = {"nodes": n_nodes, "edges": n_edges}
        row.update(counts)
        per_merged_graph.append(row)
    action_type_counts_merged = _aggregate_action_counts(all_merged_counts)
    return {
        "nodes_merged_total": total_merged_nodes,
        "edges_merged_total": total_merged_edges,
        "NCR": ncr,
        "ECR": ecr,
        "merge_time_s": merge_time_s,
        "action_type_counts_merged": action_type_counts_merged,
        "per_merged_graph": per_merged_graph,
    }


def _write_stats_json(path: Path, data: dict) -> None:
    """写入图集统计 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """#RRGGBB 或 RRGGBB -> (r, g, b) 0-255。"""
    s = (hex_str or "").strip().lstrip("#")
    if len(s) != 6:
        return (0, 0, 0)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """(r,g,b) 0-255 -> #RRGGBB。"""
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, r)),
        max(0, min(255, g)),
        max(0, min(255, b)),
    )


def _mix_hex_colors(hex_list: list[str]) -> str:
    """多个 #RRGGBB 取平均 RGB，返回 #RRGGBB。空列表返回第一色。"""
    if not hex_list:
        return NODE_COLORS[0]
    rgbs = [_hex_to_rgb(h) for h in hex_list if h]
    if not rgbs:
        return NODE_COLORS[0]
    n = len(rgbs)
    r = sum(x[0] for x in rgbs) // n
    g = sum(x[1] for x in rgbs) // n
    b = sum(x[2] for x in rgbs) // n
    return _rgb_to_hex(r, g, b)


def _assign_unmerged_colors(graph_list: list[dict]) -> None:
    """未合并图集：每个图一个颜色，写入该图所有结点的 color。原地修改。"""
    for i, g in enumerate(graph_list):
        c = NODE_COLORS[i % len(NODE_COLORS)]
        for n in g.get("nodes") or []:
            n["color"] = c


def _task_to_color_from_unmerged(graph_list: list[dict]) -> dict[str, str]:
    """从未合并图集（已赋色）得到 task_id -> 颜色 映射。按图下标取色。"""
    out: dict[str, str] = {}
    for i, g in enumerate(graph_list):
        c = NODE_COLORS[i % len(NODE_COLORS)]
        tid = g.get("task_id")
        if tid is None:
            continue
        if isinstance(tid, list):
            for t in tid:
                if t:
                    out[t] = c
        else:
            out[tid] = c
    return out


def _assign_merged_colors(graph_list: list[dict], task_to_color: dict[str, str]) -> None:
    """合并图集：结点已有 color 则保留；否则按 task_to_color 赋色，合并结点多色混色。原地修改。"""
    for g in graph_list:
        nodes = g.get("nodes") or []
        for n in nodes:
            if (n.get("color") or "").strip():
                continue
            task_ids = n.get("task_id")
            if not task_ids:
                n["color"] = NODE_COLORS[0]
                continue
            if not isinstance(task_ids, list):
                task_ids = [task_ids]
            colors_for_node = [task_to_color.get(t, NODE_COLORS[0]) for t in task_ids if t]
            if not colors_for_node:
                n["color"] = NODE_COLORS[0]
                continue
            n["color"] = (
                _mix_hex_colors(colors_for_node) if len(colors_for_node) > 1 else colors_for_node[0]
            )


def _graph_to_vis_data(G: nx.MultiDiGraph) -> dict:
    """将 MultiDiGraph 转为 vis-network 可用的 nodes + edges（含坐标与详情）。"""
    _MAX_LABEL, _WEB_MAX = 25, 10

    def _node_label(nid):
        d = G.nodes.get(nid, {})
        app = (d.get(KEY_APP_NAME) or "").strip()[: _MAX_LABEL]
        page = (d.get(KEY_PAGE_NAME) or "").strip()[: _MAX_LABEL]
        desc = (d.get(KEY_PAGE_DESCRIPTION) or "").strip()[: _MAX_LABEL]
        raw_diff = d.get(KEY_DIFFERENTIATED_CONTENT)
        if isinstance(raw_diff, list):
            diff = "\n".join(str(x).strip()[: _MAX_LABEL] for x in raw_diff if x).strip()
        else:
            diff = (raw_diff or "").strip()[: _MAX_LABEL]
        parts = [app or "?", page or "?", desc]
        if diff:
            parts.append(diff)
        return "\n".join(p for p in parts if p and p != "?") or "?"

    def _node_label_short(nid):
        d = G.nodes.get(nid, {})
        app = (d.get(KEY_APP_NAME) or "").strip()
        page = (d.get(KEY_PAGE_NAME) or "").strip()
        desc = (d.get(KEY_PAGE_DESCRIPTION) or "").strip()[:20]
        s = f"{app} {page}".strip() or "?"
        if desc and len(s) < _WEB_MAX - 4:
            s = (s + " " + desc).strip()
        return (s[:_WEB_MAX - 1] + "…") if len(s) > _WEB_MAX else s

    def _action_short(act):
        if not isinstance(act, dict):
            return ""
        a = act.get("action") or act.get("action_type") or ""
        app = act.get("app") or act.get("target") or ""
        return f"{a}({app})"[:20] if app else str(a)[:20]

    def _linear_layout(G_):
        if G_.number_of_nodes() == 0:
            return {}
        topo = None
        try:
            topo = list(nx.topological_sort(G_))
        except (nx.NetworkXError, nx.NetworkXUnfeasible):
            pass
        if topo is None or len(topo) != G_.number_of_nodes():
            nodes_with_steps = []
            for n in G_.nodes():
                attrs = G_.nodes[n]
                step_nums = attrs.get(KEY_INITIAL_STEP_NUMBERS, [])
                if step_nums:
                    min_step = min(step_nums)
                else:
                    last_updated = attrs.get(KEY_LAST_UPDATED, "")
                    if last_updated:
                        try:
                            min_step = float(last_updated.replace("T", "").replace(":", "").replace("-", ""))
                        except Exception:
                            min_step = float("inf")
                    else:
                        min_step = float("inf")
                nodes_with_steps.append((min_step, n))
            nodes_with_steps.sort(key=lambda x: (x[0], str(x[1])))
            topo = [n for _, n in nodes_with_steps]
        if len(topo) != G_.number_of_nodes():
            all_nodes = set(G_.nodes())
            topo_set = set(topo)
            missing = all_nodes - topo_set
            if missing:
                missing_sorted = sorted(missing, key=lambda n: (G_.in_degree(n), str(n)))
                topo.extend(missing_sorted)
        if len(topo) != G_.number_of_nodes():
            topo = sorted(G_.nodes(), key=str)
        pos = {}
        node_spacing = 200
        y_pos = 0
        for idx, node in enumerate(topo):
            pos[node] = (idx * node_spacing, y_pos)
        return pos

    def _hierarchy_layout(G_):
        if G_.number_of_nodes() == 0:
            return {}
        layers = {}
        try:
            topo = list(nx.topological_sort(G_))
        except (nx.NetworkXError, nx.NetworkXUnfeasible):
            if G_.number_of_nodes() > 0:
                start_nodes = [n for n in G_.nodes() if G_.in_degree(n) == 0]
                if not start_nodes:
                    start_nodes = [list(G_.nodes())[0]]
                visited = set()
                queue = [(n, 0) for n in start_nodes]
                while queue:
                    node, layer = queue.pop(0)
                    if node in visited:
                        continue
                    visited.add(node)
                    layers[node] = layer
                    for succ in G_.successors(node):
                        if succ not in visited:
                            queue.append((succ, layer + 1))
                for n in G_.nodes():
                    if n not in layers:
                        preds = list(G_.predecessors(n))
                        layers[n] = 0 if not preds else 1 + max(layers.get(p, 0) for p in preds)
            else:
                topo = list(G_.nodes())
                for n in topo:
                    preds = list(G_.predecessors(n))
                    layers[n] = 0 if not preds else 1 + max(layers.get(p, 0) for p in preds)
        else:
            for n in topo:
                preds = list(G_.predecessors(n))
                layers[n] = 0 if not preds else 1 + max(layers.get(p, 0) for p in preds)
        by_layer = {}
        for n, L in layers.items():
            by_layer.setdefault(L, []).append(n)
        max_layer = max(by_layer.keys()) if by_layer else 0
        max_nodes_per_layer = max(len(nodes) for nodes in by_layer.values()) if by_layer else 1
        base_dx = 200 + max_nodes_per_layer * 10
        base_dy = 150 + max_layer * 5
        dx = min(base_dx, 400)
        dy = min(base_dy, 250)
        pos = {}
        for L in sorted(by_layer.keys()):
            nl = sorted(
                by_layer[L],
                key=lambda n: (
                    G_.nodes.get(n, {}).get(KEY_APP_NAME, ""),
                    G_.nodes.get(n, {}).get(KEY_PAGE_NAME, ""),
                    G_.nodes.get(n, {}).get(KEY_PAGE_DESCRIPTION, ""),
                    str(n),
                ),
            )
            nc = len(nl)
            if nc > 8:
                cols = int(math.ceil(math.sqrt(nc)))
                for i, n in enumerate(nl):
                    row = i // cols
                    col = i % cols
                    pos[n] = (L * dx + col * (dx / max(cols, 1)), row * dy)
            else:
                for i, n in enumerate(nl):
                    pos[n] = (L * dx, (i - (nc - 1) / 2) * dy if nc > 1 else 0)
        return pos

    try:
        pos = _linear_layout(G)
        scale = 1
    except (nx.NetworkXError, ValueError):
        try:
            pos = _hierarchy_layout(G)
            scale = 1
        except (nx.NetworkXError, ValueError):
            num_nodes = G.number_of_nodes()
            k = max(2.0, math.sqrt(1.0 / num_nodes) * 100) if num_nodes > 0 else 2.0
            pos = nx.spring_layout(G, k=k, iterations=200, seed=42)
            scale = 1
    nodes = []
    for nid in G.nodes():
        d = G.nodes[nid]
        nid_str = node_id_to_str(nid)
        x, y = pos.get(nid, (0, 0))
        t = d.get(KEY_TASK_ID)
        task_id_list = list(t) if isinstance(t, list) else ([t] if t is not None else [])
        if not task_id_list:
            gt = G.graph.get("task_id")
            task_id_list = list(gt) if isinstance(gt, list) else ([gt] if gt else [])
        nodes.append({
            "id": nid_str,
            "label": _node_label_short(nid),
            "title": _node_label(nid).replace("\n", " "),
            "x": x * scale,
            "y": y * scale,
            "app_name": d.get(KEY_APP_NAME, "") or "",
            "page_name": d.get(KEY_PAGE_NAME, "") or "",
            "page_description": d.get(KEY_PAGE_DESCRIPTION, "") or "",
            "differentiated_content": (d.get(KEY_DIFFERENTIATED_CONTENT) if isinstance(d.get(KEY_DIFFERENTIATED_CONTENT), list) else ([d.get(KEY_DIFFERENTIATED_CONTENT, "")] if d.get(KEY_DIFFERENTIATED_CONTENT) else [])),
            "diff_to_edge_map": d.get(KEY_DIFF_TO_EDGE_MAP, {}),
            "last_updated": d.get(KEY_LAST_UPDATED, ""),
            "success_count": d.get(KEY_SUCCESS_COUNT, 0),
            "existence_probability": d.get(KEY_EXISTENCE_PROBABILITY, 0),
            "initial_step_numbers": d.get(KEY_INITIAL_STEP_NUMBERS, []),
            "task_id": task_id_list,
            "color": (d.get("color") or "").strip() or "",
            "type": (d.get(KEY_NODE_TYPE) or "mid").strip() or "mid",
        })
    edges = []
    for u, v, k in G.edges(keys=True):
        attrs = G[u][v][k]
        u_str, v_str = node_id_to_str(u), node_id_to_str(v)
        action = attrs.get(KEY_ACTION) or {}
        et = attrs.get(KEY_EDGE_TYPE) or ""
        edge_id = attrs.get(KEY_EDGE_ID)
        if not edge_id:
            raise ValueError(f"Edge from {u_str} to {v_str} (key={k}) missing edge_id")
        edges.append({
            "id": edge_id,
            "from": u_str,
            "to": v_str,
            "label": _action_short(action),
            "dashes": et == EDGE_TYPE_DASHED,
            "edge_type": et,
            "action": action,
            "existence_probability": attrs.get(KEY_EDGE_EXISTENCE_PROBABILITY),
            "initial_step_number": attrs.get(KEY_EDGE_INITIAL_STEP_NUMBER),
        })
    return {"nodes": nodes, "edges": edges}


def _merge_for_one_canvas(graph_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """合并所有图为一份 nodes/edges，用于同一画布；网格对齐布局。要求每个结点已有 color。"""
    merged_nodes: list[dict] = []
    merged_edges: list[dict] = []
    if not graph_list:
        return merged_nodes, merged_edges
    node_spacing_x = 200
    node_spacing_y = 250
    start_x = 100
    start_y = 100

    def get_node_order(n):
        step_nums = n.get("initial_step_numbers", [])
        if step_nums:
            return (0, min(step_nums))
        x_pos = n.get("x", 0)
        if x_pos > 0:
            return (1, x_pos)
        last_updated = n.get("last_updated", "")
        if last_updated:
            try:
                time_val = float(last_updated.replace("T", "").replace(":", "").replace("-", ""))
                return (2, time_val)
            except Exception:
                pass
        return (3, hash(n.get("id", "")))

    for i, g in enumerate(graph_list):
        prefix = str(i) + "|"
        nodes = g.get("nodes") or []
        nodes_sorted = sorted(nodes, key=get_node_order)
        for node_idx, n in enumerate(nodes_sorted):
            m = dict(n)
            m["id"] = prefix + n["id"]
            if n.get("color") is None or n.get("color") == "":
                raise ValueError(
                    f"结点缺少 color（图索引 {i}，结点 id: {n.get('id', '?')}）。请重新生成图集。"
                )
            m["color"] = n["color"]
            m["_graph_idx"] = i
            m["x"] = start_x + node_idx * node_spacing_x
            m["y"] = start_y + i * node_spacing_y
            merged_nodes.append(m)
        for e in (g.get("edges") or []):
            e2 = dict(e)
            e2["id"] = prefix + e["id"]
            e2["from"] = prefix + e["from"]
            e2["to"] = prefix + e["to"]
            merged_edges.append(e2)
    return merged_nodes, merged_edges


def load_collection(path: Path) -> list[dict]:
    """加载图集 JSON： [ {"task_id": str, "nodes": [...], "edges": [...]}, ... ]"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("图集 JSON 应为数组，元素形如 { task_id, nodes, edges }")
    return data


def _validate_node_colors(graph_list: list[dict]) -> None:
    """要求每个结点均有 color 字段，否则抛出 ValueError。"""
    for i, g in enumerate(graph_list):
        task_id = g.get("task_id")
        task_str = str(task_id) if task_id is not None else "?"
        nodes = g.get("nodes") or []
        for n in nodes:
            if n.get("color") is None or n.get("color") == "":
                nid = n.get("id", "?")
                raise ValueError(
                    f"图集 JSON 中结点缺少 color 字段（图索引 {i}，task_id: {task_str}，结点 id: {nid}）。请重新点击「生成图集」。"
                )


def build_graph_list_from_nx(graphs: list) -> list[dict]:
    """从 nx.MultiDiGraph 列表构建 graph_list（每项 { task_id, nodes, edges }）。"""
    graph_list = []
    for i, g in enumerate(graphs):
        if g.number_of_nodes() == 0:
            continue
        task_id = g.graph.get("task_id") or f"task_{i}"
        data = _graph_to_vis_data(g)
        graph_list.append({"task_id": task_id, "nodes": data["nodes"], "edges": data["edges"]})
    return graph_list


def graph_list_to_nx(graph_list: list[dict]) -> list[nx.MultiDiGraph]:
    """
    从 graph_list（JSON 格式：每项 { task_id, nodes, edges }）还原为 nx.MultiDiGraph 列表。
    保留结点 id 与属性，用于合并时复用同一批 ID 从而命中相似度缓存。
    """
    out: list[nx.MultiDiGraph] = []
    for item in graph_list:
        G = nx.MultiDiGraph()
        task_id = item.get("task_id")
        if task_id is not None:
            G.graph["task_id"] = task_id
        nodes = item.get("nodes") or []
        for n in nodes:
            nid = n.get("id")
            if nid is None:
                continue
            tid = n.get("task_id")
            task_id_list = list(tid) if isinstance(tid, list) else ([tid] if tid else [])
            raw_diff = n.get("differentiated_content")
            diff_list = raw_diff if isinstance(raw_diff, list) else ([str(raw_diff)] if raw_diff else [])
            diff_map = n.get("diff_to_edge_map", {})
            if not isinstance(diff_map, dict):
                diff_map = {}
            node_type = (n.get("type") or "mid").strip() or "mid"
            attrs = {
                KEY_APP_NAME: n.get("app_name") or "",
                KEY_PAGE_NAME: n.get("page_name") or "",
                KEY_PAGE_DESCRIPTION: n.get("page_description") or "",
                KEY_DIFFERENTIATED_CONTENT: diff_list,
                KEY_DIFF_TO_EDGE_MAP: diff_map,
                KEY_LAST_UPDATED: n.get("last_updated") or "",
                KEY_SUCCESS_COUNT: int(n.get("success_count", 0)),
                KEY_EXISTENCE_PROBABILITY: float(n.get("existence_probability", 0)),
                KEY_INITIAL_STEP_NUMBERS: list(n.get("initial_step_numbers") or []),
                KEY_TASK_ID: task_id_list,
                KEY_NODE_TYPE: node_type,
                "color": (n.get("color") or "").strip() or "",
            }
            G.add_node(nid, **attrs)
        edges = item.get("edges") or []
        for e in edges:
            u, v = e.get("from"), e.get("to")
            if u is None or v is None:
                continue
            edge_id = e.get("id")
            if not edge_id:
                raise ValueError(f"Edge from {u} to {v} missing id field")
            et = (e.get("edge_type") or "").strip().lower()
            if et != EDGE_TYPE_DASHED:
                et = EDGE_TYPE_SOLID
            edge_attrs = {
                KEY_EDGE_ID: edge_id,
                KEY_EDGE_TYPE: et,
                KEY_ACTION: e.get("action") or {},
                KEY_EDGE_EXISTENCE_PROBABILITY: e.get("existence_probability"),
                KEY_EDGE_INITIAL_STEP_NUMBER: e.get("initial_step_number"),
            }
            G.add_edge(u, v, **edge_attrs)
        if G.number_of_nodes() > 0:
            out.append(G)
    return out


def build_graph_payload(graph_list: list[dict]) -> dict:
    """对 graph_list 合并为画布数据，返回 { graphList, mergedNodes, mergedEdges }。要求每个结点已有 color。"""
    _validate_node_colors(graph_list)
    merged_nodes, merged_edges = _merge_for_one_canvas(graph_list)
    return {"graphList": graph_list, "mergedNodes": merged_nodes, "mergedEdges": merged_edges}


def get_graph_collection_response(
    root: Path,
    variant: str = "unmerged",
    index: str = "all",
) -> dict:
    """
    从工程目录读取图集 JSON，着色合并后返回前端所需 payload。
    variant: "unmerged" -> graph_collection.json, "merged" -> graph_collection_merged.json
    index: "all" -> 全部子图；"0","1",... -> 只返回该序号的一张图。
    返回含 totalCount（该 variant 下子图总数，用于前端下拉）。
    """
    root = Path(root)
    name = "graph_collection_merged.json" if variant == "merged" else "graph_collection.json"
    path = root / name
    if not path.is_file():
        return {
            "error": f"图集文件不存在: {path}",
            "graphList": [],
            "mergedNodes": [],
            "mergedEdges": [],
            "totalCount": 0,
        }
    try:
        graph_list = load_collection(path)
    except Exception as e:
        return {
            "error": str(e),
            "graphList": [],
            "mergedNodes": [],
            "mergedEdges": [],
            "totalCount": 0,
        }
    if not graph_list:
        return {"graphList": [], "mergedNodes": [], "mergedEdges": [], "totalCount": 0}
    total_count = len(graph_list)
    if index != "all":
        try:
            i = int(index)
            if 0 <= i < total_count:
                graph_list = [graph_list[i]]
            # else 保持全部
        except ValueError:
            pass
    try:
        out = build_graph_payload(graph_list)
    except ValueError as e:
        return {
            "error": str(e),
            "graphList": [],
            "mergedNodes": [],
            "mergedEdges": [],
            "totalCount": total_count,
        }
    out["totalCount"] = total_count
    return out


def save_graph_collection(path: Path, graph_list: list[dict]) -> None:
    """将 graph_list 写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph_list, f, ensure_ascii=False, indent=2)


def load_graph_collection(path: Path) -> list[dict] | None:
    """从 JSON 文件加载 graph_list；文件不存在或解析失败返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def _to_single_string(val, max_len: int | None = 80) -> str:
    """将 list/str 转为单字符串；max_len 为 None 时不截断。"""
    if val is None:
        return ""
    if isinstance(val, list):
        parts = []
        for x in val:
            s = (x if isinstance(x, str) else str(x)).strip()
            if s:
                parts.append(s)
        s = "；".join(parts) if parts else ""
    else:
        s = str(val).strip()
    if max_len is not None and len(s) > max_len:
        return s[: max_len - 3].rstrip() + "…"
    return s


def simplify_path_data(path_data: dict) -> dict:
    """
    从完整路径 JSON 中提取最简信息，供大模型使用。
    保留：每步的 app_name、page_name、page_description（可截断）；
    边的 edge_id（前端传的是 id）；差异化内容只保留一份，从 path_nodes[i].differentiated_content 原样拷贝到 edge.differentiated_content（列表）。
    去掉：node.differentiated_content（冗余）、action_type、timestamp 等。
    """
    path_nodes = path_data.get("path_nodes") or []
    edges_segments = path_data.get("edges") or []
    out_steps = []
    for i in range(len(path_nodes) - 1):
        node = path_nodes[i]
        if not isinstance(node, dict):
            continue
        seg = edges_segments[i] if i < len(edges_segments) else {}
        edge_list = seg.get("edges") or []

        app_name = (node.get("app_name") or "").strip()
        page_name = (node.get("page_name") or "").strip()
        page_desc = _to_single_string(node.get("page_description"), 80)
        # 差异化内容：完整 JSON 里是列表，原样拷贝到 edge.differentiated_content
        diff_content = node.get("differentiated_content")
        if isinstance(diff_content, list):
            diff_list = [str(x).strip() for x in diff_content if x is not None and str(x).strip()]
        elif diff_content is not None and str(diff_content).strip():
            diff_list = [str(diff_content).strip()]
        else:
            diff_list = []

        edge_ids = [
            (edge_obj.get("id") or edge_obj.get("edge_id") or "").strip()
            for edge_obj in edge_list
        ]

        out_steps.append({
            "step": i + 1,
            "node": {
                "app_name": app_name,
                "page_name": page_name,
                "page_description": page_desc,
            },
            "edge": {
                "edge_id": edge_ids,
                "differentiated_content": diff_list,
            },
        })

    return {"path_summary": out_steps}


def save_path_data(project_root: Path, path_data: dict) -> dict:
    """
    保存路径数据到JSON文件。
    文件保存在工程目录下的 path 子目录，文件名格式：path_YYYYMMDDHHMMSS.json
    
    参数:
        project_root: 工程目录路径
        path_data: 路径数据字典，包含 from_node_id, to_node_id, path_nodes, edges
    
    返回:
        {"ok": True, "file_path": "文件路径", "message": "保存成功"} 或错误信息
    """
    from datetime import datetime
    
    project_root = Path(project_root)
    if not project_root.is_dir():
        return {"ok": False, "error": f"工程目录不存在: {project_root}"}
    
    # 保存到根目录的 path 子目录下
    path_dir = project_root / "path"
    path_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"path_{timestamp}.json"
    file_path = path_dir / filename
    
    # 添加时间戳到数据中
    path_data_with_timestamp = dict(path_data)
    path_data_with_timestamp["timestamp"] = datetime.now().isoformat()
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(path_data_with_timestamp, f, ensure_ascii=False, indent=2)
        # 生成简化版，供大模型使用：同名文件追加 _simplify
        simplify_path = path_dir / f"path_{timestamp}_simplify.json"
        try:
            simplified = simplify_path_data(path_data_with_timestamp)
            with open(simplify_path, "w", encoding="utf-8") as f:
                json.dump(simplified, f, ensure_ascii=False, indent=2)
        except (OSError, TypeError, ValueError, KeyError):
            pass  # 简化版失败不影响主流程
        return {
            "ok": True,
            "file_path": str(file_path),
            "message": f"路径已保存到 path/{filename}",
        }
    except (OSError, TypeError, ValueError) as e:
        return {"ok": False, "error": f"保存文件失败: {str(e)}"}


def execute_replay_sequence(sequence: list[dict], delay: float = 2.0) -> dict:
    """执行重放序列：写临时 JSON，子进程调用 run_sequence.py，后台清理。立即返回。"""
    import subprocess
    import tempfile

    _root = Path(__file__).resolve().parent.parent
    temp_dir = _root / "graph_build"
    temp_dir.mkdir(exist_ok=True)
    temp_file = None
    try:
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            dir=str(temp_dir),
            encoding="utf-8",
        )
        json.dump(sequence, temp_file, ensure_ascii=False, indent=2)
        temp_file.close()
        temp_file_path = temp_file.name
        script_path = _root / "graph_build" / "run_sequence.py"
        if not script_path.exists():
            return {"success": False, "error": f"脚本文件不存在: {script_path}"}
        autoglm_path = _root / "AutoGLM"
        if not autoglm_path.is_dir():
            return {
                "success": False,
                "error": f"缺少必要的目录: {autoglm_path}\n请确保 AutoGLM 目录存在。",
            }
        phone_agent_path = autoglm_path / "phone_agent"
        if not phone_agent_path.is_dir():
            return {
                "success": False,
                "error": f"缺少必要的模块目录: {phone_agent_path}\n请确保 AutoGLM/phone_agent 目录存在。",
            }
        cmd = [sys.executable, str(script_path), temp_file_path, "--delay", str(delay)]
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_root),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
            creationflags=creation_flags if sys.platform == "win32" else 0,
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )

        def cleanup_and_log():
            try:
                output_lines = []
                error_lines = []

                def read_stdout():
                    try:
                        for line in iter(process.stdout.readline, ""):
                            if not line:
                                break
                            output_lines.append(line.rstrip())
                            print(f"[重放] {line.rstrip()}", flush=True)
                    except Exception as e:
                        print(f"[重放] 读取 stdout 失败: {e}", flush=True)

                def read_stderr():
                    try:
                        for line in iter(process.stderr.readline, ""):
                            if not line:
                                break
                            error_lines.append(line.rstrip())
                            print(f"[重放错误] {line.rstrip()}", file=sys.stderr, flush=True)
                    except Exception as e:
                        print(f"[重放错误] 读取 stderr 失败: {e}", file=sys.stderr, flush=True)

                stdout_thread = threading.Thread(target=read_stdout, daemon=True)
                stderr_thread = threading.Thread(target=read_stderr, daemon=True)
                stdout_thread.start()
                stderr_thread.start()
                process.wait()
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                try:
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.unlink(temp_file_path)
                except Exception as e:
                    print(f"清理临时文件失败: {e}", file=sys.stderr)
            except Exception as e:
                print(f"重放进程监控失败: {e}", file=sys.stderr)

        threading.Thread(target=cleanup_and_log, daemon=True).start()
        return {
            "success": True,
            "message": f"重放任务已启动（PID: {process.pid}）",
            "total_steps": len(sequence),
            "process_id": process.pid,
        }
    except Exception as e:
        if temp_file and os.path.exists(getattr(temp_file, "name", "")):
            try:
                os.unlink(temp_file.name)
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def generate_graph_collection(project_root: Path) -> dict:
    """
    根据工程目录下 task_list.csv 中 status=success 的任务建图，仅生成未合并图集 JSON：
    graph_collection.json。不执行合并。
    返回 { "ok": True, "message": "..." } 或 { "ok": False, "error": "..." }。
    """
    project_root = Path(project_root)
    if not project_root.is_dir():
        return {"ok": False, "error": f"工程目录不存在: {project_root}"}
    try:
        from graph_build.graph_collection import (
            build_graph_collection,
            get_success_task_ids,
        )
    except ImportError as e:
        return {"ok": False, "error": f"无法导入图集构建模块: {e}"}
    success_ids = get_success_task_ids(project_root)
    if not success_ids:
        return {"ok": False, "error": "task_list.csv 中无 status=success 的任务，无法建图。请先在任务收集中运行并完成部分任务。"}
    graphs = build_graph_collection(project_root)
    if not graphs:
        return {"ok": False, "error": "没有构建出有效图（可能任务目录或 steps 缺失）。"}
    graph_list = build_graph_list_from_nx(graphs)
    _assign_unmerged_colors(graph_list)
    path_unmerged = project_root / "graph_collection.json"
    save_graph_collection(path_unmerged, graph_list)
    try:
        stats_data = _build_pre_merge_stats(graph_list)
        _write_stats_json(_stats_json_path(project_root), stats_data)
    except (OSError, TypeError, ValueError) as e:
        print(f"[图集统计] 写入 JSON 失败（不影响图集生成）: {e}", file=sys.stderr)
    return {
        "ok": True,
        "message": f"已生成未合并图集 {len(graph_list)} 张 → {path_unmerged.name}。请点击「合并图集」生成合并结果。",
    }


def merge_graph_collection_api(project_root: Path, merge_threshold: float = 0.9) -> dict:
    """
    基于已有 graph_collection.json 直接加载图并合并，生成 graph_collection_merged.json。
    不重新建图，结点 ID 与 JSON 一致，从而相似度缓存可命中。
    需先执行「生成图集」；合并时从未合并图集读取 task→颜色 映射以赋色。
    返回 { "ok": True, "message": "..." } 或 { "ok": False, "error": "..." }。
    """
    project_root = Path(project_root)
    if not project_root.is_dir():
        return {"ok": False, "error": f"工程目录不存在: {project_root}"}
    path_unmerged = project_root / "graph_collection.json"
    unmerged_list = load_graph_collection(path_unmerged)
    if not unmerged_list:
        return {"ok": False, "error": "未找到未合并图集（graph_collection.json）。请先点击「生成图集」。"}
    try:
        from graph_build.graph_collection import merge_graph_collection
    except ImportError as e:
        return {"ok": False, "error": f"无法导入图集构建模块: {e}"}
    graphs = graph_list_to_nx(unmerged_list)
    if not graphs:
        return {"ok": False, "error": "未合并图集 JSON 中无有效图。请先重新「生成图集」。"}
    task_to_color = _task_to_color_from_unmerged(unmerged_list)
    total_pre_nodes = sum(len(g.get("nodes") or []) for g in unmerged_list)
    total_pre_edges = sum(len(g.get("edges") or []) for g in unmerged_list)
    t0 = time.time()
    graphs_merged = merge_graph_collection(
        graphs, merge_threshold=merge_threshold, project_root=project_root
    )
    merge_time_s = round(time.time() - t0, 2)
    total_merged_nodes = sum(G.number_of_nodes() for G in graphs_merged)
    total_merged_edges = sum(G.number_of_edges() for G in graphs_merged)
    ncr = round(total_merged_nodes / total_pre_nodes, 4) if total_pre_nodes else 0.0
    ecr = round(total_merged_edges / total_pre_edges, 4) if total_pre_edges else 0.0
    try:
        path_stats = _stats_json_path(project_root)
        if path_stats.is_file():
            with open(path_stats, "r", encoding="utf-8") as f:
                stats_data = json.load(f)
        else:
            stats_data = _build_pre_merge_stats(unmerged_list)
        merge_stats = _build_merge_stats(
            graphs_merged,
            total_merged_nodes,
            total_merged_edges,
            merge_time_s,
            ncr,
            ecr,
        )
        stats_data.update(merge_stats)
        _write_stats_json(path_stats, stats_data)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
        print(f"[图集统计] 更新合并统计失败（不影响合并结果）: {e}", file=sys.stderr)
    graph_list_merged = build_graph_list_from_nx(graphs_merged)
    _assign_merged_colors(graph_list_merged, task_to_color)
    path_merged = project_root / "graph_collection_merged.json"
    save_graph_collection(path_merged, graph_list_merged)
    
    # 合并完成后，分析完整任务路径并写入统计文件
    try:
        analyze_result = analyze_complete_task_paths(project_root)
        if not analyze_result.get("ok"):
            print(f"[路径统计] 分析路径失败（不影响合并结果）: {analyze_result.get('error', '未知错误')}", file=sys.stderr)
    except Exception as e:
        print(f"[路径统计] 分析路径时出错（不影响合并结果）: {e}", file=sys.stderr)
    
    return {
        "ok": True,
        "message": f"已合并图集 → {path_merged.name}，共 {len(graph_list_merged)} 张图。",
    }


def incremental_merge_graph_collection_api(
    base_root: Path,
    incr_root: Path,
    merge_threshold: float = 0.9,
) -> dict:
    """
    将增量数据工程的图集（graph_collection.json）合并入基础数据工程的合并图（graph_collection_merged.json）。
    只改写 base_root/graph_collection_merged.json，其他文件不动。
    返回 { "ok": True, "message": "..." } 或 { "ok": False, "error": "..." }。
    """
    base_root = Path(base_root)
    incr_root = Path(incr_root)
    if not base_root.is_dir():
        return {"ok": False, "error": f"基础工程目录不存在: {base_root}"}
    if not incr_root.is_dir():
        return {"ok": False, "error": f"增量工程目录不存在: {incr_root}"}
    path_merged = base_root / "graph_collection_merged.json"
    path_incr = incr_root / "graph_collection.json"
    if not path_merged.is_file():
        return {"ok": False, "error": "基础工程下无 graph_collection_merged.json，请先合并基础图集。"}
    if not path_incr.is_file():
        return {"ok": False, "error": "增量工程下无 graph_collection.json。"}
    list_base = load_graph_collection(path_merged)
    list_incr = load_graph_collection(path_incr)
    if not list_base:
        return {"ok": False, "error": "基础合并图集为空。"}
    if not list_incr:
        return {"ok": False, "error": "增量图集为空。"}
    try:
        from graph_build.graph_collection import merge_graph_collection
    except ImportError as e:
        return {"ok": False, "error": f"无法导入图集构建模块: {e}"}
    graphs_base = graph_list_to_nx(list_base)
    graphs_incr = graph_list_to_nx(list_incr)
    if not graphs_base or not graphs_incr:
        return {"ok": False, "error": "图集 JSON 转 nx 后无有效图。"}
    graphs_combined = graphs_base + graphs_incr
    task_to_color = _task_to_color_from_unmerged(list_base + list_incr)
    graphs_merged = merge_graph_collection(
        graphs_combined, merge_threshold=merge_threshold, project_root=base_root
    )
    graph_list_merged = build_graph_list_from_nx(graphs_merged)
    _assign_merged_colors(graph_list_merged, task_to_color)
    save_graph_collection(path_merged, graph_list_merged)
    
    # 增量合并完成后，分析完整任务路径并写入统计文件
    try:
        analyze_result = analyze_complete_task_paths(base_root)
        if not analyze_result.get("ok"):
            print(f"[路径统计] 分析路径失败（不影响合并结果）: {analyze_result.get('error', '未知错误')}", file=sys.stderr)
    except Exception as e:
        print(f"[路径统计] 分析路径时出错（不影响合并结果）: {e}", file=sys.stderr)
    
    return {
        "ok": True,
        "message": f"已把增量图集合并入基础合并图 → {path_merged.name}，共 {len(graph_list_merged)} 张图。",
    }


def _expand_path_with_multiple_edges(
    G: nx.MultiDiGraph, node_path: list
) -> list[list[tuple]]:
    """
    展开路径中相邻节点之间的多条边组合。
    
    输入：
        G: NetworkX MultiDiGraph
        node_path: 节点序列，如 [A, B, C]
    
    输出：
        路径列表，每条路径是边的序列，如 [[(A,B,key1), (B,C,key2)], [(A,B,key1), (B,C,key3)], ...]
        对于 A->B 有3条边，B->C 有5条边的情况，会输出 3×5=15 条路径
    """
    if len(node_path) < 2:
        return []
    
    # 收集每段路径的所有边
    edge_segments = []
    for i in range(len(node_path) - 1):
        u, v = node_path[i], node_path[i + 1]
        # 获取 u->v 之间的所有边（包括 key）。G.edges(u, v, keys=True) 中 v 会被当作 data 参数，需用邻接结构取边
        if v not in G[u]:
            edges_between = []
        else:
            edges_between = [(u, v, k) for k in G[u][v]]
        if not edges_between:
            return []  # 如果某段没有边，整个路径无效
        edge_segments.append(edges_between)
    
    # 使用笛卡尔积展开所有边的组合
    from itertools import product
    
    expanded_paths = []
    for edge_combination in product(*edge_segments):
        expanded_paths.append(list(edge_combination))
    
    return expanded_paths


def _find_complete_task_paths(graphs_merged: list[nx.MultiDiGraph]) -> list[dict]:
    """
    从合并后的图集中找到所有完整的任务路径。
    
    步骤：
    1. 找到只有出度没有入度的节点作为起始点
    2. 找到只有入度没有出度的节点作为任务终点
    3. 寻找任意起点到终点的所有简单路径
    4. 展开路径中相邻节点之间的多条边组合
    
    输入：
        graphs_merged: 合并后的 NetworkX MultiDiGraph 列表
    
    输出：
        路径列表，每个元素包含：
        {
            "graph_index": int,  # 图在列表中的索引
            "start_node": str,   # 起始节点ID
            "end_node": str,     # 终止节点ID
            "node_path": list[str],  # 节点序列
            "expanded_paths": list[list[tuple]],  # 展开后的边路径列表
            "path_count": int,   # 展开后的路径数量
        }
    """
    all_paths = []
    
    for graph_idx, G in enumerate(graphs_merged):
        if G.number_of_nodes() == 0:
            continue
        
        # 1. 起点：依赖结点 type（原始操作链起点）
        start_nodes = [n for n in G.nodes() if (G.nodes[n].get(KEY_NODE_TYPE) or "").strip() == NODE_TYPE_START]
        # 2. 终点：依赖结点 type（原始操作链终点）
        end_nodes = [n for n in G.nodes() if (G.nodes[n].get(KEY_NODE_TYPE) or "").strip() == NODE_TYPE_END]
        
        # 如果没有起点或终点，跳过这个图
        if not start_nodes or not end_nodes:
            continue
        
        # 3. 对每个起点-终点对，找到所有简单路径
        for start in start_nodes:
            for end in end_nodes:
                if start == end:
                    continue
                
                try:
                    # 使用 NetworkX 的 all_simple_paths 找到所有简单路径。
                    # MultiDiGraph 下同一节点序列会因平行边被返回多次，需按节点序列去重后再展开。
                    max_path_length = min(G.number_of_nodes() * 2, 100)
                    simple_paths = list(
                        nx.all_simple_paths(G, start, end, cutoff=max_path_length)
                    )
                    seen_node_sequence = set()
                    for node_path in simple_paths:
                        key = tuple(node_path)
                        if key in seen_node_sequence:
                            continue
                        seen_node_sequence.add(key)
                        # 4. 展开路径中相邻节点之间的多条边组合
                        expanded_paths = _expand_path_with_multiple_edges(G, node_path)
                        
                        if expanded_paths:
                            # 将边路径转换为可序列化的格式
                            serialized_paths = []
                            for path in expanded_paths:
                                serialized_path = []
                                for u, v, k in path:
                                    # 边的 key 可能是整数或字符串，统一转为字符串
                                    serialized_path.append((str(u), str(v), str(k)))
                                serialized_paths.append(serialized_path)
                            
                            all_paths.append({
                                "graph_index": graph_idx,
                                "start_node": str(start),
                                "end_node": str(end),
                                "node_path": [str(n) for n in node_path],
                                "expanded_paths": serialized_paths,
                                "path_count": len(expanded_paths),
                            })
                except (nx.NetworkXNoPath, nx.NetworkXError):
                    # 如果找不到路径，跳过
                    continue
    
    return all_paths


def analyze_complete_task_paths(project_root: Path) -> dict:
    """
    分析合并后的图集，统计能生成多少条完整的任务路径，并将结果写入统计文件。
    
    输入：
        project_root: 工程根目录
    
    返回：
        { "ok": True, "data": {...} } 或 { "ok": False, "error": "..." }
    """
    project_root = Path(project_root)
    if not project_root.is_dir():
        return {"ok": False, "error": f"工程目录不存在: {project_root}"}
    
    # 读取合并后的图集
    path_merged = project_root / "graph_collection_merged.json"
    if not path_merged.is_file():
        return {"ok": False, "error": "未找到合并图集（graph_collection_merged.json）。请先合并图集。"}
    
    try:
        merged_list = load_graph_collection(path_merged)
        if not merged_list:
            return {"ok": False, "error": "合并图集为空。"}
        
        # 转换为 NetworkX 图
        graphs_merged = graph_list_to_nx(merged_list)
        if not graphs_merged:
            return {"ok": False, "error": "合并图集转 nx 后无有效图。"}
        
        # 统计各图起点、终点个数（依赖结点 type，不再用度数）
        total_start_nodes = 0
        total_end_nodes = 0
        for G in graphs_merged:
            for node in G.nodes():
                t = (G.nodes[node].get(KEY_NODE_TYPE) or "").strip()
                if t == NODE_TYPE_START:
                    total_start_nodes += 1
                if t == NODE_TYPE_END:
                    total_end_nodes += 1

        # 查找所有完整任务路径
        all_paths = _find_complete_task_paths(graphs_merged)
        
        # 统计信息
        total_paths = sum(p["path_count"] for p in all_paths)
        total_node_paths = len(all_paths)
        paths_by_graph = {}
        for path_info in all_paths:
            graph_idx = path_info["graph_index"]
            if graph_idx not in paths_by_graph:
                paths_by_graph[graph_idx] = {
                    "node_path_count": 0,
                    "expanded_path_count": 0,
                }
            paths_by_graph[graph_idx]["node_path_count"] += 1
            paths_by_graph[graph_idx]["expanded_path_count"] += path_info["path_count"]
        
        # 构建结果数据（不写入 all_paths 详情）
        result_data = {
            "total_node_paths": total_node_paths,  # 节点路径数量（未展开边）
            "total_expanded_paths": total_paths,  # 展开后的完整路径数量
            "start_node_count": total_start_nodes,  # 起点个数
            "end_node_count": total_end_nodes,  # 终点个数
            "paths_by_graph": paths_by_graph,  # 每个图的路径统计
        }

        # 更新统计文件
        try:
            path_stats = _stats_json_path(project_root)
            if path_stats.is_file():
                with open(path_stats, "r", encoding="utf-8") as f:
                    stats_data = json.load(f)
            else:
                stats_data = {}

            # 更新路径统计信息
            stats_data["complete_task_paths"] = result_data

            _write_stats_json(path_stats, stats_data)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            print(f"[路径统计] 更新统计文件失败: {e}", file=sys.stderr)
        
        return {
            "ok": True,
            "data": {
                "total_node_paths": total_node_paths,
                "total_expanded_paths": total_paths,
                "paths_by_graph": paths_by_graph,
            },
        }
    
    except Exception as e:
        return {"ok": False, "error": f"分析路径时出错: {str(e)}"}