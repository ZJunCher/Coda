# Coda

Coda 是一个面向本地代码仓库任务的轻量级代码 Agent Harness。它运行在命令行中，通过模型、工具、上下文管理、结构化记忆和运行工件，将一次自然语言请求推进成一条可控、可恢复、可复盘的执行链路。

项目的重点不只是“让模型能调用工具”，而是围绕真实代码仓库任务补齐运行时能力：模型需要知道当前仓库状态，工具调用需要边界，长任务需要管理上下文，任务中断后需要恢复现场，运行结束后也需要留下可追踪的证据。

## Features

- **Workspace Context**：启动时采集当前目录、Git 仓库根目录、分支、工作区状态、最近提交和关键项目文档，形成轻量仓库基线。
- **Tool Calling**：内置文件读取、搜索、shell 执行、文件写入、精确补丁和只读 delegate 等工具，并通过参数校验、路径边界和审批策略控制风险。
- **Context Management**：将 prompt 拆分为 prefix、memory、relevant memory、history 和 current request，并在超预算时按优先级压缩。
- **Layered Memory**：维护任务摘要、最近文件、文件摘要和过程笔记，减少多轮任务中的重复读取和重复确认。
- **Checkpoint / Resume**：记录当前目标、下一步、关键文件 freshness 和运行环境信息，支持中断后的可信恢复。
- **Run Artifacts**：每次请求都会落盘 task state、trace 和 report，方便调试、复盘和指标聚合。
- **Evaluation**：提供 benchmark、scripted baseline、verifier 和 metrics 聚合能力，用于验证 harness 行为和不同机制的收益。

## Architecture

一次请求在 Coda 中大致会经过下面的链路：

```text
CLI
  -> build agent
  -> build workspace context
  -> create or resume session
  -> Pico/Coda runtime ask()
  -> build prompt
  -> call model
  -> parse model output
  -> run tool or return final answer
  -> write history / memory / checkpoint / trace / report
```

其中 runtime 是核心调度层，负责把模型输出从普通文本转换成结构化控制流。模型不会直接操作文件系统或 shell，只能输出约定格式的工具调用；runtime 会在执行前统一做校验、审批、重复调用拦截和工作区边界检查。

## Repository Layout

```text
coda/
  cli.py              # 命令行入口与 agent 装配
  runtime.py          # 主循环、prompt 构建、工具编排、状态落盘
  tools.py            # 工具定义、参数校验和具体执行逻辑
  workspace.py        # 工作区上下文采集与仓库基线生成
  context_manager.py  # prompt 分区、历史压缩和预算控制
  memory.py           # 工作记忆、文件摘要、过程笔记和长期记忆
  task_state.py       # 单次任务运行状态
  run_store.py        # run 目录、trace 和 report 写入
  models.py           # 模型 provider 适配
  evaluator.py        # benchmark 执行
  metrics.py          # 指标聚合与实验

tests/                # 单元测试与安全边界测试
benchmarks/           # benchmark 任务定义
scripts/              # 实验脚本和指标收集脚本
assets/               # README 截图资源
```

## Installation

需要 Python 3.10+。

使用 `uv` 安装依赖：

```bash
uv sync
```

也可以使用可编辑模式安装：

```bash
pip install -e .
```

## Quick Start

在当前仓库启动交互模式：

```bash
uv run coda
```

指定工作目录：

```bash
uv run coda --cwd /path/to/repo
```

执行一次性任务：

```bash
uv run coda --cwd /path/to/repo "inspect the test failures and propose a fix"
```

恢复最近一次会话：

```bash
uv run coda --resume latest
```

查看命令行参数：

```bash
uv run coda --help
```

## Providers

Coda 将不同模型服务封装成统一的 `complete()` 接口。当前支持以下 provider 入口：

- `ollama`
- `openai`
- `anthropic`
- `deepseek`

示例：

```bash
uv run coda --provider openai
uv run coda --provider anthropic
uv run coda --provider deepseek
uv run coda --provider ollama
```

配置可以放在项目根目录的 `.env` 中。真实 API Key 不应提交到 Git 仓库。

常见配置项：

