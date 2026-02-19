# Secretary Agent（小名：kai）

基于 **Cursor Agent** 的自动化任务系统：用户用自然语言提交任务，秘书 Agent 负责归类写入，扫描器调度 Worker Agent 执行任务并写报告。CLI 主入口为 **kai**（或 **secretary**），支持 Windows、macOS、Linux。

---

## 目录

- [前置要求](#前置要求)
- [安装](#安装)
- [验证安装](#验证安装)
- [快速开始](#快速开始)
- [首次运行示例（端到端）](#首次运行示例端到端)
- [配置与数据文件位置](#配置与数据文件位置)
- [命令说明](#命令说明)
- [环境变量与配置](#环境变量与配置)
- [平台差异（简要）](#平台差异简要)
- [跨平台检查清单](#跨平台检查清单)
- [发布后使用：随处使用 kai](#发布后使用随处使用-kai)
- [常见问题 / 故障排除](#常见问题--故障排除)
- [目录结构（工作区内）](#目录结构工作区内)
- [工作流程简述](#工作流程简述)
- [与 Cursor 的关系](#与-cursor-的关系)
- [任务文件格式](#任务文件格式)
- [维护者：发布与打包](#维护者发布与打包)

---

## 前置要求

- **Python 3.9+**（推荐 3.10+；依赖见 `pyproject.toml`，含 `rich`）
- **Cursor** 已安装，且能通过命令行调用：
  - **Windows**：通过 PowerShell 调用 `agent`（Cursor 安装后通常已配置）
  - **macOS / Linux**：`agent` 或 `cursor` 在 PATH 中
- 使用方式二选一：
  - **将 kai 加入 PATH**：通过 `pip install -e .` 或 `pip install secretary-agent` 安装后，在对应环境的 `Scripts`（Windows）或 `bin`（Unix）目录在 PATH 中即可直接运行 `kai`
  - **在虚拟环境中使用**：激活 venv/conda 后，安装的 `kai` 即在该环境中可用

---

## 安装

### 方式一：从源码开发安装（克隆仓库后）

适用于参与开发或使用最新代码。

**Windows（PowerShell 或 cmd）**

```powershell
cd C:\path\to\Kai
python -m venv .venv
.venv\Scripts\activate
pip install -e .
kai --help
```

**macOS / Linux**

```bash
cd /path/to/Kai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
kai --help
```

- 开发安装后，`kai` 和 `secretary` 两个命令都会指向当前源码，修改代码后无需重装。
- 若使用 **uv**：在项目根目录执行 `uv venv` 再 `uv pip install -e .`，然后使用 `uv run kai` 或确保 `uv` 创建的虚拟环境已激活后直接 `kai`。

### 方式二：发布后安装（从 PyPI）

适用于仅使用、不改源码。发布后包名为 **secretary-agent**，安装后可在任意目录使用 `kai`（需保证安装目录在 PATH 中）。

```bash
# 用户级安装（推荐，无需 root）
pip install --user secretary-agent

# 或全局安装（需相应权限）
pip install secretary-agent
```

（Windows 下可在 PowerShell 或 cmd 中执行上述 `pip` 命令；macOS/Linux 在终端中执行即可。）

- **Windows**：用户级安装时，`kai.exe` 通常在 `%APPDATA%\Python\Python3x\Scripts`，请将该目录加入系统 PATH，或在安装 Python 时勾选 “Add Python to PATH”。
- **macOS / Linux**：用户级安装时，`kai` 通常在 `~/.local/bin`，请确保该目录在 PATH 中（例如在 `~/.bashrc` 或 `~/.zshrc` 中写 `export PATH="$HOME/.local/bin:$PATH"`）。

### 可选：虚拟环境建议

- 建议在项目目录或工作区使用 **venv**：`python -m venv .venv`，再 `pip install -e .` 或 `pip install secretary-agent`，这样 `kai` 仅在该环境中存在，避免与其它项目冲突。
- 使用 **uv** 时，可用 `uv run kai ...` 自动在项目虚拟环境中运行，无需手动激活。

---

## 验证安装

安装后可在终端自检 kai 是否可用：

1. **检查命令是否在 PATH 中**：执行 `kai --help` 或 `kai monitor --text`。
   - 若出现帮助信息或状态输出，说明 kai 已可用，可继续「快速开始」。
   - 若提示「找不到命令」或 `command not found`，说明安装目录未在 PATH 中，请按下方「常见问题」中对应平台的 PATH 排查。
2. **确认 Cursor/agent 可用**：kai 的秘书与 Worker 依赖 Cursor Agent。若本机尚未配置 `agent`（Windows 下为 PowerShell 中的 `agent`），后续执行 `kai start` 时可能报错，请先确保在终端能直接运行 `agent`。

---

## 快速开始

以下均以 **kai** 为主命令；若你改过名（`kai name lily`），则把 `kai` 换成对应名字即可。

```bash
# 1. 设定工作区（建议先做，否则使用当前目录为工作区）
kai base .

# 2. 提交任务（秘书会归类并分配给工人）
kai task "实现一个 HTTP 服务器，使用 Python 标准库"

# 3. 查看状态：待办 / 进行中 / 报告
kai monitor --text

# 4. 招募工人并启动扫描器（开始处理任务）
kai hire
kai start sen

# 单次扫描（处理完当前任务后退出）
kai start sen --once
```

**从源码运行而不安装入口时**（未执行 `pip install -e .`）：

```bash
python -m secretary.cli task "你的任务描述"
python -m secretary.cli monitor --text
```

---

## 首次运行示例（端到端）

以下是从零到第一次成功执行 `kai start` 的完整命令序列，可直接复制执行。**若已安装且 kai 已在 PATH 中，可从「设定工作区」一步开始。**

**Windows (PowerShell)**

```powershell
cd C:\path\to\Kai
python -m venv .venv
.venv\Scripts\activate
pip install -e .
kai base .
kai task "列出当前目录下的文件"
kai hire
kai start sen --once
```

**macOS / Linux**

```bash
cd /path/to/Kai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
kai base .
kai task "列出当前目录下的文件"
kai hire
kai start sen --once
```

差异仅为路径与虚拟环境激活方式；后续 `kai base .`、`kai task`、`kai hire`、`kai start sen --once` 在各平台一致。

---

## 配置与数据文件位置

便于「随处用」时确认配置与数据写在哪里。以下为各平台默认约定。

| 内容 | Windows | macOS / Linux |
|------|--------|----------------|
| **配置目录** | `%APPDATA%\kai` | `~/.config/kai` |
| **持久化配置文件** | `%APPDATA%\kai\settings.json` | `~/.config/kai/settings.json` |
| **配置项说明** | `base_dir`（工作区）、`cli_name`、`model`、`language` 等，由 `kai base`、`kai name`、`kai model` 等写入。 | 同上。 |

**工作区（BASE_DIR）** 由 `kai base <路径>` 或环境变量 `SECRETARY_WORKSPACE` 决定。工作区确定后，以下路径均相对于工作区根目录：

| 路径 | 说明 |
|------|------|
| `workers/` | 工人目录（每人含 `tasks/`、`ongoing/`、`logs/` 等）。 |
| `report/` | Worker 完成的报告。 |
| `skills/` | 学会的技能。 |
| `logs/` | 后台运行日志。 |
| `secretary_goals.md`、`secretary_memory.md` | 秘书全局目标与记忆（如 `kai target` 写入）。 |

未设置工作区时，使用当前工作目录（CWD）作为工作区。

---

## 命令说明

| 类别     | 命令 | 说明 |
|----------|------|------|
| 任务     | `kai task "描述"` | 提交任务，经秘书 Agent 归类并分配；`-q` 安静模式；`--time N` 最低执行时间(秒)；`--worker NAME` 直接指定工人。 |
| 任务     | `kai keep "持续目标"` | 持续监控模式：队列空时自动生成新任务推进目标；可加 `--worker NAME`。 |
| 技能     | `kai skills` | 列出所有已学技能（内置 + 自定义）。 |
| 技能     | `kai learn "描述" skill-name` | 学习新技能。 |
| 技能     | `kai forget skill-name` | 忘掉一个技能。 |
| 技能     | `kai use <技能名>` 或 `kai <技能名>` | 使用技能，直接写入对应 worker 的 tasks。内置技能：evolving、analysis、debug。 |
| 工人     | `kai hire [名字]` | 招募 worker（只注册，不启动）；不写名字则随机生成。 |
| 工人     | `kai start [名字]` | 启动 worker 扫描器；默认 `sen`。`--once` 只跑一轮；`-q` 后台运行。 |
| 工人     | `kai fire <名字>` | 解雇 worker。 |
| 工人     | `kai workers` | 列出所有工人及状态。 |
| 工人     | `kai stop <名字>` | 停止指定 worker 的进程。 |
| 后台     | `kai recycle` | 启动回收者（审查 report/）；`--once` 只跑一次；`-q` 安静。 |
| 后台     | `kai monitor` | 实时监控面板（TUI）；`-i N` 刷新间隔(秒)。 |
| 状态     | `kai monitor` / `kai monitor --text` | 实时监控面板 (TUI) 或文本状态（待办、进行中、报告、工人、技能等）。 |
| 报告     | `kai report <worker>` / `kai report all` | 查看指定 worker 或全部报告。 |
| 设置     | `kai base [路径]` | 设定/查看工作区；`kai base .` 设为当前目录；`kai base --clear` 清除。 |
| 设置     | `kai name <新名字>` | 给 CLI 改名为其他命令（如 lily）。 |
| 设置     | `kai model [模型名]` | 设置或查看默认模型（如 Auto、gpt-4、claude-3）。 |
| 设置     | `kai target [任务1 任务2 ...]` | 设定秘书全局目标；`kai target --clear` 清空；无参数则列出。 |
| 全局选项 | `kai -l en` / `kai --language zh` | 输出语言：`en` 或 `zh`（也可用环境变量 `SECRETARY_LANGUAGE`）。 |
| 清理     | `kai clean-logs` | 清理 logs/ 下日志。 |
| 清理     | `kai clean-processes` | 清理泄露的 worker 进程记录。 |
| 帮助     | `kai help` / `kai help <命令>` | 显示帮助或某命令的详细说明。 |

不带子命令直接运行 `kai` 会进入**交互模式**，可连续输入上述子命令；输入 `exit` 退出。

---

## 环境变量与配置

工作区（BASE_DIR）优先级从高到低：

1. 本次运行的 `-w/--workspace` 参数  
2. 环境变量 `SECRETARY_WORKSPACE`  
3. 持久化配置（`kai base <path>` 保存的路径）  
4. 当前工作目录（CWD）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECRETARY_WORKSPACE` | （见上） | 工作区根目录。 |
| `CURSOR_BIN` / Cursor 调用方式 | Windows: PowerShell 调 agent；Unix: `agent` | 见 `secretary/config.py`，Windows 下通过 PowerShell 调用 Cursor。 |
| `CURSOR_MODEL` | `Auto` | 传给 Cursor 的模型。 |
| `SCAN_INTERVAL` | `5` | 扫描器轮询 tasks 的间隔（秒）。 |
| `RETRY_INTERVAL` | `3` | Worker 未完成时下一轮间隔（秒）。 |
| `SECRETARY_LANGUAGE` | `zh` | 输出语言：`zh` / `en`。 |

配置文件位置见上文「配置与数据文件位置」表格（按平台固定）；工作区下的数据由工作区（`kai base` 或 `SECRETARY_WORKSPACE`）决定，例如：

- 工作区下的 `report/`、`workers/`、`skills/`、`logs/` 等；
- 秘书记忆与目标：工作区下的 `secretary_memory.md`、`secretary_goals.md`（可由 `kai target` 等写入）。

---

## 平台差异（简要）

- **Windows**
  - 安装后 `kai` 在 `Scripts` 目录（如 `.venv\Scripts\kai.exe` 或用户目录下的 `Scripts`）；需将该目录加入 PATH 才能在任意终端使用 `kai`。
  - Cursor 通过 PowerShell 调用 `agent`，与在终端直接输入 `agent` 的行为一致；若 Cursor 未正确加入 PATH，需先配置 Cursor/agent 再使用 kai。
- **macOS / Linux**
  - 安装后 `kai` 在虚拟环境的 `bin` 或 `~/.local/bin`；确保该目录在 PATH 中。
  - 使用 `cursor` 或 `agent` 时，需保证 Cursor 已安装且命令行可用。
- **共用**
  - 建议在工作区目录下执行 `kai base .`，这样在任意目录执行 `kai task ...`、`kai monitor` 等时，都会操作该工作区（若未设 `SECRETARY_WORKSPACE` 且未用 `-w`，则依赖「当前目录」或已保存的 base）。

---

## 跨平台检查清单

发布前或用户自检时，可在各平台逐项核对：

| 检查项 | Windows | macOS / Linux |
|--------|--------|----------------|
| **kai 在 PATH 中** | 在 PowerShell/cmd 中执行 `kai --help` 能出现帮助信息；若否，将 `%APPDATA%\Python\Python3x\Scripts` 或 `.venv\Scripts` 加入 PATH。 | 终端执行 `kai --help` 能出现帮助信息；若否，将 `~/.local/bin` 或 `.venv/bin` 加入 PATH（如 `export PATH="$HOME/.local/bin:$PATH"`）。 |
| **agent 可用** | 在 PowerShell 中能执行 `agent`（Cursor 安装后通常已配置）。 | 终端能执行 `agent` 或 `cursor`。 |
| **工作区已设定** | 在项目根目录执行过 `kai base .`；或通过 `kai base` 确认当前工作区。 | 同上。 |
| **首次运行可复现** | 按「首次运行示例」中 Windows 块复制执行，能完成 `kai start sen --once`。 | 按「首次运行示例」中 macOS/Linux 块复制执行，能完成 `kai start sen --once`。 |

---

## 发布后使用：随处使用 kai

### 发布后首次使用（仅从 PyPI 安装、不参与开发）

若你通过 **PyPI** 安装（`pip install secretary-agent`），与「从源码开发安装」的差异在于：

- **无需克隆仓库**：在任意目录执行 `pip install secretary-agent`（或 `pip install --user secretary-agent`）即可，安装后 `kai` 在对应环境的 Scripts/bin 中。
- **不涉及源码**：无法直接改 kai 的代码；升级依赖 `pip install -U secretary-agent`（见下方「如何升级 kai」）。

**建议的首次使用步骤**（在要作为工作区的目录下执行）：

1. 进入你的项目或工作目录：`cd /path/to/your/project`
2. 设定工作区：`kai base .`
3. 之后即可在该目录或任意目录使用：`kai task "..."`、`kai monitor`、`kai hire`、`kai start sen` 等（只要 `kai` 在 PATH 中）。

安装 **secretary-agent** 后（或开发安装并激活对应环境后），只要 `kai` 在 PATH 中，即可在任意目录使用：

```bash
# 在任意目录提交任务（会使用已设定的工作区或当前目录）
kai task "给 README 增加一段示例"

# 查看状态（同上，依赖工作区配置）
kai monitor --text

# 临时指定本次工作区，不改变持久配置
kai -w /path/to/project task "修复测试"
```

- **工作区**：首次使用建议在项目根目录执行 `kai base .`，之后无论在哪执行 `kai`，都会操作该目录下的 `workers/`、`report/`、`skills/` 等。
- **可配置项**：环境变量见上表；持久化的工作区与模型等通过 `kai base`、`kai model` 等写入用户配置目录或工作区，具体位置见上文「配置与数据文件位置」表格。

### 查看当前版本

便于反馈问题或对照发布说明，可查看本机安装的 kai 版本。CLI 暂未提供 `kai --version`，可通过：

```bash
pip show secretary-agent
```

在输出中查看 **Version** 一行即为当前安装版本。

### 如何升级 kai

从 PyPI 安装的用户升级到最新版：

```bash
pip install -U secretary-agent
```

- **用户级安装**（`pip install --user`）时：
  - **Windows**：升级后 `kai.exe` 仍在 `%APPDATA%\Python\Python3x\Scripts`，只要该目录已在 PATH 中，无需额外操作；若曾手动加过该路径，升级后继续有效。
  - **macOS / Linux**：升级后 `kai` 仍在 `~/.local/bin`，只要该目录已在 PATH 中（如 `export PATH="$HOME/.local/bin:$PATH"`），无需额外操作。
- **虚拟环境**：在对应 venv/conda 中执行 `pip install -U secretary-agent` 即可，该环境内的 `kai` 会更新。
- **从源码开发安装**：在仓库根目录执行 `git pull` 后无需重装（`pip install -e .` 已指向当前源码），拉取即是最新代码。

### 获知新版本与发布说明

- **PyPI**：[https://pypi.org/project/secretary-agent/](https://pypi.org/project/secretary-agent/) 可查看最新版本号与简要说明。
- **GitHub**：若本仓库提供 Releases 页面，可在此查看版本历史与发布说明。可选：若仓库根目录存在 `CHANGELOG.md`，可在该文件或对应 Release 中查看更新记录。

### 卸载

从本机移除 kai（包名 **secretary-agent**）：

```bash
pip uninstall secretary-agent
```

- **用户级安装**：在当初执行 `pip install --user secretary-agent` 的同一用户、同一 Python 环境下执行上述命令即可。
- **虚拟环境**：先激活对应 venv/conda，再执行 `pip uninstall secretary-agent`，仅会从该环境中移除，不影响系统或其他环境。

---

## 常见问题 / 故障排除

针对各平台安装后「装完不能用」的典型情况，可先按「验证安装」自检，再对照下表排查。

| 平台 | 现象 | 解决思路 |
|------|------|----------|
| **Windows** | 输入 `kai` 提示找不到命令 | 用户级安装时 `kai.exe` 在 `%APPDATA%\Python\Python3x\Scripts`，需将该目录加入系统 PATH；或在安装 Python 时勾选「Add Python to PATH」；若用 venv，确保 `.venv\Scripts` 在 PATH 或先 `activate`。 |
| **Windows** | Cursor/agent 未配置或找不到 | 确保 Cursor 已安装，且在 PowerShell 中能执行 `agent`；若不可用，检查 Cursor 安装路径是否在 PATH 中。 |
| **macOS / Linux** | 输入 `kai` 提示 command not found | 用户级安装时 `kai` 在 `~/.local/bin`，在 `~/.bashrc` 或 `~/.zshrc` 中加入 `export PATH="$HOME/.local/bin:$PATH"`，保存后重新打开终端或执行 `source ~/.bashrc`。 |
| **macOS / Linux** | cursor/agent 不可用 | 确保 Cursor 已安装且 `agent` 或 `cursor` 在 PATH 中，以便 kai 调用 Cursor Agent。 |
| **共用** | `kai monitor` / `kai monitor --text` 报错或行为不符合预期 | 多为工作区未设定：在项目根目录执行 `kai base .`；或用 `kai base` 查看当前工作区，用 `kai -w <路径>` 临时指定本次运行的工作区。 |
| **共用** | 如何确认当前工作区 | 执行 `kai base`（无参数）可查看当前持久化的工作区路径；未设置时使用当前工作目录（CWD）。 |

---

## 目录结构（工作区内）

| 路径 | 说明 |
|------|------|
| `workers/` | 工人目录；每人有 `tasks/`、`ongoing/`、`logs/` 等。 |
| `workers/<name>/tasks/` | 该工人的待处理任务（.md）。 |
| `workers/<name>/ongoing/` | 该工人正在执行的任务。 |
| `report/` | Worker 完成的报告（待回收者审查）。 |
| `solved-report/`、`unsolved-report/` | 回收者判定为已解决/未解决的报告。 |
| `skills/` | 学会的技能（可复用任务模板）。 |
| `logs/` | 后台运行日志。 |
| `secretary_memory.md`、`secretary_goals.md` | 秘书记忆与全局目标（kai target 等）。 |

---

## 工作流程简述

1. **提交**：`kai task "..."` → 秘书 Agent 读入请求，决定新建或追加 → 写入某工人的 `workers/<name>/tasks/`。
2. **扫描**：`kai start <name>` 启动的扫描器轮询该工人的 `tasks/`，将 .md 移到 `ongoing/`。
3. **执行**：对每个 `ongoing/*.md` 反复调用 Worker Agent；Worker 可更新同一文件记录进展。
4. **完成**：Worker 认为完成后删除该 `ongoing/*.md`，在 `report/` 写入 `*-report.md`；回收者（`kai recycle`）可审查并移至 solved/unsolved。

---

## 与 Cursor 的关系

- 秘书与 Worker 均通过 **Cursor Agent**（如 `agent --print --force --trust --workspace <path> "<prompt>"`）执行。
- 需在已安装 Cursor 的环境中运行，且具备调用 `agent`（或 Windows 下通过 PowerShell 调用的等效方式）的权限。
- `.cursor/rules/secretary-agent.md` 等规则文件可定义秘书/Worker 的全局规则，供 Cursor 使用。

---

## 任务文件格式

`workers/<name>/tasks/` 与 `ongoing/` 中的任务文件建议为 Markdown，包含：

- `# 任务: 标题`
- `## 描述` / `## 目标` / `## 工作区`（工作区为 Worker 执行目录，可选）

Worker 未完成时会在文件末尾追加 `## 进展记录`；完成后会删除该文件并在 `report/` 写入报告。

---

## 维护者：发布与打包

本节面向**仓库维护者**，说明如何打版本、发布到 PyPI，以及发布前建议的检查项，与用户侧的「发布后使用：随处使用 kai」形成闭环。

### 版本号约定

- 采用**语义化版本**，当前为 `0.x.y`（如 `pyproject.toml` 中的 `0.1.0`）。主版本 0 表示尚未稳定，小版本号（y）用于修复与小幅更新，次版本号（x）用于功能变更。
- **Git tag 与 PyPI 版本对应**：建议每次发布前打 tag，格式为 `v0.x.y`（与 `pyproject.toml` 中 `version` 一致），便于追溯与回滚。

### 发布到 PyPI 的步骤

1. **安装构建与上传工具**（若尚未安装）：
   ```bash
   pip install build twine
   ```

2. **在仓库根目录构建分发包**：
   ```bash
   python -m build
   ```
   完成后会在 `dist/` 下生成 `.whl` 与 `.tar.gz`。

3. **上传到 PyPI**：
   - **正式 PyPI**：`twine upload dist/*`（会提示输入 PyPI 用户名与密码或 token）。
   - **Test PyPI（可选）**：先验证再发正式版时，可使用 `twine upload --repository testpypi dist/*`，然后用户可通过 `pip install -i https://test.pypi.org/simple/ secretary-agent` 安装测试版。

4. **发布后**：在 GitHub 可创建对应 Release、附上 tag 与发布说明；用户即可通过 `pip install -U secretary-agent` 获取新版本。

### 发布前检查

与「[跨平台检查清单](#跨平台检查清单)」呼应，建议维护者在发布前至少完成以下项：

| 检查项 | 说明 |
|--------|------|
| **版本号与 tag** | 在 `pyproject.toml` 中更新 `version`，并准备对应 `v0.x.y` 的 tag。 |
| **跨平台自检** | 在 Windows、macOS、Linux 至少各验证一次：`pip install -e .` 与 `kai --help` 可正常执行；可选按「首次运行示例」跑通 `kai start sen --once`。 |
| **README 与 CLI 一致** | 确认 README 中的命令、选项与当前 CLI 行为一致（如 `kai task`、`kai monitor`、`kai base` 等）。 |
| **可选：测试或 smoke** | 若有测试或 smoke 命令，发布前运行一遍，避免明显回归。 |

本仓库暂无独立的 `CONTRIBUTING.md` 或 `docs/release.md`，以上内容以 README 内说明为主；若后续新增上述文档，可在此处链接过去以避免重复。

---

更多改进方向与优先级见 [IMPROVEMENTS.md](./IMPROVEMENTS.md)。
