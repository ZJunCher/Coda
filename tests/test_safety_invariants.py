import os
import shlex
import sys
from unittest.mock import patch

from coda import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from coda import cli as mini_cli
from coda.task_state import TaskState


# 鏋勫缓 build workspace 闇€瑕佺殑鏁版嵁鎴栧璞★紝渚涘悗缁祦绋嬬户缁娇鐢ㄣ€?
def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


# 鏍规嵁 CLI 鍙傛暟缁勮 Coda agent锛屽畬鎴愭ā鍨嬨€佸伐浣滃尯鍜?session 鍒濆鍖栥€?
def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".coda" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


# 楠岃瘉 workspace escape is rejected 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_workspace_escape_is_rejected(tmp_path):
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    result = agent.run_tool("read_file", {"path": "../outside.txt"})

    assert "path escapes workspace" in result


# 楠岃瘉 symlink path traversal is rejected 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_symlink_path_traversal_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)
    agent = build_agent(tmp_path, [])

    result = agent.run_tool("read_file", {"path": "linked.txt"})

    assert "path escapes workspace" in result


# 楠岃瘉 risky tool deny behavior 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_risky_tool_deny_behavior(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="never")

    result = agent.run_tool("run_shell", {"command": "echo hi", "timeout": 20})

    assert result == "error: approval denied for run_shell"


# 楠岃瘉 cli build agent wires secret env names from parser 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_cli_build_agent_wires_secret_env_names_from_parser(tmp_path):
    class DummyModelClient:
        # 鍒濆鍖栧綋鍓嶅璞★紝淇濆瓨鍚庣画鏂规硶闇€瑕佷娇鐢ㄧ殑鍩虹鐘舵€併€?
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        # 鎵ц complete 瀵瑰簲鐨勪富娴佺▼锛屽苟鎶婄粨鏋滆繑鍥炵粰璋冪敤鏂广€?
        def complete(self, prompt, max_new_tokens):
            raise AssertionError("model should not be invoked")

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp-1", "GH_PAT": "ghp-2"}, clear=True), patch(
        "coda.cli.OllamaModelClient",
        DummyModelClient,
    ):
        args = mini_cli.build_arg_parser().parse_args(
            [
                "--cwd",
                str(tmp_path),
                "--approval",
                "auto",
                "--secret-env-name",
                "GITHUB_PAT",
                "--secret-env-name",
                "GH_PAT",
            ]
        )
        agent = mini_cli.build_agent(args)
        assert set(agent.secret_env_summary()["secret_env_names"]) == {"GITHUB_PAT", "GH_PAT"}


# 楠岃瘉 cli build agent uses default configured secret names 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_cli_build_agent_uses_default_configured_secret_names(tmp_path):
    class DummyModelClient:
        # 鍒濆鍖栧綋鍓嶅璞★紝淇濆瓨鍚庣画鏂规硶闇€瑕佷娇鐢ㄧ殑鍩虹鐘舵€併€?
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        # 鎵ц complete 瀵瑰簲鐨勪富娴佺▼锛屽苟鎶婄粨鏋滆繑鍥炵粰璋冪敤鏂广€?
        def complete(self, prompt, max_new_tokens):
            raise AssertionError("model should not be invoked")

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    with patch.dict(os.environ, {"GH_PAT": "ghp-default-1"}, clear=True), patch(
        "coda.cli.OllamaModelClient",
        DummyModelClient,
    ):
        args = mini_cli.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--approval", "auto"])
        agent = mini_cli.build_agent(args)
        assert agent.secret_env_summary()["secret_env_names"] == ["GH_PAT"]


# 楠岃瘉 cli build agent loads project env secrets before redaction setup 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_cli_build_agent_loads_project_env_secrets_before_redaction_setup(tmp_path):
    class DummyModelClient:
        # 鍒濆鍖栧綋鍓嶅璞★紝淇濆瓨鍚庣画鏂规硶闇€瑕佷娇鐢ㄧ殑鍩虹鐘舵€併€?
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        # 鎵ц complete 瀵瑰簲鐨勪富娴佺▼锛屽苟鎶婄粨鏋滆繑鍥炵粰璋冪敤鏂广€?
        def complete(self, prompt, max_new_tokens):
            raise AssertionError("model should not be invoked")

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    (tmp_path / ".env").write_text("CODA_DEEPSEEK_API_KEY=dummy-project-secret\n", encoding="utf-8")
    with patch.dict(os.environ, {}, clear=True), patch("coda.cli.AnthropicCompatibleModelClient", DummyModelClient):
        args = mini_cli.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "deepseek"])
        agent = mini_cli.build_agent(args)
        assert agent.secret_env_summary()["secret_env_names"] == ["CODA_DEEPSEEK_API_KEY"]


