"""命令行入口。

这个模块负责把“用户怎么启动 coda”翻译成 runtime 能理解的对象：
解析参数、挑模型后端、构建工作区快照、恢复或新建 session，
最后进入 one-shot 或交互式循环。
"""

import argparse
import os
import shutil
import sys
import textwrap
import unicodedata

from .config import load_project_env, provider_env
from .models import AnthropicCompatibleModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .runtime import Coda, SessionStore
from .workspace import WorkspaceContext, middle

# 这些环境变量名会被当作“可能包含密钥”的字段。
# Coda 在写 trace/report 等运行工件时，会用这份名单辅助脱敏，
# 避免把 API key、token、PAT 之类的敏感值原样落盘。
DEFAULT_SECRET_ENV_NAMES = (
    "CODA_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "CODA_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CODA_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "CODA_RIGHT_CODES_API_KEY",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)

# 欢迎页只是一层 CLI 体验，不参与 agent 推理。
# 这些常量放在这里，是为了让启动后用户能马上看到：
# 当前模型、工作区、审批模式和 session id。
WELCOME_ART = (
    "        /\\___/\\",
    "     __/ ^   ^ \\__",
    "    /  \\   v   /  \\",
    "   /|   | >_  |   |\\",
    "    |___|_____|___|",
)
WELCOME_NAME = "coda"
WELCOME_SUBTITLE = "local coding agent"
WELCOME_STATUS = "calm shell, ready for work"
HELP_DETAILS = textwrap.dedent(
    """\
    Commands:
    /help    Show this help message.
    /memory  Show the agent's distilled working memory.
    /session Show the path to the saved session file.
    /reset   Clear the current session history and memory.
    /exit    Exit the agent.
    """
).strip()


DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENAI_BASE_URL = "https://www.right.codes/codex/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_ANTHROPIC_BASE_URL = "https://www.right.codes/claude/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
# 旧变量名保留是为了兼容早期版本配置；新代码优先读 CODA_SECRET_ENV_NAMES。
LEGACY_SECRET_ENV_NAMES_VAR = "MINI_CODING_AGENT_SECRET_ENV_NAMES"
SECRET_ENV_NAMES_VAR = "CODA_SECRET_ENV_NAMES"


# 处理 effective model 相关逻辑，支撑当前模块的主要流程。
def _effective_model(args, provider):
    # 模型选择优先级：
    # 1. 用户显式传入 --model
    # 2. provider 对应的环境变量
    # 3. 代码里的默认值
    explicit_model = getattr(args, "model", None)
    if explicit_model:
        return explicit_model

    # 下面每个 provider 都有自己的环境变量名。
    # 这样用户可以在 .env 里固定默认模型，平时启动时就不用反复传 --model。
    if provider == "openai":
        model = provider_env("CODA_OPENAI_MODEL", ("OPENAI_MODEL",))
        if model:
            return model
        return DEFAULT_OPENAI_MODEL
    if provider == "anthropic":
        model = provider_env("CODA_ANTHROPIC_MODEL", ("ANTHROPIC_MODEL",))
        if model:
            return model
        return DEFAULT_ANTHROPIC_MODEL
    if provider == "deepseek":
        model = provider_env("CODA_DEEPSEEK_MODEL", ("DEEPSEEK_MODEL",))
        if model:
            return model
        return DEFAULT_DEEPSEEK_MODEL
    return DEFAULT_OLLAMA_MODEL


