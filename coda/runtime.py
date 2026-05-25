"""Agent 运行时核心逻辑。

Coda 就是包在模型外面的控制循环：负责组 prompt、解析模型输出、
校验并执行工具、写 trace、更新工作记忆，以及在合适的时候停下来。
"""

import json
import os
import re
import textwrap
import uuid
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import memory as memorylib
from .context_manager import ContextManager
from .run_store import RunStore
from .task_state import TaskState
from . import tools as toolkit
from .workspace import IGNORED_PATH_NAMES, MAX_HISTORY, WorkspaceContext, clip, now

SENSITIVE_ENV_NAME_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
REDACTED_VALUE = "<redacted>"

# shell_env_allowlist 是“允许传给 shell 命令的环境变量名单”。
# 为什么需要白名单：
# 运行 shell 命令时，如果直接继承整个系统环境，里面可能有各种 API key。
# Coda 只把 HOME、PATH、TEMP 这类基础变量传进去，降低密钥泄露到 trace/report 的风险。
DEFAULT_SHELL_ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "PATH", "PWD", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER")

# feature_flags 是运行时功能开关。
# 初学时可以把它理解成“方便做实验的总开关”：
# - memory: 是否启用工作记忆
# - relevant_memory: 是否按当前问题召回相关记忆
# - context_reduction: prompt 超预算时是否压缩上下文
# - prompt_cache: 是否启用后端 prompt cache 相关链路
DEFAULT_FEATURE_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    "context_reduction": True,
    "prompt_cache": True,
}

# checkpoint 是“可恢复现场”的快照。
# 它不是完整复制仓库，而是记录当前任务目标、下一步、关键文件 freshness 等信息。
# schema_version 用来防止旧版本 checkpoint 被新 runtime 误当成可信状态。
CHECKPOINT_SCHEMA_VERSION = "phase1-v1"
CHECKPOINT_NONE_STATUS = "no-checkpoint"
CHECKPOINT_FULL_VALID_STATUS = "full-valid"
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
CHECKPOINT_SCHEMA_MISMATCH_STATUS = "schema-mismatch"

# durable memory 是“长期记忆”：只有用户明确说“记住/保存/记录”时，
# Coda 才会把最终答案里的稳定事实沉淀到 .coda/memory/。
DURABLE_MEMORY_INTENT_PATTERN = re.compile(r"(?i)\b(capture|remember|save|store|persist|note)\b")
DURABLE_MEMORY_INTENT_ZH_PATTERN = re.compile(r"(记住|保存|记录|沉淀|长期记忆|持久记忆)")
DURABLE_MEMORY_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
)
SECRET_SHAPED_TEXT_PATTERN = re.compile(r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})")


@dataclass
class PromptPrefix:
    # prefix 除了文本本身，还带一小份元数据，
    # 这样 runtime 才能明确判断 prefix 是否可以复用。
    text: str
    hash: str
    workspace_fingerprint: str
    tool_signature: str
    built_at: str


class SessionStore:
    """负责保存和读取 session。

    session 可以理解成“长期会话文件”，里面有 history、memory、checkpoint。
    它和 run 不一样：
    - session：跨多次用户输入继续存在
    - run：一次 ask() 的执行过程
    """

    # 初始化当前对象，保存后续方法需要使用的基础状态。
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # 处理 path 相关路径，确保文件访问不越过工作区边界。
    def path(self, session_id):
        # 每个 session id 对应一个 JSON 文件。
        return self.root / f"{session_id}.json"

    # 读写 save 相关的持久化数据，保证状态可以复用。
    def save(self, session):
        # 保存的是可恢复状态，不是完整运行过程。
        # 完整过程记录由 RunStore 写到 .coda/runs/<run_id>/。
        path = self.path(session["id"])
        path.write_text(json.dumps(session, indent=2), encoding="utf-8")
        return path

    # 处理 load 相关逻辑，支撑当前模块的主要流程。
    def load(self, session_id):
        # --resume <session_id> 最终会走到这里。
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    # 处理 latest 相关逻辑，支撑当前模块的主要流程。
    def latest(self):
        # --resume latest 用文件修改时间找最近保存过的 session。
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None


