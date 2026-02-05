"""
从一个任务生成一张图：读取 task_xxx/steps/*.json，按顺序建结点与边。
在项目根目录运行：python graph_build/task_graph.py <任务目录或 task_id>
  例：python graph_build/task_graph.py autoglm_runs/task_0001
  例：python graph_build/task_graph.py task_0001  （使用默认 autoglm_runs）
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 以脚本方式运行时保证项目根在 path 中，才能 import graph_build
_root = Path(__file__).resolve().parent.parent
if _root not in sys.path:
    sys.path.insert(0, str(_root))

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
    KEY_EDGE_SUCCESS_COUNT,
    KEY_EDGE_TYPE,
    KEY_EXISTENCE_PROBABILITY,
    KEY_INITIAL_STEP_NUMBERS,
    KEY_LAST_UPDATED,
    KEY_NODE_TYPE,
    KEY_PAGE_DESCRIPTION,
    KEY_PAGE_NAME,
    KEY_DIFFERENTIATED_CONTENT,
    KEY_SUCCESS_COUNT,
    NODE_TYPE_END,
    NODE_TYPE_MID,
    NODE_TYPE_START,
    generate_global_id,
    posterior_edge_existence_probability,
    posterior_node_existence_probability,
)
# 单图展示已迁至 Web 控制台：保存 JSON 后打开 app 回滚攻击页


def _copy_action(step: dict[str, Any]) -> dict[str, Any]:
    """取 step 的完整 action 字典副本，用于边上的重放。"""
    action = step.get("action")
    if isinstance(action, dict):
        return dict(action)
    return {}


def load_steps_from_task(runs_dir: Path, task_id: str) -> list[dict[str, Any]]:
    """
    从 runs_dir/task_xxx/steps/ 读取所有 step_xxx.json，按 step_number 排序返回。
    """
    steps_dir = runs_dir / task_id / "steps"
    if not steps_dir.is_dir():
        return []
    steps = []
    for p in steps_dir.glob("step_*.json"):
        try:
            num = int(p.stem.replace("step_", ""))
        except ValueError:
            continue
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["_step_number"] = num
        steps.append(data)
    steps.sort(key=lambda x: x["_step_number"])
    return steps


def build_graph_from_steps(steps: list[dict[str, Any]]) -> nx.MultiDiGraph:
    """
    根据一个任务的步骤列表建图。使用 MultiDiGraph，同一对结点间允许多条边（每条边一个操作）。
    - 结点键：全局唯一 ID（时间戳+随机）；属性保留 app_name, page_name, page_description, differentiated_content 及 last_updated 等。
    - 边：每条边表示「从上一结点到当前结点」的转移，挂的是**上一步**的 action。
    - 若某步缺 page_name 则跳过该步不连边。本任务内相同通用描述 (app_name, page_name, page_description) 共用一个结点 ID。
    """
    G = nx.MultiDiGraph()
    # 本任务内：结点描述 (app_name, page_name, page_description) -> 结点 ID（去重用）
    seen_description_to_id: dict[tuple[str, str, str], str] = {}
    prev_key: str | None = None
    prev_page: str | None = None
    prev_action = None
    prev_step_number = None
    total_node_visits = 0
    for step in steps:
        # 暂时用 step_xxx.json 原来自带的 current_app 作为 app name，后续可改为用 app_name
        app_name = (step.get("current_app") or step.get("app_name") or "").strip()
        page = (step.get("page_name") or "").strip()
        page_desc = (step.get("page_description") or "").strip()
        raw_diff = step.get("differentiated_content")
        if isinstance(raw_diff, list):
            diff_content = [str(x).strip() for x in raw_diff if x is not None and str(x).strip()]
        else:
            s = (raw_diff or "").strip()
            diff_content = [s] if s else []
        if not page:
            prev_key = None
            prev_page = None
            prev_action = None
            prev_step_number = None
            continue
        node_description = (app_name, page, page_desc)
        ts = (step.get("timestamp") or "").strip()
        step_num = step.get("_step_number", step.get("step_number"))
        node_key = seen_description_to_id.setdefault(node_description, generate_global_id())
        if node_key not in G:
            G.add_node(
                node_key,
                **{
                    KEY_APP_NAME: app_name,
                    KEY_PAGE_NAME: page,
                    KEY_PAGE_DESCRIPTION: page_desc,
                    KEY_DIFFERENTIATED_CONTENT: diff_content,
                    KEY_DIFF_TO_EDGE_MAP: {},
                    KEY_LAST_UPDATED: ts,
                    KEY_SUCCESS_COUNT: 1,
                    KEY_EXISTENCE_PROBABILITY: 0.0,
                    KEY_INITIAL_STEP_NUMBERS: [step_num] if step_num is not None else [],
                    KEY_NODE_TYPE: NODE_TYPE_MID,
                },
            )
            total_node_visits += 1
        else:
            G.nodes[node_key][KEY_SUCCESS_COUNT] += 1
            G.nodes[node_key][KEY_LAST_UPDATED] = ts
            if step_num is not None:
                G.nodes[node_key][KEY_INITIAL_STEP_NUMBERS].append(step_num)
            total_node_visits += 1
        if prev_key is not None and prev_page is not None:
            same_page = prev_page == page
            edge_type = EDGE_TYPE_DASHED if same_page else EDGE_TYPE_SOLID
            action_dict = prev_action or {}
            # 生成边ID
            edge_id = generate_global_id()
            G.add_edge(
                prev_key,
                node_key,
                **{
                    KEY_EDGE_ID: edge_id,
                    KEY_EDGE_TYPE: edge_type,
                    KEY_ACTION: action_dict,
                    KEY_EDGE_SUCCESS_COUNT: 1,
                    KEY_EDGE_EXISTENCE_PROBABILITY: 0.0,
                    KEY_EDGE_INITIAL_STEP_NUMBER: prev_step_number,
                },
            )
            # 建立差异化内容到边的映射
            diff_map = G.nodes[node_key].get(KEY_DIFF_TO_EDGE_MAP, {})
            for diff_item in diff_content:
                if diff_item and diff_item.strip():
                    diff_str = diff_item.strip()
                    # 如果同一个差异化内容对应不同的边，只保留第一个（先到先得）
                    if diff_str not in diff_map:
                        diff_map[diff_str] = edge_id
            G.nodes[node_key][KEY_DIFF_TO_EDGE_MAP] = diff_map
        prev_key = node_key
        prev_page = page
        prev_action = _copy_action(step)
        prev_step_number = step_num
    for node_key in G.nodes():
        G.nodes[node_key][KEY_INITIAL_STEP_NUMBERS].sort()
    if total_node_visits > 0:
        for node_key in G.nodes():
            c = G.nodes[node_key][KEY_SUCCESS_COUNT]
            G.nodes[node_key][KEY_EXISTENCE_PROBABILITY] = posterior_node_existence_probability(
                c, total_visits=total_node_visits
            )
    total_edges = G.number_of_edges()
    if total_edges > 0:
        total_edge_visits = sum(
            G[u][v][key].get(KEY_EDGE_SUCCESS_COUNT, 0) for u, v, key in G.edges(keys=True)
        )
        for u, v, k in G.edges(keys=True):
            c = G[u][v][k].get(KEY_EDGE_SUCCESS_COUNT, 0)
            G[u][v][k][KEY_EDGE_EXISTENCE_PROBABILITY] = posterior_edge_existence_probability(
                c, total_edge_visits=total_edge_visits
            )
    # 按入度/出度标记起点、终点（原始操作链）
    for node_key in G.nodes():
        in_d = G.in_degree(node_key)
        out_d = G.out_degree(node_key)
        if in_d == 0 and out_d > 0:
            G.nodes[node_key][KEY_NODE_TYPE] = NODE_TYPE_START
        elif out_d == 0 and in_d > 0:
            G.nodes[node_key][KEY_NODE_TYPE] = NODE_TYPE_END
        else:
            G.nodes[node_key][KEY_NODE_TYPE] = NODE_TYPE_MID
    return G


def _merge_redundant_launch_same_page(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    若某条边为 Launch 且前后结点 app_name 相同，则合并两结点并删除该 Launch 边。
    保持合并后结点属性（步号、访问次数等）合并；重定向所有与 v 相连的边到 u 后删除 v。
    """
    edges_launch_same: list[tuple[Any, Any, Any]] = []  # (u, v, key)
    merge_target: dict[Any, Any] = {}  # v -> u（将 v 合并进 u）
    for u, v, k in list(G.edges(keys=True)):
        attrs = G[u][v][k]
        action = attrs.get(KEY_ACTION) or {}
        act = (action.get("action") or action.get("action_type") or "").strip()
        if act.lower() != "launch":
            continue
        app_u = (G.nodes[u].get(KEY_APP_NAME) or "").strip()
        app_v = (G.nodes[v].get(KEY_APP_NAME) or "").strip()
        if app_u != app_v:
            continue
        edges_launch_same.append((u, v, k))
        # 同一对结点只保留一个合并方向（id 小的合并进 id 大的），避免 A↔B 双向 Launch 时 merge_dag 成环
        small, large = (u, v) if str(u) < str(v) else (v, u)
        # start 与 end 不能合并
        t_small = G.nodes[small].get(KEY_NODE_TYPE) or NODE_TYPE_MID
        t_large = G.nodes[large].get(KEY_NODE_TYPE) or NODE_TYPE_MID
        if (t_small == NODE_TYPE_START and t_large == NODE_TYPE_END) or (t_small == NODE_TYPE_END and t_large == NODE_TYPE_START):
            continue
        if small not in merge_target:
            merge_target[small] = large

    if not merge_target:
        return G

    # 拓扑序：若 v 合并进 u，则先处理“被合并进 v”的结点，再处理 v
    merge_dag = nx.DiGraph()
    for v, u in merge_target.items():
        merge_dag.add_edge(v, u)
    try:
        topo_order = list(nx.topological_sort(merge_dag))
    except nx.NetworkXError:
        topo_order = list(merge_target.keys())

    for n in topo_order:
        if n not in merge_target:
            continue
        v, u = n, merge_target[n]
        if v not in G.nodes() or u not in G.nodes():
            continue
        # 合并结点属性到 u：前三个属性取 u 的即可；差异化内容为数组，追加合并
        nu, nv = G.nodes[u], G.nodes[v]
        steps_u = list(nu.get(KEY_INITIAL_STEP_NUMBERS) or [])
        steps_v = list(nv.get(KEY_INITIAL_STEP_NUMBERS) or [])
        G.nodes[u][KEY_INITIAL_STEP_NUMBERS] = sorted(set(steps_u + steps_v))
        G.nodes[u][KEY_SUCCESS_COUNT] = (nu.get(KEY_SUCCESS_COUNT) or 0) + (nv.get(KEY_SUCCESS_COUNT) or 0)
        if nv.get(KEY_LAST_UPDATED):
            G.nodes[u][KEY_LAST_UPDATED] = nv.get(KEY_LAST_UPDATED)
        # app_name / page_name / page_description 保留 u 的，不覆盖
        # 差异化内容：规范为列表后追加
        def _diff_to_list(x):
            if isinstance(x, list):
                return [str(i).strip() for i in x if i is not None and str(i).strip()]
            s = (x or "").strip() if isinstance(x, str) else ""
            return [s] if s else []
        G.nodes[u][KEY_DIFFERENTIATED_CONTENT] = _diff_to_list(nu.get(KEY_DIFFERENTIATED_CONTENT)) + _diff_to_list(nv.get(KEY_DIFFERENTIATED_CONTENT))
        # 合并差异化内容到边的映射：如果同一个差异化内容对应不同的边，只保留第一个（u的）
        map_u = G.nodes[u].get(KEY_DIFF_TO_EDGE_MAP, {})
        map_v = G.nodes[v].get(KEY_DIFF_TO_EDGE_MAP, {})
        for diff, edge_id in map_v.items():
            if diff not in map_u:
                map_u[diff] = edge_id
        G.nodes[u][KEY_DIFF_TO_EDGE_MAP] = map_u
        # 合并 type：start+mid->start，end+mid->end（start+end 已在上方过滤）
        tu, tv = nu.get(KEY_NODE_TYPE) or NODE_TYPE_MID, nv.get(KEY_NODE_TYPE) or NODE_TYPE_MID
        if tu == NODE_TYPE_START or tv == NODE_TYPE_START:
            G.nodes[u][KEY_NODE_TYPE] = NODE_TYPE_START
        elif tu == NODE_TYPE_END or tv == NODE_TYPE_END:
            G.nodes[u][KEY_NODE_TYPE] = NODE_TYPE_END
        else:
            G.nodes[u][KEY_NODE_TYPE] = NODE_TYPE_MID
        # 贝叶斯更新：合并后结点 u 的存在概率（用全图总访问次数）
        total_node_visits = sum(G.nodes[n].get(KEY_SUCCESS_COUNT, 0) for n in G.nodes())
        if total_node_visits > 0:
            G.nodes[u][KEY_EXISTENCE_PROBABILITY] = posterior_node_existence_probability(
                G.nodes[u][KEY_SUCCESS_COUNT], total_visits=total_node_visits
            )
        # 重定向 v 的出边（v -> w, w != u）为 u -> w
        for v2, w, k in list(G.edges(v, keys=True)):
            if w == u:
                G.remove_edge(v, w, k)
                continue
            edge_attrs = dict(G[v][w][k])
            # 保留边的唯一ID
            G.add_edge(u, w, **edge_attrs)
            G.remove_edge(v, w, k)
        # 重定向 v 的入边（w -> v, w != u）为 w -> u
        for w, v2, k in list(G.in_edges(v, keys=True)):
            if w == u:
                G.remove_edge(w, v, k)
                continue
            edge_attrs = dict(G[w][v][k])
            # 保留边的唯一ID
            G.add_edge(w, u, **edge_attrs)
            G.remove_edge(w, v, k)
        G.remove_node(v)

    return G