# 处理 configured secret names 相关逻辑，支撑当前模块的主要流程。
def _configured_secret_names(args):
    """合并所有“需要脱敏”的环境变量名。

    初学者可以把这里理解成三层来源：
    1. 代码内置的一批常见密钥名；
    2. 用户启动时通过 --secret-env-name 额外传入的名字；
    3. 环境变量 CODA_SECRET_ENV_NAMES 里用逗号配置的一批名字。

    最后统一转成大写，是为了减少大小写差异带来的漏脱敏。
    """
    configured_secret_names = set(DEFAULT_SECRET_ENV_NAMES)
    configured_secret_names.update(str(name).upper() for name in args.secret_env_names)

    # 支持在环境变量里配置额外 secret 名称：
    # CODA_SECRET_ENV_NAMES="FOO_TOKEN,BAR_PASSWORD"
    extra_names = os.environ.get(SECRET_ENV_NAMES_VAR, "")
    if not extra_names.strip():
        extra_names = os.environ.get(LEGACY_SECRET_ENV_NAMES_VAR, "")
    if extra_names.strip():
        configured_secret_names.update(
            item.strip().upper()
            for item in extra_names.split(",")
            if item.strip()
        )
    return sorted(configured_secret_names)


# 处理 build model client 相关逻辑，支撑当前模块的主要流程。
def _build_model_client(args):
    """根据 --provider 创建具体的模型客户端。

    runtime 只需要一个统一的接口：client.complete(prompt, max_new_tokens)。
    至于底层是 Ollama、本地服务、OpenAI-compatible 还是 Anthropic-compatible，
    都在这里被翻译成对应的 client 对象。
    """
    provider = getattr(args, "provider", "openai")
    # CLI 只负责把 provider 选择翻译成具体 client。
    # 真正的提示词格式、缓存支持、HTTP 协议差异，都封装在 models.py 里。
    if provider == "openai":
        model = _effective_model(args, provider)
        # base_url 和 api_key 都优先从 provider 专属的 CODA_* 变量里读。
        # 如果用户启动时传了 --base-url，则显式参数优先级最高。
        base_url = getattr(args, "base_url", None) or provider_env("CODA_OPENAI_API_BASE", ("OPENAI_API_BASE",), DEFAULT_OPENAI_BASE_URL)
        api_key = provider_env("CODA_OPENAI_API_KEY", ("OPENAI_API_KEY",))
        return OpenAICompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
        )
    if provider == "anthropic":
        model = _effective_model(args, provider)
        base_url = getattr(args, "base_url", None) or provider_env("CODA_ANTHROPIC_API_BASE", ("ANTHROPIC_API_BASE",), DEFAULT_ANTHROPIC_BASE_URL)
        # Anthropic-compatible 这里允许从多个 key 名称回退。
        # 这是为了适配一些代理服务：同一套 key 可能同时服务 OpenAI/Anthropic 兼容接口。
        api_key = provider_env(
            "CODA_ANTHROPIC_API_KEY",
            ("ANTHROPIC_API_KEY", "CODA_RIGHT_CODES_API_KEY", "RIGHT_CODES_API_KEY", "CODA_OPENAI_API_KEY", "OPENAI_API_KEY"),
        )
        return AnthropicCompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
        )
    if provider == "deepseek":
        model = _effective_model(args, provider)
        # DeepSeek 当前走 Anthropic-compatible Messages API，
        # 所以这里复用 AnthropicCompatibleModelClient，而不是单独写一个 client。
        base_url = getattr(args, "base_url", None) or provider_env("CODA_DEEPSEEK_API_BASE", ("DEEPSEEK_API_BASE",), DEFAULT_DEEPSEEK_BASE_URL)
        api_key = provider_env("CODA_DEEPSEEK_API_KEY", ("DEEPSEEK_API_KEY",))
        return AnthropicCompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(args, "openai_timeout", getattr(args, "ollama_timeout", 300)),
        )

    model = _effective_model(args, provider)
    host = getattr(args, "host", DEFAULT_OLLAMA_HOST)
    # 走到这里说明 provider 是 ollama。
    # Ollama 是本地 HTTP 服务，所以它需要 host、top_p 这类本地生成参数。
    return OllamaModelClient(
        model=model,
        host=host,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout=args.ollama_timeout,
    )