class Coda:
    # 初始化当前对象，保存后续方法需要使用的基础状态。
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        run_store=None,
        approval_policy="ask",
        max_steps=6,
        max_new_tokens=512,
        depth=0,
        max_depth=1,
        read_only=False,
        shell_env_allowlist=None,
        secret_env_names=None,
        feature_flags=None,
    ):
        # model_client 是“模型适配器”。
        # runtime 不关心底层是 Ollama、OpenAI-compatible 还是 Anthropic-compatible，
        # 只要求它提供 complete(prompt, max_new_tokens) 这样的统一入口。
        self.model_client = model_client

        # workspace 是当前仓库的基础地图，由 WorkspaceContext.build() 创建。
        # self.root 是所有文件工具的安全边界，后续 path() 会确保读写不逃出这里。
        self.workspace = workspace
        self.root = Path(workspace.repo_root)

        # session_store 管跨轮会话；run_store 管单次运行工件。
        # 两者分开后，恢复上下文和复盘某一次执行不会混在一起。
        self.session_store = session_store

        # approval_policy 控制高风险工具：
        # ask = 执行前问用户；auto = 自动允许；never = 永不允许。
        self.approval_policy = approval_policy
        self.max_steps = max_steps
        self.max_new_tokens = max_new_tokens

        # depth/max_depth 用来限制 delegate 子 agent 的递归深度。
        # 这样模型不能无限创建子 agent。
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only

        # shell 命令默认只拿到白名单环境变量，避免把整套父进程环境暴露给工具。
        self.shell_env_allowlist = tuple(shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST)
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update({str(key): bool(value) for key, value in feature_flags.items()})
        self.run_store = run_store or RunStore(Path(workspace.repo_root) / ".coda" / "runs")

        # 如果调用方没有传入旧 session，就新建一个。
        # history 记录完整对话/工具事件；memory 是更短的工作记忆层。
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": memorylib.default_memory_state(),
        }

        # 兼容旧 session：磁盘上的 session 可能缺少新字段，
        # 所以初始化时先把结构补齐。
        self._ensure_session_shape()

        # LayeredMemory 是对 session["memory"] 的包装，提供 remember_file、
        # set_file_summary、retrieval_candidates 等更好用的方法。
        self.memory = memorylib.LayeredMemory(
            self.session.setdefault("memory", memorylib.default_memory_state()),
            workspace_root=self.root,
        )
        self.session["memory"] = self.memory.to_dict()

        # tools 是模型能请求的动作白名单。
        # prefix 是模型每轮都会看到的“工作手册 + 当前仓库摘要”。
        self.tools = self.build_tools()
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text

        # ContextManager 负责把 prefix、memory、history、当前请求组装成最终 prompt。
        self.context_manager = ContextManager(self)

        # resume_state 评估当前 session/checkpoint 是否还能信任：
        # 例如关键文件是否变旧、runtime 配置是否漂移。
        self.resume_state = self.evaluate_resume_state()
        self.session_path = self.session_store.save(self.session)

        # 下面这些 last_* 字段用于 report/trace 汇总。
        # 它们不是业务状态，而是“最近一次运行/最近一轮模型调用”的观察指标。
        self.current_task_state = None
        self.current_run_dir = None
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self._last_tool_result_metadata = {}
        self._last_prefix_refresh = {
            "workspace_changed": False,
            "prefix_changed": False,
        }

    # 处理 from session 相关逻辑，支撑当前模块的主要流程。
    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=session_store.load(session_id),
            **kwargs,
        )

    # 处理 ensure session shape 相关逻辑，支撑当前模块的主要流程。
    def _ensure_session_shape(self):
        """把 session 规范化成当前 runtime 需要的结构。

        为什么要做：
        session 是落盘 JSON，可能来自旧版本，也可能被手工编辑过。
        runtime 后面会直接访问 history、memory、checkpoints 等字段，
        所以这里先给缺失字段补默认值，避免主循环到一半 KeyError。
        """
        self.session.setdefault("history", [])
        self.session.setdefault("memory", memorylib.default_memory_state())
        checkpoints = self.session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            self.session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        runtime_identity = self.session.setdefault("runtime_identity", {})
        if not isinstance(runtime_identity, dict):
            self.session["runtime_identity"] = {}
        resume_state = self.session.setdefault("resume_state", {})
        if not isinstance(resume_state, dict):
            self.session["resume_state"] = {}

    # 处理 current runtime identity 相关逻辑，支撑当前模块的主要流程。
    def current_runtime_identity(self):
        """返回“这次 runtime 是怎么启动的”。

        这份身份信息会写进 checkpoint。
        下次 resume 时，如果模型、审批策略、工作区指纹等变化了，
        Coda 就能判断：旧 checkpoint 可能已经不能完全信任。
        """
        return {
            "session_id": self.session.get("id", ""),
            "cwd": str(self.root),
            "model": str(getattr(self.model_client, "model", "")),
            "model_client": self.model_client.__class__.__name__,
            "approval_policy": self.approval_policy,
            "read_only": bool(self.read_only),
            "max_steps": int(self.max_steps),
            "max_new_tokens": int(self.max_new_tokens),
            "feature_flags": dict(self.feature_flags),
            "shell_env_allowlist": list(self.shell_env_allowlist),
            "workspace_fingerprint": getattr(getattr(self, "prefix_state", None), "workspace_fingerprint", self.workspace.fingerprint()),
            "tool_signature": self.tool_signature(),
        }

    # 处理 checkpoint state 相关逻辑，支撑当前模块的主要流程。
    def checkpoint_state(self):
        self._ensure_session_shape()
        return self.session["checkpoints"]

    # 处理 current checkpoint 相关逻辑，支撑当前模块的主要流程。
    def current_checkpoint(self):
        state = self.checkpoint_state()
        checkpoint_id = str(state.get("current_id", "")).strip()
        if not checkpoint_id:
            return None
        return state.get("items", {}).get(checkpoint_id)

    # 维护 invalidate stale memory 相关记忆数据，帮助 agent 减少重复确认。
    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    # 评估恢复现场是否仍然可信，避免误用过期状态。
    def evaluate_resume_state(self):
        """评估当前 session 的恢复状态。

        初学者可以把它理解成一个“恢复前体检”：
        - 没有 checkpoint：no-checkpoint
        - checkpoint 版本太旧：schema-mismatch
        - 关键文件内容变了：partial-stale
        - runtime 配置/工作区状态变了：workspace-mismatch
        - 都没问题：full-valid
        """
        previous_resume_state = dict(self.session.get("resume_state", {}) or {})
        invalidated = self.invalidate_stale_memory()
        checkpoint = self.current_checkpoint()
        status = CHECKPOINT_NONE_STATUS
        stale_paths = list(invalidated)
        mismatch_fields = []
        if checkpoint:
            if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                status = CHECKPOINT_SCHEMA_MISMATCH_STATUS
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    expected = item.get("freshness")
                    current = memorylib.file_freshness(path, self.root)
                    # freshness 是文件内容 hash。
                    # 如果 checkpoint 记录的 hash 和当前文件 hash 不一致，
                    # 说明旧摘要可能过期，下一轮 prompt 需要提醒模型重新确认。
                    if expected != current and path not in stale_paths:
                        stale_paths.append(path)
                saved_identity = dict(checkpoint.get("runtime_identity", {}) or self.session.get("runtime_identity", {}) or {})
                current_identity = self.current_runtime_identity()
                identity_keys = (
                    "cwd",
                    "model",
                    "model_client",
                    "approval_policy",
                    "read_only",
                    "max_steps",
                    "max_new_tokens",
                    "feature_flags",
                    "shell_env_allowlist",
                    "workspace_fingerprint",
                    "tool_signature",
                )
                for key in identity_keys:
                    if key not in saved_identity:
                        continue
                    if saved_identity.get(key) != current_identity.get(key):
                        # runtime_identity mismatch 不一定代表不能继续，
                        # 但它代表恢复现场的条件变了，trace 里需要留下证据。
                        mismatch_fields.append(key)
                mismatch_fields.sort()
                if stale_paths:
                    status = CHECKPOINT_PARTIAL_STALE_STATUS
                elif mismatch_fields:
                    status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS
                else:
                    status = CHECKPOINT_FULL_VALID_STATUS

        resume_state = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatch_fields,
            "stale_summary_invalidations": max(
                len(invalidated),
                int(previous_resume_state.get("stale_summary_invalidations", 0))
                if status == CHECKPOINT_PARTIAL_STALE_STATUS
                else 0,
            ),
        }
        self.session["resume_state"] = resume_state
        self.session["runtime_identity"] = self.current_runtime_identity()
        return resume_state

    # 渲染 render checkpoint text 对应的文本内容，用于 prompt、trace 或报告展示。
    def render_checkpoint_text(self):
        checkpoint = self.current_checkpoint()
        if not checkpoint:
            return ""
        lines = [
            "Task checkpoint:",
            f"- Resume status: {self.resume_state.get('status', CHECKPOINT_NONE_STATUS)}",
            f"- Current goal: {checkpoint.get('current_goal', '-') or '-'}",
            f"- Current blocker: {checkpoint.get('current_blocker', '-') or '-'}",
            f"- Next step: {checkpoint.get('next_step', '-') or '-'}",
        ]
        key_files = [str(item.get("path", "")).strip() for item in checkpoint.get("key_files", []) if str(item.get("path", "")).strip()]
        lines.append(f"- Key files: {', '.join(key_files) or '-'}")
        if checkpoint.get("completed"):
            lines.append("- Completed: " + " | ".join(str(item) for item in checkpoint.get("completed", [])))
        if checkpoint.get("excluded"):
            lines.append("- Excluded: " + " | ".join(str(item) for item in checkpoint.get("excluded", [])))
        if self.resume_state.get("stale_paths"):
            lines.append("- Stale paths: " + ", ".join(self.resume_state["stale_paths"]))
        summary = str(checkpoint.get("summary", "")).strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        return "\n".join(lines)

    # 维护 remember 相关记忆数据，帮助 agent 减少重复确认。
    @staticmethod
    def remember(bucket, item, limit):
        if not item:
            return
        if item in bucket:
            bucket.remove(item)
        bucket.append(item)
        del bucket[:-limit]

    # 构建 build tools 需要的数据或对象，供后续流程继续使用。
    def build_tools(self):
        # 真正的工具实现放在 tools.py。
        # runtime 这里只负责向模型暴露“当前允许使用哪些工具”。
        return toolkit.build_tool_registry(self)

    # 执行 tool signature 工具逻辑，并把受控结果返回给 runtime。
    def tool_signature(self):
        """给当前工具清单算一个指纹。

        工具名、参数 schema、风险等级、描述只要变了，signature 就会变。
        这样 checkpoint/prefix cache 能知道：模型看到的工具合同是否和之前一致。
        """
        payload = []
        for name in sorted(self.tools):
            tool = self.tools[name]
            payload.append(
                {
                    "name": name,
                    "schema": tool["schema"],
                    "risky": tool["risky"],
                    "description": tool["description"],
                }
            )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    # 构建稳定 prompt 前缀，包含 agent 规则、工具表面和工作区摘要。
    def build_prefix(self):
        """构建模型每轮都会看到的稳定前缀。

        prefix 可以理解成给模型的“上岗手册”，它包含：
        1. Coda 的工作规则；
        2. 可以使用的工具清单；
        3. 工具调用格式示例；
        4. 当前工作区摘要。

        注意：用户当前请求不在 prefix 里。当前请求会由 ContextManager 放到
        prompt 的最后，这样模型最容易看到本轮真正要做什么。
        """
        tool_lines = []
        for name, tool in self.tools.items():
            fields = ", ".join(f"{key}: {value}" for key, value in tool["schema"].items())
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        tool_text = "\n".join(tool_lines)
        examples = "\n".join(
            [
                '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
                '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
                '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
                '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
                "<final>Done.</final>",
            ]
        )
        # prefix 可以理解成 agent 的“工作手册”：
        # 它是谁、工具怎么调用、当前仓库是什么状态，都写在这里。
        text = textwrap.dedent(
            f"""\
            You are coda, a small local coding agent working inside a local repository.

            Rules:
            - Use tools instead of guessing about the workspace.
            - Return exactly one <tool>...</tool> or one <final>...</final>.
            - Tool calls must look like:
              <tool>{{"name":"tool_name","args":{{...}}}}</tool>
            - For write_file and patch_file with multi-line text, prefer XML style:
              <tool name="write_file" path="file.py"><content>...</content></tool>
            - Final answers must look like:
              <final>your answer</final>
            - Never invent tool results.
            - Keep answers concise and concrete.
            - If the user asks you to create or update a specific file and the path is clear, use write_file or patch_file instead of repeatedly listing files.
            - Before writing tests for existing code, read the implementation first.
            - When writing tests, match the current implementation unless the user explicitly asked you to change the code.
            - New files should be complete and runnable, including obvious imports.
            - Do not repeat the same tool call with the same arguments if it did not help. Choose a different tool or return a final answer.
            - Required tool arguments must not be empty. Do not call read_file, write_file, patch_file, run_shell, or delegate with args={{}}.

            Tools:
            {tool_text}

            Valid response examples:
            {examples}

            {self.workspace.text()}
            """
        ).strip()
        return PromptPrefix(
            text=text,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            workspace_fingerprint=self.workspace.fingerprint(),
            tool_signature=self.tool_signature(),
            built_at=now(),
        )

    # 处理 apply prefix state 相关逻辑，支撑当前模块的主要流程。
    def _apply_prefix_state(self, prefix_state):
        self.prefix_state = prefix_state
        self.prefix = prefix_state.text

    # 检查工作区和工具表面是否变化，必要时重建 prompt 前缀。
    def refresh_prefix(self, force=False):
        """在每轮 prompt 前刷新仓库基线和 prefix。

        为什么不是启动时只建一次：
        agent 可能刚刚 patch 了文件、跑了命令、改变了 git status。
        下一轮模型应该看到最新工作区状态。

        为什么又不是无脑重建：
        prefix 可能很长，而且后端可能支持 prompt cache。
        所以这里先算 workspace fingerprint；只有工作区摘要真的变了，
        才重建 prefix。
        """
        previous_hash = getattr(getattr(self, "prefix_state", None), "hash", None)
        previous_workspace_fingerprint = getattr(getattr(self, "prefix_state", None), "workspace_fingerprint", None)

        # 工作区事实相对稳定，所以这里按整体刷新；
        # 只有这些事实真的变化了，才重建完整 prefix。
        refreshed_workspace = WorkspaceContext.build(self.root)
        refreshed_workspace_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = force or refreshed_workspace_fingerprint != previous_workspace_fingerprint
        if workspace_changed:
            self.workspace = refreshed_workspace

        prefix_state = self.build_prefix() if workspace_changed or force or previous_hash is None else self.prefix_state
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self._apply_prefix_state(prefix_state)

        self._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self._last_prefix_refresh)

    # 渲染 memory text 对应的文本内容，用于 prompt、trace 或报告展示。
    def memory_text(self):
        return self.memory.render_memory_text()

    # 渲染 history text 对应的文本内容，用于 prompt、trace 或报告展示。
    def history_text(self):
        """把 session history 渲染成给模型看的文字。

        history 是完整事件流的一部分，但不能无脑全量塞进 prompt。
        这里会对旧 read_file 结果做折叠，对最近几轮保留更多细节，
        目的是让模型既能接上上下文，又不被过长历史拖垮。
        """
        history = self.session["history"]
        if not history:
            return "- empty"

        lines = []
        seen_reads = set()
        recent_start = max(0, len(history) - 6)
        for index, item in enumerate(history):
            recent = index >= recent_start
            if item["role"] == "tool" and item["name"] == "read_file" and not recent:
                path = str(item["args"].get("path", ""))
                if path in seen_reads:
                    continue
                seen_reads.add(path)

            if item["role"] == "tool":
                limit = 900 if recent else 180
                lines.append(f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}")
                lines.append(clip(item["content"], limit))
            else:
                limit = 900 if recent else 220
                lines.append(f"[{item['role']}] {clip(item['content'], limit)}")

        return clip("\n".join(lines), MAX_HISTORY)

    # 处理 feature enabled 相关逻辑，支撑当前模块的主要流程。
    def feature_enabled(self, name):
        return bool(self.feature_flags.get(str(name), False))

    # 组装 prompt 相关 prompt 内容，让模型拿到必要上下文。
    def prompt(self, user_message):
        prompt, _ = self._build_prompt_and_metadata(user_message)
        return prompt

    # 把一条会话历史写入 session，供后续 prompt 和恢复使用。
    def record(self, item):
        # 所有 user/assistant/tool 事件都会进 session history。
        # 每次 record 后立刻保存 session，保证中途出错也能恢复到较新的状态。
        self.session["history"].append(item)
        self.session_path = self.session_store.save(self.session)

    # 检查 looks sensitive env name 相关条件是否成立，用于提前拦截异常情况。
    @staticmethod
    def looks_sensitive_env_name(name):
        upper = str(name).upper()
        return any(upper == marker or upper.endswith(marker) or upper.endswith(f"_{marker}") for marker in SENSITIVE_ENV_NAME_MARKERS)

    # 检查 is secret env name 相关条件是否成立，用于提前拦截异常情况。
    def is_secret_env_name(self, name):
        upper = str(name).upper()
        return upper in self.secret_env_names or self.looks_sensitive_env_name(upper)

    # 处理 configured secret env items 相关逻辑，支撑当前模块的主要流程。
    def configured_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if str(name).upper() in self.secret_env_names and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    # 处理 detected secret env items 相关逻辑，支撑当前模块的主要流程。
    def detected_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if self.is_secret_env_name(name) and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    # 处理 secret env summary 相关逻辑，支撑当前模块的主要流程。
    def secret_env_summary(self):
        names = [name for name, _ in self.configured_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    # 处理 detected secret env summary 相关逻辑，支撑当前模块的主要流程。
    def detected_secret_env_summary(self):
        names = [name for name, _ in self.detected_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    # 处理 redact text 相关逻辑，支撑当前模块的主要流程。
    def redact_text(self, text):
        text = str(text)
        for _, value in sorted(self.detected_secret_env_items(), key=lambda item: len(item[1]), reverse=True):
            text = text.replace(value, REDACTED_VALUE)
        return text

    # 处理 redact artifact 相关逻辑，支撑当前模块的主要流程。
    def redact_artifact(self, value, key=None):
        if key and self.is_secret_env_name(key):
            return REDACTED_VALUE
        if isinstance(value, dict):
            return {
                str(item_key): self.redact_artifact(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, tuple):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, str):
            redacted = self.redact_text(value)
            return redacted
        return value

    # 处理 shell env 相关逻辑，支撑当前模块的主要流程。
    def shell_env(self):
        """构造给 run_shell 使用的环境变量。

        初学者容易误会：run_shell 不是直接继承当前 PowerShell 的所有环境。
        Coda 只传白名单变量，并强制设置 PWD 到仓库根目录。
        """
        env = {
            name: os.environ[name]
            for name in self.shell_env_allowlist
            if name in os.environ
        }
        env["PWD"] = str(self.root)
        if "PATH" not in env and os.environ.get("PATH"):
            env["PATH"] = os.environ["PATH"]
        return env

    # 组装 prompt metadata 相关 prompt 内容，让模型拿到必要上下文。
    def prompt_metadata(self, user_message, prompt):
        # prompt 参数保留在签名里是历史兼容；实际 metadata 重新按当前状态构建。
        # del 不写也没问题，这里函数只返回 metadata。
        _, metadata = self._build_prompt_and_metadata(user_message)
        return metadata

    # 构建本轮发给模型的 prompt，并收集用于 trace 和 report 的元数据。
    def _build_prompt_and_metadata(self, user_message):
        """构建本轮发给模型的 prompt，并返回解释这份 prompt 的 metadata。

        prompt 是模型真正看到的文本。
        metadata 是给 runtime/trace/report/benchmark 看的解释信息，比如：
        - prompt 多长；
        - prefix 是否变了；
        - workspace fingerprint 是多少；
        - 是否触发上下文压缩；
        - resume 状态是否 stale/mismatch。
        """
        # 第一步：先刷新 workspace/prefix。
        # 这保证模型看到的仓库状态尽量接近当前真实状态。
        refresh = self.refresh_prefix()

        # 第二步：重新评估恢复状态。
        # 如果关键文件被外部修改，resume_state 会在这里变成 partial-stale。
        self.resume_state = self.evaluate_resume_state()

        # 第三步：交给 ContextManager 组装最终 prompt。
        # 它会按预算拼 prefix、memory、relevant memory、history、当前请求。
        prompt, metadata = self.context_manager.build(user_message)
        # 这里把“这轮 prompt 是怎么拼出来的”连同缓存相关状态一起记下来，
        # 后面 trace/report 才能解释清楚：为什么这一轮 prefix 变了、缓存有没有命中。
        metadata.update(
            {
                "prefix_chars": len(self.prefix),
                "workspace_chars": len(self.workspace.text()),
                "memory_chars": len(self.memory_text()),
                "history_chars": len(self.history_text()),
                "request_chars": len(user_message),
                "tool_count": len(self.tools),
                "workspace_docs": len(self.workspace.project_docs),
                "recent_commits": len(self.workspace.recent_commits),
                "prefix_hash": self.prefix_state.hash,
                "prompt_cache_key": self.prefix_state.hash,
                "workspace_fingerprint": self.prefix_state.workspace_fingerprint,
                "tool_signature": self.prefix_state.tool_signature,
                "workspace_changed": refresh["workspace_changed"],
                "prefix_changed": refresh["prefix_changed"],
                "prompt_cache_supported": bool(getattr(self.model_client, "supports_prompt_cache", False)),
                "resume_status": self.resume_state.get("status", CHECKPOINT_NONE_STATUS),
                "stale_summary_invalidations": int(self.resume_state.get("stale_summary_invalidations", 0)),
                "stale_paths": list(self.resume_state.get("stale_paths", [])),
                "runtime_identity_mismatch_fields": list(self.resume_state.get("runtime_identity_mismatch_fields", [])),
            }
        )
        metadata.update(self.detected_secret_env_summary())
        return prompt, metadata

    # 写入一条运行 trace 事件，方便后续复盘和调试。
    def emit_trace(self, task_state, event, payload=None):
        """写一条 trace 事件。

        trace 是运行过程的时间线。每条事件先脱敏，再追加到 trace.jsonl。
        以后排查“这次 agent 到底做了什么”，主要就是看它。
        """
        payload = self.redact_artifact(payload or {})
        payload["event"] = event
        payload["created_at"] = now()
        # trace 是运行中的逐事件时间线，适合回答“这一轮 agent 到底做了什么”。
        self.run_store.append_trace(task_state, payload)
        return payload

    # 处理 capture workspace snapshot 相关逻辑，支撑当前模块的主要流程。
    def capture_workspace_snapshot(self):
        """给当前工作区文件内容做一个轻量快照。

        这里只保存 path -> 文件内容 hash，不保存文件内容本身。
        目的不是做版本控制，而是在高风险工具执行前后比较：
        这次工具到底改了哪些文件？
        """
        snapshot = {}
        for path in self.root.rglob("*"):
            try:
                relative_parts = path.relative_to(self.root).parts
            except ValueError:
                continue
            if any(part in IGNORED_PATH_NAMES for part in relative_parts):
                continue
            if not path.is_file():
                continue
            try:
                snapshot[path.relative_to(self.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception:
                continue
        return snapshot

    # 处理 diff workspace snapshots 相关逻辑，支撑当前模块的主要流程。
    @staticmethod
    def diff_workspace_snapshots(before, after):
        """比较两个工作区快照，返回变动文件和摘要。"""
        changed_paths = []
        summaries = []
        all_paths = sorted(set(before) | set(after))
        for path in all_paths:
            if before.get(path) == after.get(path):
                continue
            changed_paths.append(path)
            if path not in before:
                summaries.append(f"created:{path}")
            elif path not in after:
                summaries.append(f"deleted:{path}")
            else:
                summaries.append(f"modified:{path}")
        return changed_paths, summaries

    # 创建当前任务 checkpoint，记录目标、关键文件和下一步线索。
    def create_checkpoint(self, task_state, user_message, trigger):
        """创建一个可恢复 checkpoint。

        checkpoint 不是完整历史，也不是完整仓库备份。
        它记录的是“如果下一次要接着做，模型需要知道的最低恢复信息”：
        当前目标、下一步、关键文件、文件 freshness、runtime identity。
        """
        state = self.checkpoint_state()
        current = self.current_checkpoint()
        checkpoint_id = "ckpt_" + uuid.uuid4().hex[:8]
        key_files = []
        freshness = {}
        for path in self.memory.to_dict()["working"]["recent_files"]:
            file_freshness = memorylib.file_freshness(path, self.root)
            freshness[path] = file_freshness
            key_files.append({"path": path, "freshness": file_freshness})
        # parent_checkpoint_id 形成一条简单链路，方便知道本 checkpoint 是从哪个现场继续来的。
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "created_at": now(),
            "current_goal": str(user_message),
            "completed": [task_state.final_answer] if task_state.final_answer else [],
            "excluded": [],
            "current_blocker": "" if str(task_state.stop_reason or "") in ("", "final_answer_returned") else str(task_state.stop_reason),
            "next_step": self.infer_next_step(task_state),
            "key_files": key_files,
            "freshness": freshness,
            "summary": f"{trigger}: {clip(str(user_message), 120)}",
            "runtime_identity": self.current_runtime_identity(),
        }
        state["items"][checkpoint_id] = checkpoint
        state["current_id"] = checkpoint_id
        task_state.checkpoint_id = checkpoint_id
        self.session["runtime_identity"] = checkpoint["runtime_identity"]
        self.session_path = self.session_store.save(self.session)
        return checkpoint

    # 处理 infer next step 相关逻辑，支撑当前模块的主要流程。
    def infer_next_step(self, task_state):
        # 这里不是让模型做复杂计划，只是给 checkpoint 留一个很粗的续跑提示。
        if task_state.status == "completed":
            return "No next step recorded."
        if task_state.stop_reason == "step_limit_reached":
            return "Resume from the latest checkpoint and continue the task."
        if task_state.last_tool:
            return f"Decide the next action after {task_state.last_tool}."
        return "Continue the task from the latest checkpoint."

    # 根据工具结果更新工作记忆，仅沉淀后续可能有用的少量信息。
    def update_memory_after_tool(self, name, args, result):
        """把少量高价值工具结果沉淀到 working memory。

        为什么存在：
        并不是每个工具结果都值得长期带进下一轮 prompt。完整结果已经进了
        `history`，这里只挑少量“下一轮大概率还会用到”的事实做提纯，
        例如最近读写过哪些文件、某个文件读出来的短摘要。

        输入 / 输出：
        - 输入：工具名 `name`、参数 `args`、执行结果 `result`
        - 输出：无显式返回值，副作用是更新 `self.memory`

        在 agent 链路里的位置：
        它发生在 `run_tool()` 真正执行完工具之后、下一轮 prompt 组装之前。
        也就是说：工具结果先进入完整历史，再由这个函数择优沉淀成轻量记忆。
        """
        if not self.feature_enabled("memory"):
            return
        path = args.get("path")
        if not path:
            return

        canonical_path = self.memory.canonical_path(path)
        # 不是所有工具结果都进入工作记忆。
        # 读文件会生成摘要；写文件/patch 会让旧摘要失效，因为它们可能过期了。
        if name in {"read_file", "write_file", "patch_file"}:
            self.memory.remember_file(canonical_path)
        if name == "read_file":
            summary = memorylib.summarize_read_result(result)
            self.memory.set_file_summary(canonical_path, summary)
            self.memory.append_note(summary, tags=(canonical_path,), source=canonical_path)
        elif name in {"write_file", "patch_file"}:
            self.memory.invalidate_file_summary(canonical_path)

    # 更新 note tool 对应的运行状态，让任务进度可追踪。
    def note_tool(self, name, args, result):
        self.update_memory_after_tool(name, args, result)

    # 更新 record process note for tool 对应的运行状态，让任务进度可追踪。
    def record_process_note_for_tool(self, name, metadata):
        status = str(metadata.get("tool_status", "")).strip()
        if status not in {"partial_success", "error", "rejected"}:
            return
        affected_paths = [str(path).strip() for path in metadata.get("affected_paths", []) if str(path).strip()]
        path_text = ", ".join(affected_paths) or "workspace"
        if status == "partial_success":
            text = f"{name} partial_success on {path_text}; inspect diff before retry"
        elif status == "error":
            text = f"{name} error on {path_text}; check the failure before retry"
        else:
            text = f"{name} rejected; choose a different action before retry"
        tags = ["process", status, *affected_paths]
        self.memory.append_note(text, tags=tuple(tags), source=name, kind="process")
        self.session["memory"] = self.memory.to_dict()

    # 处理 reject durable reason 相关逻辑，支撑当前模块的主要流程。
    def reject_durable_reason(self, note_text):
        text = str(note_text or "").strip()
        lowered = text.lower()
        if not text:
            return "empty"
        if REDACTED_VALUE in text or SECRET_SHAPED_TEXT_PATTERN.search(text):
            return "secret_shaped"
        checkpoint_like_prefixes = (
            "current goal",
            "current blocker",
            "next step",
            "current phase",
            "key files",
            "freshness",
            "当前目标",
            "当前卡点",
            "下一步",
            "当前阶段",
            "关键文件",
            "已完成",
            "已排除",
        )
        if any(lowered.startswith(prefix) for prefix in checkpoint_like_prefixes):
            return "transient_task_state"
        if re.search(r"(?i)\b(stdout|stderr|traceback|exit_code)\b", text) or len(text) > 220:
            return "noisy_output"
        return ""

    # 解析 extract durable promotions 相关输入，把原始内容转成结构化数据。
    def extract_durable_promotions(self, user_message, final_answer):
        user_text = str(user_message or "")
        if not (DURABLE_MEMORY_INTENT_PATTERN.search(user_text) or DURABLE_MEMORY_INTENT_ZH_PATTERN.search(user_text)):
            return [], []
        promotions = []
        rejections = []
        for line in str(final_answer or "").splitlines():
            text = line.strip()
            if not text or REDACTED_VALUE in text:
                continue
            for topic, pattern in DURABLE_MEMORY_LINE_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                note_text = match.group(1).strip()
                if note_text:
                    reason = self.reject_durable_reason(note_text)
                    if reason:
                        rejections.append(f"{topic}:{reason}")
                        break
                    promotions.append((topic, note_text))
                break
        return promotions, rejections

    # 维护 promote durable memory 相关记忆数据，帮助 agent 减少重复确认。
    def promote_durable_memory(self, user_message, final_answer):
        promotions, rejections = self.extract_durable_promotions(user_message, final_answer)
        promoted, superseded = self.memory.promote_durable(promotions)
        self.session["memory"] = self.memory.to_dict()
        self.last_durable_promotions = promoted
        self.last_durable_rejections = rejections
        self.last_durable_superseded = superseded
        return promoted, rejections, superseded

    # 执行一次完整 agent 请求，串起 prompt、模型、工具和运行工件。
    def ask(self, user_message):
        """执行一次完整的 agent 回合，直到产出最终答案或命中停止条件。

        为什么存在：
        `ask()` 是整个 runtime 的总调度器。它把“用户提一个请求”扩展成一条
        可持续推进的控制循环：记录会话、组 prompt、调用模型、执行工具、
        写 trace/report、更新状态，直到模型给出最终答案或系统主动停下。

        输入 / 输出：
        - 输入：`user_message`，即用户这一次的任务描述
        - 输出：字符串形式的最终回答；如果中途达到步数上限或重试上限，
          返回的是一条停止原因说明

        在 agent 链路里的位置：
        它是 CLI 和底层工具/模型之间的核心桥梁。CLI 收到用户输入后基本只做
        一件事：调用 `agent.ask()`。而 `ask()` 内部再去驱动 `ContextManager`
        组 prompt、`model_client.complete()` 调模型、`run_tool()` 执行动作。
        如果新人想理解 coda 是怎么“从一句话跑成一个 agent 流程”的，
        这里就是最关键的入口。
        """
        # monotonic 用来算耗时，比 datetime 更适合做运行时间统计，
        # 因为它不会被系统时间调整影响。
        run_started_at = time.monotonic()

        # 先把本轮用户请求写进工作记忆。
        # 这样后续 prompt 里的 Memory 会知道当前任务摘要是什么。
        self.memory.set_task_summary(user_message)

        # 把用户消息写入 session history。
        # 注意：history 是跨轮会话记录；下面的 TaskState 是本次 run 的状态。
        self.record({"role": "user", "content": user_message, "created_at": now()})

        # 每次 ask() 都创建一个新的 TaskState。
        # run_id 用于 .coda/runs/<run_id>/ 目录；task_id 是本轮任务编号。
        task_state = TaskState.create(run_id=self.new_run_id(), task_id=self.new_task_id(), user_request=user_message)
        task_state.resume_status = self.resume_state.get("status", CHECKPOINT_NONE_STATUS)
        self.current_task_state = task_state

        # start_run 会创建 run 目录，并立刻写第一版 task_state.json。
        # 这样即使后面模型调用失败，也能看到这轮运行已经开始过。
        self.current_run_dir = self.run_store.start_run(task_state)
        self.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )

        tool_steps = 0
        attempts = 0
        # attempts 是模型调用次数，tool_steps 是工具执行次数。
        # max_attempts 给 retry 留出空间：模型偶尔输出坏格式时，不会立刻耗尽工具步数。
        max_attempts = max(self.max_steps * 3, self.max_steps + 4)

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足
        while tool_steps < self.max_steps and attempts < max_attempts:
            attempts += 1
            task_state.record_attempt()
            self.run_store.write_task_state(task_state)

            # 每一轮都重新构建 prompt，因为 history/memory/workspace 都可能变化。
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = self._build_prompt_and_metadata(user_message)
            self.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                # partial-stale 表示旧 checkpoint 里记录的关键文件 hash 已经过期。
                # 这里先创建一个新 checkpoint，把“需要重新确认现场”这件事记录下来。
                checkpoint = self.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
                # workspace-mismatch 表示 runtime identity 变了，比如模型、审批策略、
                # 工作区 fingerprint 等不同。它不一定阻止继续，但必须写进 trace。
                self.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                    },
                )
                checkpoint = self.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            if prompt_metadata.get("budget_reductions"):
                # budget_reductions 表示 ContextManager 为了不超 prompt 预算压缩过上下文。
                # 建 checkpoint 是为了让后续 resume 时知道：这轮发生过上下文裁剪。
                checkpoint = self.create_checkpoint(task_state, user_message, trigger="context_reduction")
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            self.emit_trace(
                task_state,
                "model_requested",
                {
                    "attempts": task_state.attempts,
                    "tool_steps": task_state.tool_steps,
                    "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                },
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if getattr(self.model_client, "supports_prompt_cache", False):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()

            # 这里才是真正调用模型。
            # runtime 传进去的是完整 prompt，而不是只有 user_message。
            raw = self.model_client.complete(
                prompt,
                self.max_new_tokens,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
            completion_metadata = dict(getattr(self.model_client, "last_completion_metadata", {}) or {})
            if completion_metadata:
                # 把后端返回的 usage/cache 统计并回 prompt_metadata，
                # 方便统一写入 report 和 trace。
                prompt_metadata.update(completion_metadata)
            self.last_completion_metadata = completion_metadata
            self.last_prompt_metadata = prompt_metadata

            # 模型返回的是文本，parse() 把它归类成 tool/final/retry。
            # 从这一刻开始，普通文本进入 runtime 的结构化控制流。
            kind, payload = self.parse(raw)
            self.emit_trace(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
                    "completion_metadata": completion_metadata,
                    "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                },
            )

            if kind == "tool":
                # 模型请求工具时，主循环不会结束。
                # 执行工具后把结果写回 history，然后 continue 进入下一轮模型调用。
                tool_steps += 1
                name = payload.get("name", "")
                args = payload.get("args", {})
                task_state.record_tool(name)
                tool_started_at = time.monotonic()

                # run_tool 是统一护栏：校验、审批、执行、记录副作用都在里面。
                result = self.run_tool(name, args)
                self.record(
                    {
                        "role": "tool",
                        "name": name,
                        "args": args,
                        "content": result,
                        "created_at": now(),
                    }
                )
                # 工具执行后立刻更新 task_state 和 trace。
                # 这样如果下一轮模型调用失败，已经执行过的工具仍然有记录。
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "tool_executed",
                    {
                        "name": name,
                        "args": args,
                        "result": clip(result, 500),
                        "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                        **dict(self._last_tool_result_metadata or {}),
                    },
                )
                # 每次工具执行后都打 checkpoint。
                # 这让“跑到某个工具之后中断再继续”更容易恢复。
                checkpoint = self.create_checkpoint(task_state, user_message, trigger="tool_executed")
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "tool_executed",
                    },
                )
                continue

            if kind == "retry":
                # retry 表示模型输出格式不合格，比如空响应、坏 JSON、缺工具名。
                # 这里把 runtime 的提醒写回 history，让下一轮模型看到错误并修正。
                self.record({"role": "assistant", "content": payload, "created_at": now()})
                self.run_store.write_task_state(task_state)
                continue

            # 走到这里就是 final。
            # payload 是 parse() 提取出来的最终答案；如果没有 payload，就回退 raw。
            final = (payload or raw).strip()
            self.record({"role": "assistant", "content": final, "created_at": now()})
            task_state.finish_success(final)

            # 如果用户明确要求“记住/保存”，这里会从 final_answer 中提取稳定事实，
            # 写入 durable memory。
            self.promote_durable_memory(user_message, final)
            checkpoint = self.create_checkpoint(task_state, user_message, trigger="run_finished")
            self.run_store.write_task_state(task_state)
            self.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "run_finished",
                },
            )
            self.emit_trace(
                task_state,
                "run_finished",
                {
                    "status": task_state.status,
                    "stop_reason": task_state.stop_reason,
                    "final_answer": final,
                    "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                },
            )
            # report 是最终汇总。写完 report 后，ask() 正常返回 final。
            self.run_store.write_report(task_state, self.redact_artifact(self.build_report(task_state)))
            return final

        # 能走到这里，说明 while 没有通过 final 正常返回。
        # 只有两种主要情况：
        # 1. retry 太多，模型一直没给合法 tool/final；
        # 2. 工具步数达到上限，仍没有 final。
        if attempts >= max_attempts and tool_steps < self.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        self.record({"role": "assistant", "content": final, "created_at": now()})
        self.promote_durable_memory(user_message, final)
        self.run_store.write_task_state(task_state)
        checkpoint = self.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
        self.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": task_state.stop_reason or "run_stopped",
            },
        )
        self.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        self.run_store.write_report(task_state, self.redact_artifact(self.build_report(task_state)))
        return final

    # 统一校验并执行工具调用，同时处理审批、记忆和副作用记录。
    def run_tool(self, name, args):
        """执行一次工具调用，并在执行前后套上完整护栏。

        为什么存在：
        在 agent 系统里，真正危险的不是“模型会不会想调用工具”，而是
        “平台有没有在执行前把边界守住”。这个函数就是工具层的总闸口：
        所有工具调用都必须先经过它，不能让模型直接碰到底层函数。

        输入 / 输出：
        - 输入：工具名 `name`，参数字典 `args`
        - 输出：字符串结果。无论是成功结果还是错误信息，都会统一返回文本，
          这样模型下一轮都能继续消费这份反馈。

        在 agent 链路里的位置：
        它位于 `ask()` 的“模型决定要调用工具”之后，是控制循环里真正把模型
        意图落到外部世界的一步。因此这里串起了几乎所有安全与可控设计：
        工具是否存在、参数是否合法、是否重复、是否需要审批、执行结果是否裁剪、
        是否需要回写记忆。
        """
        # 工具执行不是“直接调函数”，而是一条带护栏的流水线：
        # 工具是否存在 -> 参数是否合法 -> 是否重复调用 -> 是否通过审批
        # -> 真正执行 -> 更新记忆。
        tool = self.tools.get(name)
        if tool is None:
            # 模型可能幻觉出一个不存在的工具名。
            # 这里不抛异常，而是返回可读错误，让模型下一轮有机会纠正。
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "unknown_tool",
                "security_event_type": "",
                "risk_level": "high",
                "read_only": False,
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return f"error: unknown tool '{name}'"
        try:
            # 参数校验发生在审批之前。
            # 如果参数本来就不合法，就没有必要打扰用户确认。
            self.validate_tool(name, args)
        except Exception as exc:
            example = self.tool_example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "invalid_arguments",
                "security_event_type": security_event_type,
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return message
        if self.repeated_tool_call(name, args):
            # 防止最简单的坏循环：
            # 模型连续几轮在没有新信息的情况下请求完全相同的工具调用。
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "repeated_identical_call",
                "security_event_type": "",
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return f"error: repeated identical tool call for {name}; choose a different tool or return a final answer"
        if tool["risky"] and not self.approve(name, args):
            # risky 工具包括 run_shell/write_file/patch_file。
            # 它们可能改变外部世界，所以要受 approval_policy 约束。
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "approval_denied",
                "security_event_type": "read_only_block" if self.read_only else "approval_denied",
                "risk_level": "high",
                "read_only": False,
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return f"error: approval denied for {name}"

        # 高风险工具执行前后会各做一次工作区快照。
        # 执行完后对比 hash，就能知道这次工具影响了哪些文件。
        before_snapshot = self.capture_workspace_snapshot() if tool["risky"] else {}
        after_snapshot = before_snapshot
        try:
            result = clip(tool["run"](args))
            after_snapshot = self.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = self.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            tool_status = "ok"
            tool_error_code = ""
            if name == "run_shell":
                # run_shell 的返回文本里包含 exit_code。
                # exit_code 非 0 表示命令失败；如果同时文件又变了，
                # 就标成 partial_success，提醒后续要检查 diff。
                match = re.search(r"exit_code:\s*(-?\d+)", result)
                exit_code = int(match.group(1)) if match else 0
                if exit_code != 0 and workspace_changed:
                    tool_status = "partial_success"
                    tool_error_code = "tool_partial_success"
                elif exit_code != 0:
                    tool_status = "error"
                    tool_error_code = "tool_failed"

            # 工具执行成功后，抽取少量信息进工作记忆。
            # 完整结果仍然保存在 session history 和 trace 里。
            self.update_memory_after_tool(name, args, result)
            self._last_tool_result_metadata = {
                "tool_status": tool_status,
                "tool_error_code": tool_error_code,
                "security_event_type": "",
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": affected_paths,
                "workspace_changed": workspace_changed,
                "workspace_fingerprint": self.workspace.fingerprint(),
                "diff_summary": diff_summary,
            }
            self.record_process_note_for_tool(name, self._last_tool_result_metadata)
            return result
        except Exception as exc:
            # 即使工具抛异常，也要检查它是否已经改过文件。
            # 这就是为什么异常分支也会 diff before/after snapshot。
            after_snapshot = self.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = self.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            self._last_tool_result_metadata = {
                "tool_status": "partial_success" if workspace_changed else "error",
                "tool_error_code": "tool_partial_success" if workspace_changed else "tool_failed",
                "security_event_type": security_event_type,
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": affected_paths,
                "workspace_changed": workspace_changed,
                "workspace_fingerprint": self.workspace.fingerprint(),
                "diff_summary": diff_summary,
            }
            self.record_process_note_for_tool(name, self._last_tool_result_metadata)
            return f"error: tool {name} failed: {exc}"

    # 检查 repeated tool call 相关条件是否成立，用于提前拦截异常情况。
    def repeated_tool_call(self, name, args):
        # agent 很常见的一种坏循环，是在没有新信息的情况下反复发起同一调用。
        # 这里提前挡掉最简单的这种循环。
        tool_events = [item for item in self.session["history"] if item["role"] == "tool"]
        if len(tool_events) < 2:
            return False
        recent = tool_events[-2:]
        return all(item["name"] == name and item["args"] == args for item in recent)

    # 构建 new task id 需要的数据或对象，供后续流程继续使用。
    @staticmethod
    def new_task_id():
        return "task_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    # 构建 new run id 需要的数据或对象，供后续流程继续使用。
    @staticmethod
    def new_run_id():
        return "run_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    # 构建 build report 需要的数据或对象，供后续流程继续使用。
    def build_report(self, task_state):
        # report 是一次运行的最终摘要；
        # 和 trace 的区别在于，trace 关注过程，report 关注结果与关键指标。
        return {
            "run_id": task_state.run_id,
            "task_id": task_state.task_id,
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "final_answer": task_state.final_answer,
            "tool_steps": task_state.tool_steps,
            "attempts": task_state.attempts,
            "checkpoint_id": task_state.checkpoint_id,
            "resume_status": task_state.resume_status,
            "task_state": task_state.to_dict(),
            "prompt_metadata": self.last_prompt_metadata,
            "durable_promotions": list(self.last_durable_promotions),
            "durable_rejections": list(self.last_durable_rejections),
            "durable_superseded": list(self.last_durable_superseded),
            "redacted_env": self.detected_secret_env_summary(),
        }

    # 执行 tool example 工具逻辑，并把受控结果返回给 runtime。
    def tool_example(self, name):
        return toolkit.tool_example(name)

    # 校验工具参数是否合法，在真正执行前拦住错误调用。
    def validate_tool(self, name, args):
        """把通用工具校验和 runtime 级额外约束串起来。"""
        toolkit.validate_tool(self, name, args)
        if name == "delegate":
            # delegate 是只读子 agent 调查工具。
            # 这里限制深度，防止 agent 递归创建子 agent。
            if self.depth >= self.max_depth:
                raise ValueError("delegate depth exceeded")

    # 执行 tool list files 工具逻辑，并把受控结果返回给 runtime。
    def tool_list_files(self, args):
        return toolkit.tool_list_files(self, args)

    # 执行 tool read file 工具逻辑，并把受控结果返回给 runtime。
    def tool_read_file(self, args):
        return toolkit.tool_read_file(self, args)

    # 执行 tool search 工具逻辑，并把受控结果返回给 runtime。
    def tool_search(self, args):
        return toolkit.tool_search(self, args)

    # 执行 tool run shell 工具逻辑，并把受控结果返回给 runtime。
    def tool_run_shell(self, args):
        return toolkit.tool_run_shell(self, args)

    # 执行 tool write file 工具逻辑，并把受控结果返回给 runtime。
    def tool_write_file(self, args):
        return toolkit.tool_write_file(self, args)

    # 执行 tool patch file 工具逻辑，并把受控结果返回给 runtime。
    def tool_patch_file(self, args):
        return toolkit.tool_patch_file(self, args)

    # 执行 tool delegate 工具逻辑，并把受控结果返回给 runtime。
    def tool_delegate(self, args):
        return toolkit.tool_delegate(self, args)

    # 处理 approve 相关逻辑，支撑当前模块的主要流程。
    def approve(self, name, args):
        """根据审批策略决定是否允许高风险工具执行。"""
        if self.read_only:
            # 只读 agent 永远不能执行 risky 工具。
            return False
        if self.approval_policy == "auto":
            return True
        if self.approval_policy == "never":
            return False
        try:
            answer = input(f"approve {name} {json.dumps(args, ensure_ascii=True)}? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    # 解析模型输出，判断是工具调用、最终答案还是需要重试。
    @staticmethod
    def parse(raw):
        """把模型原始输出解析成 runtime 可执行的动作或最终答案。

        为什么存在：
        模型输出首先是自然语言文本，而 runtime 需要的是结构化决策：
        “这是工具调用”还是“这是最终答案”。如果没有这层解析，后面的工具校验、
        审批和执行链路就没法可靠工作。

        输入 / 输出：
        - 输入：模型返回的原始文本 `raw`
        - 输出：`(kind, payload)`，其中 `kind` 可能是 `tool`、`final`、`retry`

        在 agent 链路里的位置：
        它位于 `model_client.complete()` 之后、`run_tool()` 之前，是模型输出
        进入平台控制流的第一道结构化关口。
        """
        raw = str(raw)
        # 这里支持两种工具格式：
        # 1. <tool>...</tool> 里包 JSON，适合简短调用
        # 2. XML 风格属性/子标签，适合写文件这类多行内容
        if "<tool>" in raw and ("<final>" not in raw or raw.find("<tool>") < raw.find("<final>")):
            # JSON 工具格式示例：
            # <tool>{"name":"read_file","args":{"path":"README.md"}}</tool>
            body = Coda.extract(raw, "tool")
            try:
                payload = json.loads(body)
            except Exception:
                # 格式坏了不直接失败，而是给模型一个 retry notice。
                return "retry", Coda.retry_notice("model returned malformed tool JSON")
            if not isinstance(payload, dict):
                return "retry", Coda.retry_notice("tool payload must be a JSON object")
            if not str(payload.get("name", "")).strip():
                return "retry", Coda.retry_notice("tool payload is missing a tool name")
            args = payload.get("args", {})
            if args is None:
                payload["args"] = {}
            elif not isinstance(args, dict):
                return "retry", Coda.retry_notice()
            return "tool", payload
        if "<tool" in raw and ("<final>" not in raw or raw.find("<tool") < raw.find("<final>")):
            # XML 工具格式示例：
            # <tool name="write_file" path="a.py"><content>...</content></tool>
            payload = Coda.parse_xml_tool(raw)
            if payload is not None:
                return "tool", payload
            return "retry", Coda.retry_notice()
        if "<final>" in raw:
            # final 是正常停止信号。
            final = Coda.extract(raw, "final").strip()
            if final:
                return "final", final
            return "retry", Coda.retry_notice("model returned an empty <final> answer")
        raw = raw.strip()
        if raw:
            # 兼容宽松输出：如果模型没包 <final> 但返回了非空文本，
            # runtime 仍把它当最终答案。
            return "final", raw
        return "retry", Coda.retry_notice("model returned an empty response")

    # 处理 retry notice 相关逻辑，支撑当前模块的主要流程。
    @staticmethod
    def retry_notice(problem=None):
        # retry_notice 会被写进 history。
        # 下一轮模型能看到这条提醒，从而修正输出格式。
        prefix = "Runtime notice"
        if problem:
            prefix += f": {problem}"
        else:
            prefix += ": model returned malformed tool output"
        return (
            f"{prefix}. Reply with a valid <tool> call or a non-empty <final> answer. "
            'For multi-line files, prefer <tool name="write_file" path="file.py"><content>...</content></tool>.'
        )

    # 解析 parse xml tool 相关输入，把原始内容转成结构化数据。
    @staticmethod
    def parse_xml_tool(raw):
        """解析 XML 风格工具调用。

        这个格式主要服务多行文本场景，比如 write_file/patch_file。
        JSON 里写多行字符串很容易转义出错，XML 子标签更直观。
        """
        match = re.search(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", raw, re.S)
        if not match:
            return None
        attrs = Coda.parse_attrs(match.group("attrs"))
        name = str(attrs.pop("name", "")).strip()
        if not name:
            return None

        body = match.group("body")
        args = dict(attrs)
        # 这些字段既可以写成属性，也可以写成子标签。
        # 子标签适合 content/old_text/new_text 这种多行内容。
        for key in ("content", "old_text", "new_text", "command", "task", "pattern", "path"):
            if f"<{key}>" in body:
                args[key] = Coda.extract_raw(body, key)

        body_text = body.strip("\n")
        if name == "write_file" and "content" not in args and body_text:
            args["content"] = body_text
        if name == "delegate" and "task" not in args and body_text:
            args["task"] = body_text.strip()
        return {"name": name, "args": args}

    # 解析 parse attrs 相关输入，把原始内容转成结构化数据。
    @staticmethod
    def parse_attrs(text):
        # 解析 <tool name="..." path="..."> 里的属性。
        attrs = {}
        for match in re.finditer(r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", text):
            attrs[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
        return attrs

    # 处理 extract 相关逻辑，支撑当前模块的主要流程。
    @staticmethod
    def extract(text, tag):
        # 提取 <tag>...</tag> 内容，并 strip 首尾空白。
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:].strip()
        return text[start:end].strip()

    # 解析 extract raw 相关输入，把原始内容转成结构化数据。
    @staticmethod
    def extract_raw(text, tag):
        # 提取原始内容，不 strip。
        # 写文件时保留换行和缩进很重要，所以 content/old_text/new_text 会用这个。
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:]
        return text[start:end]

    # 处理 reset 相关逻辑，支撑当前模块的主要流程。
    def reset(self):
        """清空当前 session 的短期状态。

        注意：这里只清空当前 session 的 history/memory，
        不会删除 .coda/runs 下已经生成的 trace/report。
        """
        self.session["history"] = []
        self.session["memory"].clear()
        self.session["memory"].update(memorylib.default_memory_state())
        self.memory = memorylib.LayeredMemory(self.session["memory"], workspace_root=self.root)
        self.session_store.save(self.session)

    # 处理 path 相关路径，确保文件访问不越过工作区边界。
    def path(self, raw_path):
        """把工具传入的路径解析到工作区内。

        所有文件类工具最终都会走这里。
        它的核心安全规则是：解析后的绝对路径必须仍在 self.root 下面。
        """
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        # 所有文件类工具都被锚定在 workspace root 之下。
        # 这样既能防住 "../" 逃逸，也能防住符号链接解析后跳出仓库。
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved


MiniAgent = Coda