```env
CODA_OPENAI_API_BASE=
CODA_OPENAI_API_KEY=
CODA_OPENAI_MODEL=

CODA_ANTHROPIC_API_BASE=
CODA_ANTHROPIC_API_KEY=
CODA_ANTHROPIC_MODEL=

CODA_DEEPSEEK_API_BASE=
CODA_DEEPSEEK_API_KEY=
CODA_DEEPSEEK_MODEL=
```

## Tools

当前工具表面是显式白名单，不做动态工具发现：

| Tool | Description | Risk |
| --- | --- | --- |
| `list_files` | 列出工作区目录 | low |
| `read_file` | 按行读取 UTF-8 文件 | low |
| `search` | 搜索工作区文本 | low |
| `run_shell` | 在仓库根目录执行 shell 命令 | high |
| `write_file` | 写入文本文件 | high |
| `patch_file` | 对文件做精确文本替换 | high |
| `delegate` | 启动受限只读子 agent 调查问题 | low |

高风险工具会受到审批策略影响：

```bash
uv run coda --approval ask
uv run coda --approval auto
uv run coda --approval never
```

## Runtime State

Coda 会在工作区下创建 `.coda/` 目录，用于保存本地状态：

```text
.coda/
  sessions/           # 会话状态，包括 history、memory、checkpoint
  runs/               # 每次 ask() 的运行工件
  memory/             # 长期记忆主题
```

每次请求都会创建一个独立 run：

```text
.coda/runs/<run_id>/
  task_state.json     # 当前任务状态快照
  trace.jsonl         # 逐事件运行日志
  report.json         # 运行结束后的摘要报告
```

这部分文件主要用于调试、复盘和实验统计。默认情况下不建议提交到 Git。

## Context Management

Coda 的 prompt 由几个稳定 section 组成：

```text
prefix
memory
relevant_memory
history
current_request
```

其中 `prefix` 包含 agent 规则、工具说明和工作区摘要；`memory` 保存当前任务相关的短期状态；`history` 保存会话过程；`current_request` 保留用户当前请求。

当 prompt 超过预算时，系统会优先压缩相关记忆和旧历史，最后才动稳定前缀；当前请求不会被裁剪。

## Memory

工作记忆主要包含：

- `task_summary`：当前任务摘要。
- `recent_files`：最近访问过的文件。
- `file_summaries`：文件短摘要。
- `episodic_notes`：过程笔记。

文件摘要会记录 freshness，也就是文件内容哈希。文件变化后，旧摘要会失效，避免恢复或后续推理时误信过期信息。

## Checkpoint and Resume

checkpoint 用于保存任务执行中的关键现场，包括当前目标、下一步、关键文件和 freshness。恢复会话时，Coda 会重新比较文件 freshness、workspace fingerprint、tool signature 和 runtime identity。

如果旧状态仍然可信，任务可以继续推进；如果工作区或运行环境已经变化，恢复状态会被标记出来，后续需要重新确认现场。

## Evaluation

项目内置了 benchmark 和 metrics 相关模块，用于验证 harness 行为：

- benchmark 定义固定任务、fixture、工具预算、期望产物和 verifier。
- evaluator 在隔离副本中执行任务，避免污染原始 fixture。
- metrics 聚合 benchmark artifact 和 run artifact，统计通过率、工具步数、缓存命中、安全事件和停止原因等指标。

这使得项目不只停留在“能跑”，也能对上下文治理、记忆收益、恢复正确性和工具安全边界做对照验证。

## Development

运行测试：

```bash
pytest
```

或：

```bash
uv run pytest
```

语法检查：

```bash
python -m py_compile coda/*.py
```

如果安装了 Ruff：

```bash
uv run ruff check .
```

## Notes

Coda 当前更关注本地代码 agent harness 的核心运行链路，因此没有接入完整 MCP 工具生态，也没有做重型 OS 级沙箱。工具集合、路径边界、审批策略、上下文治理和运行工件是当前版本的主要设计重点。

后续可以继续扩展的方向包括：

- 更细粒度的上下文 token 预算。
- 更强的长期记忆管理。
- 更完整的 provider cache 策略。
- 更严格的 shell 隔离机制。
- 更丰富的 benchmark 场景。