def _action_canonical(action: dict | None) -> str:
    """将边上的 action 转为可比较的字符串，用于判断「指令一模一样」。"""
    if not action or not isinstance(action, dict):
        return ""
    try:
        return json.dumps(action, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(action)


def merge_duplicate_edges_same_action(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    同一起终 (u, v) 且边上指令（KEY_ACTION）一模一样的多条边合并为一条边。
    保留一条边，合并 KEY_EDGE_SUCCESS_COUNT（求和）；其余属性以保留边为准。
    原地修改 G 并返回 G。
    """
    to_remove: list[tuple[Any, Any, Any]] = []  # (u, v, key)
    done_uv: set[tuple[Any, Any]] = set()
    for u, v, _ in G.edges(keys=True):
        if (u, v) in done_uv:
            continue
        done_uv.add((u, v))
        edges_uv = list(G[u][v].items())  # (key, attrs)
        if len(edges_uv) <= 1:
            continue
        # 按 action 规范化分组
        groups: dict[str, list[tuple[Any, dict]]] = {}
        for key, attrs in edges_uv:
            canon = _action_canonical(attrs.get(KEY_ACTION))
            groups.setdefault(canon, []).append((key, attrs))
        for canon, key_attrs_list in groups.items():
            if len(key_attrs_list) <= 1:
                continue
            # 保留第一条，其余标记删除；合并 success_count，贝叶斯更新边存在概率
            keep_key, keep_attrs = key_attrs_list[0]
            total_count = sum(
                (a.get(KEY_EDGE_SUCCESS_COUNT) or 0) for _, a in key_attrs_list
            )
            G[u][v][keep_key][KEY_EDGE_SUCCESS_COUNT] = total_count
            for k, _ in key_attrs_list[1:]:
                to_remove.append((u, v, k))
    for u, v, k in to_remove:
        G.remove_edge(u, v, k)
    # 全图边存在概率贝叶斯更新（用所有边 success_count 之和作为总次数）
    total_edge_visits = sum(
        G[u][v][key].get(KEY_EDGE_SUCCESS_COUNT, 0) for u, v, key in G.edges(keys=True)
    )
    if total_edge_visits > 0:
        for u, v, k in G.edges(keys=True):
            c = G[u][v][k].get(KEY_EDGE_SUCCESS_COUNT, 0)
            G[u][v][k][KEY_EDGE_EXISTENCE_PROBABILITY] = posterior_edge_existence_probability(
                c, total_edge_visits=total_edge_visits
            )
    return G


def load_task_graph(runs_dir: Path, task_id: str) -> nx.MultiDiGraph | None:
    """
    从 runs_dir 下指定 task_id 的任务目录读 steps 并建图。
    若无有效步骤则返回 None。
    建图后会合并「Launch 且前后 app_name 相同」的冗余边对应结点。
    """
    steps = load_steps_from_task(runs_dir, task_id)
    if not steps:
        return None
    G = build_graph_from_steps(steps)
    if G.order() == 0:
        return None
    G = _merge_redundant_launch_same_page(G)
    return G


_DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "autoglm_runs"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从指定任务建一张图。",
    )
    parser.add_argument(
        "task",
        help="任务目录路径（如 autoglm_runs/task_0001）或 task_id（如 task_0001，则从默认 autoglm_runs 查找）",
    )
    parser.add_argument("--show", action="store_true", help="建图后弹出窗口显示图")
    args = parser.parse_args()
    task_arg = Path(args.task)
    if task_arg.is_dir() and task_arg.name.startswith("task_"):
        runs_dir = task_arg.parent
        task_id = task_arg.name
    else:
        runs_dir = _DEFAULT_RUNS_DIR
        task_id = args.task.strip()
        if not task_id.startswith("task_"):
            print("task 应为 task_xxx 或指向 task_xxx 的目录路径", file=sys.stderr)
            sys.exit(1)
    if not runs_dir.is_dir():
        print(f"Runs dir not found: {runs_dir}", file=sys.stderr)
        sys.exit(1)
    G = load_task_graph(runs_dir, task_id)
    if G is None:
        print(f"No graph built for {task_id} (no valid steps with page_name)", file=sys.stderr)
        sys.exit(1)
    print(f"Built graph for {task_id}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    if args.show:
        import webbrowser
        from interaction.rollback_api import build_graph_list_from_nx, save_graph_collection
        graph_list = build_graph_list_from_nx([G])
        json_path = runs_dir / "graph_collection.json"
        save_graph_collection(json_path, graph_list)
        webbrowser.open("http://127.0.0.1:8765/#rollback")
        print(f"已保存图集到 {json_path}，请在浏览器中打开「回滚攻击」页查看（工程目录需设为 {runs_dir}）。")


if __name__ == "__main__":
    main()