# 生成 CLI 启动欢迎页，展示工作区、模型和 session 状态。
def build_welcome(agent, model, host):
    """生成启动时显示的欢迎框。

    这段逻辑不影响 agent 能力，只负责把关键信息排版得更容易看：
    工作区、模型、分支、审批策略、session id。
    """
    # 终端宽度可能很窄也可能很宽；这里把欢迎框限制在 68 到 84 列之间。
    # 太窄会挤坏字段，太宽又会显得松散。
    width = max(68, min(shutil.get_terminal_size((80, 20)).columns, 84))
    inner = width - 4
    gap = 3
    left_width = (inner - gap) // 2
    right_width = inner - gap - left_width

    # 处理 display width 相关逻辑，支撑当前模块的主要流程。
    def display_width(text):
        width_count = 0
        for char in str(text):
            if unicodedata.combining(char):
                continue
            width_count += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        return width_count

    # 处理 fit display 相关逻辑，支撑当前模块的主要流程。
    def fit_display(text, size):
        text = str(text).replace("\n", " ")
        if display_width(text) <= size:
            return text
        if size <= 3:
            result = ""
            used = 0
            for char in text:
                char_width = display_width(char)
                if used + char_width > size:
                    break
                result += char
                used += char_width
            return result
        left_limit = (size - 3) // 2
        right_limit = size - 3 - left_limit

        left_text = ""
        used = 0
        for char in text:
            char_width = display_width(char)
            if used + char_width > left_limit:
                break
            left_text += char
            used += char_width

        right_chars = []
        used = 0
        for char in reversed(text):
            char_width = display_width(char)
            if used + char_width > right_limit:
                break
            right_chars.append(char)
            used += char_width
        return left_text + "..." + "".join(reversed(right_chars))

    # 处理 pad right 相关逻辑，支撑当前模块的主要流程。
    def pad_right(text, size):
        text = fit_display(text, size)
        return text + " " * max(0, size - display_width(text))

    # 处理 pad center 相关逻辑，支撑当前模块的主要流程。
    def pad_center(text, size):
        text = fit_display(text, size)
        padding = max(0, size - display_width(text))
        left = padding // 2
        right = padding - left
        return " " * left + text + " " * right

    # 处理 row 相关逻辑，支撑当前模块的主要流程。
    def row(text):
        # middle() 会在文本过长时保留头尾并用 ... 省略中间，
        # 适合展示很长的路径。
        body = pad_right(text, width - 4)
        return f"| {body} |"

    # 处理 divider 相关逻辑，支撑当前模块的主要流程。
    def divider(char="-"):
        return "+" + char * (width - 2) + "+"

    # 处理 center 相关逻辑，支撑当前模块的主要流程。
    def center(text):
        body = pad_center(text, inner)
        return f"| {body} |"

    # 处理 center block 相关逻辑，支撑当前模块的主要流程。
    def center_block(lines):
        block_width = max(display_width(line) for line in lines)
        return [center(pad_right(line, block_width)) for line in lines]

    # 处理 cell 相关逻辑，支撑当前模块的主要流程。
    def cell(label, value, size):
        return pad_right(f"{label:<9} {value}", size)

    # 处理 pair 相关逻辑，支撑当前模块的主要流程。
    def pair(left_label, left_value, right_label, right_value):
        # 一行放两组 label/value，比如 MODEL 和 BRANCH。
        # 左右宽度提前算好，避免长路径或长模型名撑坏边框。
        left = cell(left_label, left_value, left_width)
        right = cell(right_label, right_value, right_width)
        return f"| {left}{' ' * gap}{right} |"

    line = divider("=")
    rows = center_block(WELCOME_ART)
    rows.extend(
        [
            center(WELCOME_NAME),
            center(WELCOME_SUBTITLE),
            center(WELCOME_STATUS),
            divider("-"),
            row(""),
            row("WORKSPACE  " + middle(agent.workspace.cwd, inner - 11)),
            pair("MODEL", model, "BRANCH", agent.workspace.branch),
            pair("APPROVAL", agent.approval_policy, "SESSION", agent.session["id"]),
            row(""),
        ]
    )
    return "\n".join([line, *rows, line])


