# UI Rollback Attack


**Repository**: [https://github.com/wdlysyn/UI-Rollback-Attack](https://github.com/wdlysyn/UI-Rollback-Attack)

## Overview

Mobile agents are increasingly deployed to automate multistep tasks on smartphones by perceiving user interface (UI) states and executing UI actions. This shift makes agent-driven UI actions a natural target for **rollback-style exploitation**: previously observed interaction flows can be replayed to regain control over devices. However, traditional smartphone rollback attacks rely on **system-wide state reversion** and typically require **root privileges**, which creates substantial deployment barriers in real-world scenarios.

To address this gap, we propose a **UI-level rollback attack framework** that targets mobile agents and achieves device control by **replaying and recombining UI action sequences** without compromising system integrity. We monitor agent–smartphone UI interactions and model the observed UI steps as a **UI-action rollback attack graph**. To overcome the limitations of single-path attacks, we design a **UI-action path recombination** mechanism based on this graph. To mitigate failures caused by UI layout shifts, we further introduce an **online activation mechanism** that reactivates outdated rollback paths using traces from newly executed tasks. Experiments on real-world applications show that the attacks reliably reproduce mobile agent tasks and can bypass existing defenses to gain control over smartphones, exposing critical vulnerabilities in mobile agent deployments.

**This repository** provides the code and experimental environment for the above research. It includes a local web console for building and merging rollback attack graphs, generating and recombining UI-action paths, collecting agent task traces, and replaying attack sequences on devices.

---

## Features

| Module | Description |
|--------|-------------|
| **Environment Info** | Set project directory, update-data directory, and API keys / base URLs (for task creation and path generation). Configuration is persisted and used by all modules. |
| **Task Collection** | Create task lists (AndroidWorld or custom CSV), run tasks, and monitor status (pending / running / success / failed). Supports base and incremental data modes. |
| **Post Processing** | Run post-processing on task screenshots (e.g. page state recognition). View per-step status and retry failed items. |
| **Graph Build** | Build per-task state graphs from `task_xxx/steps`, then **merge** graphs by node similarity (app name + page description, BGE embeddings). View unmerged/merged graph sets and generate candidate paths between two selected nodes. |
| **Rollback Attack** | Load merged graph, select start/end nodes, find path, **replay** the action sequence on device. Path generation (goal + JSON) and statistics (node/edge counts, complete task paths) are also available. |

---

## Requirements

- **Python** 3.10+
- **Dependencies**: See `requirements.txt` (includes `networkx`, `sentence-transformers` for graph merge similarity, `requests`, etc.)
- **For replay**: Android device/emulator and ADB. **For task creation or path generation**: external API (set in Environment Info).

---

## Installation

```bash
git clone git@github.com:wdlysyn/UI-Rollback-Attack.git
cd android_world
pip install -r requirements.txt
```

---

## Quick Start

1. **Start the local server** (from project root):

   ```bash
   python app.py
   ```

   The console will show:

   ```
   Serving at http://127.0.0.1:8765/
   ```

2. **Open in browser**: Go to [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

3. **Configure environment** (Environment Info tab):
   - Set **Project Directory** (default: `autoglm_runs` under project root).
   - Set Update Data Directory and API keys / URLs when using incremental merge or external APIs.
   - Click **Save Configuration**.

4. **Run tasks** (Task Collection): Generate or import task list → **Start Running** → wait for success tasks.

5. **Build and merge graphs** (Graph Build): Click **Generate Graph Collection** → then **Merge Graph Collection**. Load merged graph and select two nodes → **Generate Candidate Paths**.

6. **Replay** (Rollback Attack): After finding a path on Graph Build, open Rollback Attack tab → **Replay Task** to send the action sequence to the device.

---

## Usage Overview

### Environment Info

- **Project Directory**: Root for task data, graphs, and stats. Required for most operations.
- **Update Data Directory**: Used for incremental merge (merge new tasks into existing merged graph).
- **API keys / URLs**: Set in Environment Info; used for task creation and path generation.

### Task Collection

- **Data**: Base (main project dir) or Incremental (separate update dir).
- **Task Source**: AndroidWorld (generate from API) or Custom (import CSV).
- Create list → Start Running → Refresh to see status. Success tasks are used for graph building.

### Post Processing

- Runs post-processing on step images under each task. Use after tasks have produced `task_xxx/steps/`.

### Graph Build

- **Generate Graph Collection**: Builds one graph per success task → writes `graph_collection.json`.
- **Merge Graph Collection**: Merges nodes by semantic similarity (threshold default 0.9) → writes `graph_collection_merged.json` and updates `graph_collection_stats.json`.
- **Incremental Merge**: Merge an “update” directory’s graphs into the existing merged set.
- Select two nodes → **Generate Candidate Paths** → path is saved and can be replayed from Rollback Attack.

### Rollback Attack

- Load merged graph (same data as Graph Build merged view).
- **Path Generation**: Upload JSON + goal to request path generation (uses the API set in Environment Info).
- **Replay Task**: Replay the path found on Graph Build (same two nodes) on the connected device.
- **Display Statistics**: Shows task count, pre/merge node and edge counts, NCR/ECR, complete task paths (node paths, expanded paths, start/end counts).

---

## Configuration

- All config is stored via `interaction/project_config.py` (project root, update-data dir, API keys / URLs).
- Project directory can be set via UI (Environment Info → Select Directory → Save) or by API (`POST /api/project` with `project_root`).

---

## Notes

- Most APIs require a valid **project directory**. Set it in Environment Info first.
- **Generate Graph Collection** needs at least one **success** task in `task_list.csv`; run Task Collection until some tasks succeed.
- Graph merge uses **sentence-transformers** (BGE); first run may download the model.
- Replay requires the replay backend (e.g. `graph_build/run_sequence.py`) and an accessible device/emulator.

---

## License

This repository is for research and academic use. See [LICENSE](LICENSE) in the repository root for license terms.
# UIrollback