# 楠岃瘉 cli build agent reads secret names from environment config 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_cli_build_agent_reads_secret_names_from_environment_config(tmp_path):
    class DummyModelClient:
        # 鍒濆鍖栧綋鍓嶅璞★紝淇濆瓨鍚庣画鏂规硶闇€瑕佷娇鐢ㄧ殑鍩虹鐘舵€併€?
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        # 鎵ц complete 瀵瑰簲鐨勪富娴佺▼锛屽苟鎶婄粨鏋滆繑鍥炵粰璋冪敤鏂广€?
        def complete(self, prompt, max_new_tokens):
            raise AssertionError("model should not be invoked")

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    with patch.dict(
        os.environ,
        {
            "MCA_CUSTOM_SECRET": "custom-secret-value",
            "MINI_CODING_AGENT_SECRET_ENV_NAMES": "MCA_CUSTOM_SECRET",
        },
        clear=True,
    ), patch("coda.cli.OllamaModelClient", DummyModelClient):
        args = mini_cli.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--approval", "auto"])
        agent = mini_cli.build_agent(args)
        assert agent.secret_env_summary()["secret_env_names"] == ["MCA_CUSTOM_SECRET"]


# 楠岃瘉 run shell uses allowlisted environment only 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_run_shell_uses_allowlisted_environment_only(tmp_path):
    secret = "shh-allowlist-secret"
    agent = build_agent(tmp_path, [], approval_policy="auto")
    script = 'import os; print(os.getenv("MCA_ALLOWLIST_SECRET", "missing"))'
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    with patch.dict(os.environ, {"MCA_ALLOWLIST_SECRET": secret}, clear=False):
        result = agent.run_tool("run_shell", {"command": command, "timeout": 20})

    assert secret not in result
    assert "missing" in result


# 楠岃瘉 bound tool methods delegate into tools module 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_bound_tool_methods_delegate_into_tools_module(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="auto")

    with patch("coda.tools.subprocess.run") as fake_run:
        fake_run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "toolkit-shell\n", "stderr": ""},
        )()
        shell_result = agent.tool_run_shell({"command": "echo bypass", "timeout": 20})

    assert "toolkit-shell" in shell_result
    fake_run.assert_called_once()
    assert agent.tool_run_shell.__func__.__module__ == "coda.runtime"

    with patch("coda.tools.tool_delegate", return_value="toolkit-delegate") as fake_delegate:
        delegate_result = agent.tool_delegate({"task": "inspect README.md", "max_steps": 2})

    assert delegate_result == "toolkit-delegate"
    fake_delegate.assert_called_once()


# 楠岃瘉 delegate depth limit is enforced 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_delegate_depth_limit_is_enforced(tmp_path):
    agent = build_agent(tmp_path, [], depth=1, max_depth=1)

    try:
        agent.validate_tool("delegate", {"task": "inspect README.md", "max_steps": 2})
    except ValueError as exc:
        assert "delegate depth exceeded" in str(exc)
    else:
        raise AssertionError("delegate depth validation did not fail")


# 楠岃瘉 delegate child is read only 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_delegate_child_is_read_only(tmp_path):
    target = tmp_path / "child-was-not-allowed.txt"
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"delegate","args":{"task":"write a file","max_steps":2}}</tool>',
            '<tool>{"name":"write_file","args":{"path":"child-was-not-allowed.txt","content":"nope"}}</tool>',
            "<final>child done</final>",
            "<final>parent done</final>",
        ],
    )

    result = agent.ask("Delegate the work")

    assert result == "parent done"
    assert not target.exists()
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "delegate"
    assert "delegate_result" in tool_events[0]["content"]


# 楠岃瘉 configured secret env names are redacted in trace and report 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_configured_secret_env_names_are_redacted_in_trace_and_report(tmp_path):
    github_pat = "ghp_configured_secret_123"
    gh_pat = "ghp_configured_secret_456"
    with patch.dict(os.environ, {"GITHUB_PAT": github_pat, "GH_PAT": gh_pat}, clear=True):
        agent = build_agent(
            tmp_path,
            [],
            secret_env_names=("GITHUB_PAT", "GH_PAT"),
        )
        state = TaskState.create(run_id="run_001", task_id="task_001", user_request="Mask configured secrets")
        agent.run_store.start_run(state)

        assert set(agent.secret_env_summary()["secret_env_names"]) == {"GITHUB_PAT", "GH_PAT"}

        payload = {
            "GITHUB_PAT": github_pat,
            "GH_PAT": gh_pat,
            "nested": {"GITHUB_PAT": github_pat, "GH_PAT": gh_pat},
            "list": [github_pat, gh_pat],
        }
        agent.emit_trace(state, "tool_executed", payload)
        agent.run_store.write_report(
            state,
            agent.redact_artifact({"task_state": state.to_dict(), "payload": payload}),
        )

    run_dir = agent.run_store.run_dir(state.run_id)
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    report_text = (run_dir / "report.json").read_text(encoding="utf-8")

    assert github_pat not in trace_text
    assert gh_pat not in trace_text
    assert github_pat not in report_text
    assert gh_pat not in report_text
    assert trace_text.count("<redacted>") >= 4
    assert report_text.count("<redacted>") >= 4