# 根据 CLI 参数组装 Coda agent，完成模型、工作区和 session 初始化。
def build_agent(args):
    """根据 CLI 参数装配出一个可运行的 Coda 实例。

    为什么存在：
    命令行参数只是字符串和开关，runtime 需要的是已经装配好的对象图：
    model client、workspace snapshot、session store、secret 配置等。
    这个函数负责把“启动参数”翻译成“agent 运行现场”。

    输入 / 输出：
    - 输入：`argparse` 解析后的 `args`
    - 输出：一个新的 `Coda`，或一个从旧 session 恢复出来的 `Coda`

    在 agent 链路里的位置：
    它是整个程序启动链路里最靠近 runtime 的装配点。`main()` 先调它，
    得到 agent 后，后面无论是 one-shot 还是 REPL 模式，都会落到 `ask()`。
    """
    # 这里是 CLI 到 runtime 的装配点：
    # 先采集工作区快照和加载项目级环境，再整理 secret 名单、模型后端和 session。
    workspace = WorkspaceContext.build(args.cwd)

    # .env 是按项目读取的，而不是按当前 shell 全局读取。
    # 这样每个仓库可以有自己的 CODA_* 配置，例如模型、base_url、api_key。
    load_project_env(workspace.repo_root)
    configured_secret_names = _configured_secret_names(args)

    # session 保存的是“跨轮可恢复的会话状态”，比如 history 和 memory。
    # 它和单次运行的 trace/report 不同；trace/report 在 runtime 的 RunStore 里处理。
    store = SessionStore(workspace.repo_root + "/.coda/sessions")
    model = _build_model_client(args)

    # --resume latest 是一个方便入口：
    # 用户不用记 session id，也可以继续最近一次会话。
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        # 恢复旧 session 时，会复用磁盘上的 history/memory/checkpoint，
        # 但模型 client、工作区快照、审批策略等运行参数仍来自本次启动。
        return Coda.from_session(
            model_client=model,
            workspace=workspace,
            session_store=store,
            session_id=session_id,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
            secret_env_names=configured_secret_names,
        )

    # 没有 --resume 时，就创建一个全新的 Coda 实例和新 session。
    return Coda(
        model_client=model,
        workspace=workspace,
        session_store=store,
        approval_policy=args.approval,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        secret_env_names=configured_secret_names,
    )


