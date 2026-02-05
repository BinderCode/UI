# UI Rollback Attack

**仓库地址**：[https://github.com/wdlysyn/UI-Rollback-Attack](https://github.com/wdlysyn/UI-Rollback-Attack)

## 概述

移动智能体越来越多地被部署在智能手机上，通过感知用户界面（UI）状态并执行 UI 操作来自动完成多步任务。这一趋势使得由智能体驱动的 UI 操作成为**回滚式利用**的自然目标：已观测到的交互流程可被重放以重新获得对设备的控制。然而，传统的智能手机回滚攻击依赖**系统级状态回滚**，且通常需要**root 权限**，在真实场景中部署门槛较高。

为填补这一空白，我们提出一种面向移动智能体的 **UI 级回滚攻击框架**，通过**重放与重组 UI 操作序列**在不破坏系统完整性的前提下实现对设备的控制。我们监控智能体与智能手机的 UI 交互，将观测到的 UI 步骤建模为 **UI 操作回滚攻击图**。为克服单一路径攻击的局限，我们基于该图设计了 **UI 操作路径重组**机制。为缓解因 UI 布局变化导致的失败，我们进一步引入**在线激活机制**，利用新执行任务产生的轨迹重新激活过期的回滚路径。在真实应用上的实验表明，该攻击能稳定复现移动智能体任务，并可绕过现有防御获得对智能手机的控制，揭示了移动智能体部署中的严重安全风险。

**本仓库**提供上述研究对应的代码与实验环境，包含用于构建与合并回滚攻击图、生成与重组 UI 操作路径、收集智能体任务轨迹以及在设备上重放攻击序列的本地 Web 控制台。

---

## 功能概览

| 模块                                   | 说明                                                                                                                                                             |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Environment Info（环境信息）** | 设置工程目录、增量数据目录及 API Key / Base URL（用于任务创建与路径生成）。配置持久化，供各模块使用。 |
| **Task Collection（任务收集）**  | 创建任务列表（AndroidWorld 或自定义 CSV）、启动运行、查看状态（pending / running / success / failed）。支持基础与增量数据模式。                                  |
| **Post Processing（后处理）**    | 对任务截图执行后处理（如页面状态识别）。按步骤查看处理状态，支持仅重试失败项。                                                                                   |
| **Graph Build（建图）**          | 从 `task_xxx/steps` 构建单任务状态图，再按**结点相似度**（app 名 + 页面描述，BGE 嵌入）**合并**图集。可查看未合并/合并图、选择两结点生成候选路径。 |
| **Rollback Attack（回滚攻击）**  | 加载合并图，选择起点/终点、查找路径，在设备上**重放**操作序列。另支持路径生成（目标 + JSON）与统计（结点/边数量、可执行任务路径等）。                      |

---

## 环境要求

- **Python** 3.10+
- **依赖**：见 `requirements.txt`（含 `networkx`、`sentence-transformers` 用于图合并相似度、`requests` 等）
- **重放**需：Android 设备/模拟器与 ADB。**任务创建或路径生成**需：在 Environment Info 中配置外部 API。

---

## 安装

```bash
git clone git@github.com:wdlysyn/UI-Rollback-Attack.git
cd android_world
pip install -r requirements.txt
```

---

## 快速开始

1. **启动本地服务**（在项目根目录）：

   ```bash
   python app.py
   ```

   控制台会显示：

   ```
   Serving at http://127.0.0.1:8765/
   ```
2. **浏览器访问**：[http://127.0.0.1:8765/](http://127.0.0.1:8765/)
3. **配置环境**（Environment Info）：

   - 设置 **Project Directory**（默认：项目下的 `autoglm_runs`）。
   - 使用增量合并或外部 API 时，设置 Update Data Directory、API Key / URL。
   - 点击 **Save Configuration**。
4. **运行任务**（Task Collection）：生成或导入任务列表 → **Start Running** → 等待部分任务 success。
5. **建图与合并**（Graph Build）：**Generate Graph Collection** → **Merge Graph Collection** → 加载合并图，选择两结点 → **Generate Candidate Paths**。
6. **重放**（Rollback Attack）：在 Graph Build 找到路径后，切到 Rollback Attack → **Replay Task** 将操作序列发送到设备。

---

## 使用说明

### Environment Info（环境信息）

- **Project Directory**：任务数据、图集、统计的根目录，多数操作依赖此项。
- **Update Data Directory**：增量合并时，将新任务合并进已有合并图所用目录。
- **API Key / URL**：在 Environment Info 中设置；用于任务创建、路径生成。

### Task Collection（任务收集）

- **Data**：Base（主工程目录）或 Incremental（增量数据目录）。
- **Task Source**：AndroidWorld（从 API 生成）或 Custom（导入 CSV）。
- 创建列表 → Start Running → Refresh 查看状态；**success** 任务用于建图。

### Post Processing（后处理）

- 对每个任务的步骤截图执行后处理；需先有 `task_xxx/steps/`。

### Graph Build（建图）

- **Generate Graph Collection**：为每个 success 任务建一张图 → 写出 `graph_collection.json`。
- **Merge Graph Collection**：按结点语义相似度合并（默认阈值 0.9）→ 写出 `graph_collection_merged.json` 并更新 `graph_collection_stats.json`。
- **Incremental Merge**：将“增量”目录的图合并进当前合并图集。
- 选择两结点 → **Generate Candidate Paths** → 路径可保存，并在 Rollback Attack 中重放。

### Rollback Attack（回滚攻击）

- 加载与 Graph Build 相同的合并图。
- **Path Generation**：上传 JSON + Goal，请求路径生成（使用 Environment Info 中配置的 API）。
- **Replay Task**：重放在 Graph Build 中选定的两结点之间的路径到设备。
- **Display Statistics**：查看任务数、合并前后结点/边数、NCR/ECR、可执行任务路径（结点路径数、展开路径数、起点/终点数等）。

---

## 配置

- 配置由 `interaction/project_config.py` 管理（工程根目录、增量数据目录、API Key / URL 等）。
- 工程目录可在 Environment Info 中“选择目录”后保存，或通过 API（`POST /api/project`，body 含 `project_root`）设置。

---

## 说明

- 多数 API 依赖已设置的**工程目录**，请先在 Environment Info 中配置并保存。
- **Generate Graph Collection** 要求 `task_list.csv` 中至少有一个 **success** 任务；请先在 Task Collection 中运行并得到成功任务。
- 图合并使用 **sentence-transformers**（BGE），首次运行可能下载模型。
- 重放需后端（如 `graph_build/run_sequence.py`）与可用的设备/模拟器。

---

## License

本仓库供研究与学术使用。许可条款见仓库根目录下的 [LICENSE](LICENSE) 文件。