# 构建 build arg parser 需要的数据或对象，供后续流程继续使用。
def build_arg_parser():
    """声明 CLI 支持的所有参数。

    argparse 负责把命令行字符串解析成 args 对象。
    例如：
      coda --provider deepseek --cwd E:\repo "修一下测试"
    会被解析成 provider、cwd 和 prompt 等字段。
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Minimal coding agent for Ollama, OpenAI-compatible, Anthropic-compatible, or DeepSeek models.",
    )

    # 位置参数 prompt 使用 nargs="*"，表示可以接收 0 个或多个词。
    # 如果用户传了 prompt，程序进入 one-shot；如果没传，就进入交互式 REPL。
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt.")

    # --cwd 决定 Coda 进入哪个工作区。
    # 后续所有文件工具都会被限制在这个工作区的 repo_root 下。
    parser.add_argument("--cwd", default=".", help="Workspace directory.")

    # provider 决定底层模型服务类型，但 runtime 上层不直接关心 provider 差异。
    parser.add_argument("--provider", choices=("ollama", "openai", "anthropic", "deepseek"), default="openai", help="Model backend to use.")
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override. Defaults to qwen3.5:4b for Ollama, CODA_OPENAI_MODEL for openai, CODA_ANTHROPIC_MODEL for anthropic, and CODA_DEEPSEEK_MODEL for deepseek when set.",
    )
    parser.add_argument("--host", default=DEFAULT_OLLAMA_HOST, help="Ollama server URL.")
    parser.add_argument("--base-url", default=None, help="Provider API base URL for openai, anthropic, or deepseek.")
    parser.add_argument("--ollama-timeout", type=int, default=300, help="Ollama request timeout in seconds.")
    parser.add_argument("--openai-timeout", type=int, default=300, help="OpenAI-compatible request timeout in seconds.")
    parser.add_argument("--resume", default=None, help="Session id to resume or 'latest'.")

    # approval 控制高风险工具是否允许执行。
    # ask: 每次写文件/跑 shell 前询问用户
    # auto: 自动允许
    # never: 永不允许，适合只读或测试安全边界
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default="ask", help="Approval policy for risky tools.")
    parser.add_argument(
        "--secret-env-name",
        dest="secret_env_names",
        action="append",
        default=[],
        help="Extra environment variable names to treat as secrets for trace/report redaction.",
    )
    parser.add_argument("--max-steps", type=int, default=6, help="Maximum tool/model iterations per request.")

    # max_new_tokens 限制的是模型每一轮最多输出多少 token，
    # 不是整个会话的总 token。
    parser.add_argument("--max-new-tokens", type=int, default=512, help="Maximum model output tokens per step.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature sent to Ollama.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling value sent to Ollama.")
    return parser


# 处理 main 相关逻辑，支撑当前模块的主要流程。
def main(argv=None):
    """程序主入口。

    这个函数做三件事：
    1. 解析 CLI 参数；
    2. 构建 Coda agent；
    3. 根据是否传入 prompt，选择 one-shot 或 REPL 模式。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_arg_parser().parse_args(argv)
    agent = build_agent(args)

    # 不同模型 client 暴露的字段略有不同：
    # Ollama 有 host，OpenAI/Anthropic-compatible 通常有 base_url。
    # getattr 的嵌套写法是在做“有哪个字段就用哪个字段”的兼容读取。
    model = getattr(agent.model_client, "model", getattr(args, "model", DEFAULT_OLLAMA_MODEL))
    host = getattr(agent.model_client, "host", getattr(agent.model_client, "base_url", getattr(args, "host", DEFAULT_OLLAMA_HOST)))
    print(build_welcome(agent, model=model, host=host))

    if args.prompt:
        # one-shot 模式：只跑一次 ask，不进入 REPL 循环。
        # 因为 argparse 会把多个词拆成列表，所以这里再拼回一整句。
        prompt = " ".join(args.prompt).strip()
        if prompt:
            print()
            try:
                # ask() 是 runtime 的核心入口：
                # 它会构建 prompt、调用模型、解析工具请求、执行工具，并返回最终回答。
                print(agent.ask(prompt))
            except RuntimeError as exc:
                # 模型服务不可达、HTTP 错误等运行期问题会走到这里。
                # 返回 1 表示命令执行失败，方便脚本/CI 判断。
                print(str(exc), file=sys.stderr)
                return 1
        return 0

    while True:
        # 交互模式：每次读取一条用户输入，交给同一个 agent，
        # 因此 session history 和 working memory 会跨轮延续。
        try:
            user_input = input("\ncoda> ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl-D / Ctrl-C 都视为正常退出。
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print(HELP_DETAILS)
            continue
        if user_input == "/memory":
            # memory_text() 展示的是 runtime 提炼后的工作记忆，
            # 不是完整聊天历史。
            print(agent.memory_text())
            continue
        if user_input == "/session":
            # session 文件可以用来观察 history/memory/checkpoint 如何落盘。
            print(agent.session_path)
            continue
        if user_input == "/reset":
            # reset 只清空当前 session 的 history/memory，
            # 不会删除整个 .coda 目录，也不会清理历史 run artifacts。
            agent.reset()
            print("session reset")
            continue

        print()
        try:
            print(agent.ask(user_input))
        except RuntimeError as exc:
            # REPL 里单次请求失败不直接退出，方便用户改配置后继续尝试。
            print(str(exc), file=sys.stderr)
