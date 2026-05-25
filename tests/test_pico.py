"""Coda 鏍稿績杩愯閾捐矾鐨勯泦鎴愬瀷鍗曞厓娴嬭瘯銆?

缁欏垵瀛﹁€呯殑闃呰寤鸿锛?
杩欎釜鏂囦欢涓嶆槸鍙祴涓€涓嚱鏁帮紝鑰屾槸鍦ㄧ敤寰堝灏忓満鏅獙璇?Coda 杩欐潯涓婚摼璺細

    鐢ㄦ埛璇锋眰 -> Coda.ask() -> 鏋勫缓 prompt -> 璋冩ā鍨?-> 瑙ｆ瀽 tool/final
    -> 鎵ц宸ュ叿 -> 鏇存柊 session/memory -> 鍐?trace/report -> 杩斿洖绛旀

涓轰簡璁╂祴璇曠ǔ瀹氾紝杩欓噷澶ч噺浣跨敤 FakeModelClient銆傚畠涓嶄細鐪熺殑璇锋眰澶фā鍨嬶紝
鑰屾槸鎸夋垜浠鍏堢粰瀹氱殑 outputs 涓€鏉℃潯杩斿洖銆傝繖鏍蜂綘 debug 鏃剁湅鍒扮殑妯″瀷杈撳嚭
鏄‘瀹氱殑锛屼笉浼氬彈缃戠粶銆丄PI key 鎴栨ā鍨嬮殢鏈烘€у奖鍝嶃€?

鍑犱釜甯歌娴嬭瘯姒傚康锛?
- tmp_path: pytest 鎻愪緵鐨勪复鏃剁洰褰曪紝姣忎釜娴嬭瘯鐙珛锛岄伩鍏嶆薄鏌撶湡瀹炰粨搴撱€?
- FakeModelClient: 鍋囨ā鍨嬶紝鐢ㄥ浐瀹氳緭鍑烘ā鎷熸ā鍨嬩笅涓€姝ヨ鍋氫粈涔堛€?
- build_agent(): 鏈枃浠堕噷鐨勬祴璇曡緟鍔╁嚱鏁帮紝蹇€熷垱寤轰竴涓彲杩愯鐨?Coda銆?
- agent.ask(): Coda 涓€娆＄敤鎴疯姹傜殑涓诲叆鍙ｏ紝涔熸槸鏈€鍊煎緱鎵撴柇鐐圭殑鍦版柟銆?
"""

import os
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import coda as mini_pkg
from coda import (
    AnthropicCompatibleModelClient,
    FakeModelClient,
    MiniAgent,
    OllamaModelClient,
    OpenAICompatibleModelClient,
    SessionStore,
    WorkspaceContext,
    build_welcome,
)


# 鏋勫缓 build workspace 闇€瑕佺殑鏁版嵁鎴栧璞★紝渚涘悗缁祦绋嬬户缁娇鐢ㄣ€?
def build_workspace(tmp_path):
    """鍒涘缓涓€涓渶灏忓伐浣滃尯锛屽苟鐢熸垚 WorkspaceContext銆?

    Coda 鍚姩鏃堕渶瑕佸厛鐭ラ亾鈥滃綋鍓嶄粨搴撴槸浠€涔堟牱鈥濄€傛祴璇曢噷涓嶆兂渚濊禆鐪熷疄浠撳簱锛?
    鎵€浠ョ敤 tmp_path 涓存椂閫犱竴涓皬浠撳簱鐩綍锛屽苟鍐欎竴涓?README.md銆?
    """
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


# 鏍规嵁 CLI 鍙傛暟缁勮 Coda agent锛屽畬鎴愭ā鍨嬨€佸伐浣滃尯鍜?session 鍒濆鍖栥€?
def build_agent(tmp_path, outputs, **kwargs):
    """鐢?FakeModelClient 鍒涘缓涓€涓祴璇曠敤 Coda agent銆?

    outputs 鏄亣妯″瀷浼氭寜椤哄簭杩斿洖鐨勬枃鏈€備緥濡傦細
    1. 绗竴杞繑鍥?<tool>read_file...</tool>
    2. 绗簩杞繑鍥?<final>Done.</final>

    杩欐牱娴嬭瘯灏辫兘绋冲畾澶嶇幇鈥滄ā鍨嬪厛璋冪敤宸ュ叿锛屽啀缁欐渶缁堢瓟妗堚€濈殑瀹屾暣娴佺▼銆?
    """
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".coda" / "sessions")

    # 娴嬭瘯榛樿鐢?auto锛岃〃绀洪珮椋庨櫓宸ュ叿鑷姩鎵瑰噯銆?
    # 鍗曠嫭娴嬪鎵归€昏緫鏃讹紝娴嬭瘯浼氭樉寮忎紶 approval_policy="ask" 鎴?"never"銆?
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. agent.ask() 涓婚摼璺細妯″瀷杈撳嚭 tool/final锛宺untime 鎺ㄥ姩涓€娆″畬鏁?run
# 楠岃瘉 agent runs tool then final 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?


def test_agent_runs_tool_then_final(tmp_path):
    """鏈€閫傚悎鍒濆鑰呮墦鏂偣鐨勬祴璇曘€?

    杩欎釜娴嬭瘯妯℃嫙浜嗕竴娆℃渶灏?agent 娴佺▼锛?
    - 鐢ㄦ埛璁?agent 妫€鏌?hello.txt
    - 鍋囨ā鍨嬬涓€杞姹傝皟鐢?read_file
    - runtime 鎵ц宸ュ叿锛屽苟鎶婄粨鏋滃啓鍥?history/memory
    - 鍋囨ā鍨嬬浜岃疆缁欏嚭 final
    """
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    # FakeModelClient 浼氭寜鍒楄〃椤哄簭杩斿洖锛?
    # 绗竴鏉℃槸宸ュ叿璋冪敤锛岀浜屾潯鏄渶缁堢瓟妗堛€?
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Read the file successfully.</final>",
        ],
    )

    # 杩欓噷浼氳繘鍏?Coda.ask() 涓诲惊鐜€?
    # 鍦?PyCharm 閲屽彲浠ヤ粠杩欎竴琛?Step Into锛岃瀵?task_state銆乸rompt銆乸arse銆乺un_tool銆?
    answer = agent.ask("Inspect hello.txt")

    # 鏈€缁堢瓟妗堟潵鑷浜岃疆 FakeModelClient 鐨?<final>銆?
    assert answer == "Read the file successfully."

    # 宸ュ叿璋冪敤浼氳鍐欒繘 session history锛屾柟渚夸笅涓€杞?prompt 甯︿笂宸ュ叿缁撴灉銆?
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])

    # read_file 杩樹細鏇存柊 memory锛岃 agent 璁板緱鏈€杩戠杩?hello.txt銆?
    assert "hello.txt" in agent.session["memory"]["files"]


# 楠岃瘉 agent updates task summary on each request 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_updates_task_summary_on_each_request(tmp_path):
    """姣忔 ask() 寮€濮嬫椂锛屽綋鍓嶇敤鎴疯姹備細鍐欒繘 memory 鐨?task_summary銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>First pass.</final>",
            "<final>Second pass.</final>",
        ],
    )

    assert agent.ask("First request") == "First pass."
    assert agent.session["memory"]["working"]["task_summary"] == "First request"

    assert agent.ask("Second request") == "Second pass."
    assert agent.session["memory"]["working"]["task_summary"] == "Second request"


# 楠岃瘉 agent only stores reusable epistemic notes 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_only_stores_reusable_epistemic_notes(tmp_path):
    """娴嬭瘯鈥滄湁浠峰€肩殑浜嬪疄鈥濅細杩?memory锛岃€屾櫘閫?Done 涓嶄細鍙樻垚璁板繂銆?

    杩欓噷绗竴杞 facts.txt 鍚庯紝read_file 鐨勬憳瑕佷細鍙樻垚 episodic note銆?
    鎭㈠ session 鍚庡啀鎻愰棶锛岀浉鍏宠蹇嗕細杩涘叆 prompt 鐨?Relevant memory 鍖哄潡銆?
    """
    (tmp_path / "facts.txt").write_text("deploy key is red\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"facts.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
            "<final>It is red.</final>",
        ],
    )

    assert agent.ask("Read the file and remember the fact") == "Done."
    notes = agent.session["memory"]["episodic_notes"]
    assert any("deploy key is red" in note["text"] for note in notes)
    assert not any(note["text"] == "Done." for note in notes)
    assert not any(note["text"] == "Done." for note in notes)

    # from_session 妯℃嫙 --resume锛氭崲涓€涓柊 Coda 瀵硅薄锛屼絾璇诲洖鏃?session銆?
    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>It is red.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("What color is the deploy key?") == "It is red."
    prompt = resumed.model_client.prompts[-1]
    # 濡傛灉浣?debug 鍒拌繖閲岋紝鍙互灞曞紑 prompt 鐪?Relevant memory 鏄€庝箞鎷艰繘鍘荤殑銆?
    assert "Relevant memory" in prompt
    assert "deploy key is red" in prompt


# 楠岃瘉 file summary cache is invalidated on out of band edit and path spelling 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_file_summary_cache_is_invalidated_on_out_of_band_edit_and_path_spelling(tmp_path):
    """鏂囦欢鍐呭鍙樹簡浠ュ悗锛屾棫 file summary 涓嶈兘缁х画琚綋鐪熴€?

    out-of-band edit 鎸囩殑鏄€滀笉閫氳繃 agent 宸ュ叿淇敼鏂囦欢鈥濓紝鑰屾槸鍦ㄦ祴璇曢噷鐩存帴鏀规枃浠躲€?
    Coda 浼氱敤 freshness锛屼篃灏辨槸鏂囦欢鍐呭 hash锛屽彂鐜版棫鎽樿杩囨湡銆?
    """
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    agent.memory.set_file_summary("./sample.txt", "sample.txt: alpha")
    agent.memory.remember_file("./sample.txt")
    assert agent.memory.to_dict()["file_summaries"]["sample.txt"]["freshness"]

    assert "sample.txt: alpha" in agent.memory.render_memory_text()
    file_path.write_text("beta\n", encoding="utf-8")

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient([]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert "sample.txt: alpha" not in resumed.memory_text()
    resumed.memory.invalidate_file_summary("sample.txt")
    assert "sample.txt" not in resumed.memory.to_dict()["file_summaries"]


# 楠岃瘉 agent retries after empty model output 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_retries_after_empty_model_output(tmp_path):
    """妯″瀷绌鸿緭鍑烘椂锛宺untime 涓嶇洿鎺ュけ璐ワ紝鑰屾槸鍐?retry notice 鍐嶈皟涓€娆℃ā鍨嬨€?""
    agent = build_agent(
        tmp_path,
        [
            "",
            "<final>Recovered after retry.</final>",
        ],
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after retry."
    notices = [item["content"] for item in agent.session["history"] if item["role"] == "assistant"]
    assert any("empty response" in item for item in notices)


# 楠岃瘉 agent retries after malformed tool payload 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_retries_after_malformed_tool_payload(tmp_path):
    """妯″瀷杩斿洖鍧忓伐鍏锋牸寮忔椂锛宲arse() 浼氳繑鍥?retry锛屼笅涓€杞ā鍨嬪彲淇銆?""
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":"bad"}</tool>',
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
            "<final>Recovered after malformed tool output.</final>",
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Recovered after malformed tool output."
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])
    notices = [item["content"] for item in agent.session["history"] if item["role"] == "assistant"]
    assert any("valid <tool> call" in item for item in notices)


# 楠岃瘉 agent accepts xml write file tool 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_accepts_xml_write_file_tool(tmp_path):
    """XML 椋庢牸宸ュ叿璋冪敤閫傚悎鍐欏琛屽唴瀹癸紝閬垮厤 JSON 瀛楃涓茶浆涔夊お楹荤儲銆?""
    agent = build_agent(
        tmp_path,
        [
            '<tool name="write_file" path="hello.py"><content>print("hi")\n</content></tool>',
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Create hello.py")

    assert answer == "Done."
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hi")\n'


# 楠岃瘉 retries do not consume the whole budget 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_retries_do_not_consume_the_whole_budget(tmp_path):
    """retry 娆℃暟鍜?tool_steps 鍒嗗紑绠椼€?

    max_steps 闄愬埗鐨勬槸宸ュ叿鎵ц姝ユ暟锛屼笉鏄ā鍨嬭瑕佹眰淇鏍煎紡鐨勬鏁般€?
    """
    agent = build_agent(
        tmp_path,
        [
            "",
            "",
            "<final>Recovered after several retries.</final>",
        ],
        max_steps=1,
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after several retries."


# 楠岃瘉 agent saves and resumes session 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_saves_and_resumes_session(tmp_path):
    """session 浼氳惤鐩橈紝涔嬪悗鍙互鐢?from_session 鎭㈠ history/memory銆?""
    agent = build_agent(tmp_path, ["<final>First pass.</final>"])
    assert agent.ask("Start a session") == "First pass."

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.session["history"][0]["content"] == "Start a session"
    assert resumed.ask("Continue") == "Resumed."


# 楠岃瘉 delegate uses child agent 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_delegate_uses_child_agent(tmp_path):
    """delegate 浼氬惎鍔ㄤ竴涓彧璇诲瓙 agent 璋冩煡锛屽啀鎶婄粨鏋滀氦鍥炵埗 agent銆?""
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"delegate","args":{"task":"inspect README","max_steps":2}}</tool>',
            "<final>Child result.</final>",
            "<final>Parent incorporated the child result.</final>",
        ],
    )

    answer = agent.ask("Use delegation")

    assert answer == "Parent incorporated the child result."
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "delegate"
    assert "delegate_result" in tool_events[0]["content"]


# ---------------------------------------------------------------------------
# 2. 宸ュ叿灞傛姢鏍忥細绮剧‘ patch銆佸弬鏁版牎楠屻€侀殣钘忓唴閮ㄧ洰褰曘€侀噸澶嶈皟鐢ㄦ嫤鎴?
# 楠岃瘉 patch file replaces exact match 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?


def test_patch_file_replaces_exact_match(tmp_path):
    """patch_file 鍙仛绮剧‘鏇挎崲锛歰ld_text 蹇呴』鍦ㄦ枃浠堕噷鍞竴鍑虹幇銆?""
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "patch_file",
        {
            "path": "sample.txt",
            "old_text": "world",
            "new_text": "agent",
        },
    )

    assert result == "patched sample.txt"
    assert file_path.read_text(encoding="utf-8") == "hello agent\n"


# 楠岃瘉 invalid risky tool does not prompt for approval 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_invalid_risky_tool_does_not_prompt_for_approval(tmp_path):
    """鍙傛暟閮戒笉鍚堟硶鏃讹紝runtime 涓嶅簲璇ヨ繘鍏ュ鎵规楠ゃ€?

    杩欎綋鐜?run_tool() 鐨勯『搴忥細鍏?validate_tool锛屽啀 approval銆?
    """
    agent = build_agent(tmp_path, [], approval_policy="ask")

    with patch("builtins.input") as mock_input:
        result = agent.run_tool("write_file", {})

    assert result.startswith("error: invalid arguments for write_file: 'path'")
    assert 'example: <tool name="write_file"' in result
    mock_input.assert_not_called()


# 楠岃瘉 list files hides internal agent state 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_list_files_hides_internal_agent_state(tmp_path):
    """.coda 鍜?.git 鏄唴閮ㄧ姸鎬佺洰褰曪紝list_files 涓嶅簲璇ユ毚闇插畠浠粰妯″瀷銆?""
    agent = build_agent(tmp_path, [])
    (tmp_path / ".coda").mkdir(exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")

    result = agent.run_tool("list_files", {})

    assert ".coda" not in result
    assert ".git" not in result
    assert "[F] hello.txt" in result


# 楠岃瘉 repeated identical tool call is rejected 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_repeated_identical_tool_call_is_rejected(tmp_path):
    """杩炵画閲嶅宸ュ叿璋冪敤浼氳鎷掔粷锛岄槻姝㈡ā鍨嬪崱鍦ㄦ棤鏁堝惊鐜噷銆?""
    agent = build_agent(tmp_path, [])
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "1"})
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "2"})

    result = agent.run_tool("list_files", {})

    assert result == "error: repeated identical tool call for list_files; choose a different tool or return a final answer"


# 楠岃瘉 welcome screen keeps box shape for long paths 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_welcome_screen_keeps_box_shape_for_long_paths(tmp_path):
    """娆㈣繋椤靛彧鏄?CLI 灞曠ず灞傦紝浣嗛暱璺緞涔熶笉鑳芥妸杈规鎾戝潖銆?""
    deep = tmp_path / "very" / "long" / "path" / "for" / "the" / "mini" / "agent" / "welcome" / "screen"
    deep.mkdir(parents=True)
    agent = build_agent(deep, [])

    welcome = build_welcome(agent, model="qwen3.5:4b", host="http://127.0.0.1:11434")
    lines = welcome.splitlines()

    assert len(lines) >= 5
    assert len({len(line) for line in lines}) == 1
    assert "..." in welcome
    assert "(  o o  )" in welcome
    assert "MINI-CODING-AGENT" not in welcome
    assert "MINI CODING AGENT" not in welcome
    assert "coda" in welcome
    assert "local coding agent" in welcome
    assert "// READY" not in welcome
    assert "SLASH" not in welcome
    assert "READY      " not in welcome
    assert "commands: Commands:" not in welcome


# ---------------------------------------------------------------------------
# 3. 妯″瀷鍚庣閫傞厤锛氫笉鐪熺殑鑱旂綉锛岀敤 fake_urlopen 楠岃瘉 HTTP payload 鍜岃В鏋愰€昏緫
# 楠岃瘉 ollama client posts expected payload 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?


def test_ollama_client_posts_expected_payload():
    """Ollama client 搴旇鍚?/api/generate 鍙戦€佺害瀹氭牸寮忕殑 JSON銆?""
    captured = {}

    class FakeResponse:
        # 杩欎釜绫绘ā鎷?urllib.request.urlopen 杩斿洖鐨?response 瀵硅薄銆?
        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return json.dumps({"response": "<final>ok</final>"}).encode("utf-8")

    # 澶勭悊 fake urlopen 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
    def fake_urlopen(request, timeout):
        # fake_urlopen 涓嶈仈缃戯紝鍙妸璇锋眰鍐呭鎶撳埌 captured锛屼緵涓嬮潰 assert 妫€鏌ャ€?
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OllamaModelClient(
        model="qwen3.5:4b",
        host="http://127.0.0.1:11434",
        temperature=0.2,
        top_p=0.9,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["timeout"] == 30
    assert captured["body"]["model"] == "qwen3.5:4b"
    assert captured["body"]["prompt"] == "hello"
    assert captured["body"]["stream"] is False


# 楠岃瘉 openai compatible client posts expected responses payload 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_openai_compatible_client_posts_expected_responses_payload():
    """OpenAI-compatible client 搴旇璧?/responses锛屽苟甯?Authorization header銆?""
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return json.dumps({"output_text": "<final>ok</final>"}).encode("utf-8")

    # 澶勭悊 fake urlopen 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
    def fake_urlopen(request, timeout):
        # 杩欓噷妫€鏌ョ殑鏄?client 鏋勯€?HTTP 璇锋眰鏄惁姝ｇ‘锛屼笉娴嬬湡瀹炴湇鍔°€?
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "https://right.codes/v1/responses"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["User-agent"] == "coda/0.1"
    assert captured["body"] == {
        "model": "right.codes/codex-mini",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "hello",
                    }
                ],
            }
        ],
        "max_output_tokens": 42,
        "stream": False,
        "temperature": 0.2,
    }


# 楠岃瘉 openai compatible client sends prompt cache fields and records usage 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_openai_compatible_client_sends_prompt_cache_fields_and_records_usage():
    """鍚庣鏀寔 prompt cache 鏃讹紝璇锋眰瑕佸甫 cache 瀛楁锛屽搷搴?usage 瑕佽璁板綍銆?""
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return json.dumps(
                {
                    "output_text": "<final>ok</final>",
                    "usage": {
                        "input_tokens": 2048,
                        "input_tokens_details": {"cached_tokens": 1536},
                        "output_tokens": 32,
                        "total_tokens": 2080,
                    },
                }
            ).encode("utf-8")

    # 澶勭悊 fake urlopen 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete(
            "hello",
            42,
            prompt_cache_key="prefix-hash-123",
            prompt_cache_retention="in_memory",
        )

    assert result == "<final>ok</final>"
    assert captured["body"]["prompt_cache_key"] == "prefix-hash-123"
    assert captured["body"]["prompt_cache_retention"] == "in_memory"
    assert client.last_completion_metadata["prompt_cache_supported"] is True
    assert client.last_completion_metadata["cached_tokens"] == 1536
    assert client.last_completion_metadata["cache_hit"] is True
    assert client.last_completion_metadata["input_tokens"] == 2048


# 楠岃瘉 openai compatible client extracts text from event stream 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_openai_compatible_client_extracts_text_from_event_stream():
    """鏈変簺鍏煎鍚庣浼氳繑鍥?SSE/event-stream锛岃繖閲岄獙璇佽兘浠庝簨浠堕噷鎶藉嚭鏂囨湰銆?""
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return (
                'data: {"type":"response.created","response":{"id":"resp_1","output":[]}}\n'
                'data: {"type":"response.completed","response":{"output":[{"content":[{"text":"<final>stream ok</final>"}]}]}}\n'
                "data: [DONE]\n"
            ).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>stream ok</final>"


# 楠岃瘉 openai compatible client extracts text from event stream deltas 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_openai_compatible_client_extracts_text_from_event_stream_deltas():
    """SSE delta 褰㈠紡浼氫竴鐐圭偣鍚愭枃鏈紝client 瑕佽兘鎷煎嚭鏈€缁?answer銆?""
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return (
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"<final>"}\n'
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"OK"}\n'
                'event: response.output_text.done\n'
                'data: {"type":"response.output_text.done","text":"<final>OK</final>"}\n'
                "data: [DONE]\n"
            ).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>OK</final>"


# 楠岃瘉 anthropic compatible client posts expected messages payload 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_anthropic_compatible_client_posts_expected_messages_payload():
    """Anthropic-compatible client 搴旇璧?/messages锛屽苟浣跨敤 x-api-key header銆?""
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return json.dumps(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "<final>ok</final>",
                        }
                    ]
                }
            ).encode("utf-8")

    # 澶勭悊 fake urlopen 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = AnthropicCompatibleModelClient(
        model="claude-sonnet-4-5-20250929",
        base_url="https://www.right.codes/claude-aws/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "https://www.right.codes/claude-aws/v1/messages"
    assert captured["timeout"] == 30
    assert captured["headers"]["X-api-key"] == "sk-test"
    assert captured["headers"]["Anthropic-version"] == "2023-06-01"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"] == {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                    }
                ],
            }
        ],
        "max_tokens": 42,
        "stream": False,
        "temperature": 0.2,
    }


# 楠岃瘉 anthropic compatible client extracts first text block 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_anthropic_compatible_client_extracts_first_text_block():
    """Anthropic 鍝嶅簲鍙兘鍖呭惈 thinking/text 澶氫釜鍧楋紝client 瑕佹娊绗竴涓?text 鍧椼€?""
    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        # 澶勭悊 enter 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __enter__(self):
            return self

        # 澶勭悊 exit 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def __exit__(self, exc_type, exc, tb):
            return False

        # 澶勭悊 read 鐩稿叧閫昏緫锛屾敮鎾戝綋鍓嶆ā鍧楃殑涓昏娴佺▼銆?
        def read(self):
            return json.dumps(
                {
                    "content": [
                        {"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "<final>ok</final>"},
                    ]
                }
            ).encode("utf-8")

    client = AnthropicCompatibleModelClient(
        model="claude-sonnet-4-5-20250929",
        base_url="https://www.right.codes/claude-aws/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"


# ---------------------------------------------------------------------------
# 4. CLI/build_agent 瑁呴厤锛歱rovider銆乵odel銆乪nv銆乫allback 鎬庝箞鍙樻垚 model_client
# 楠岃瘉 build agent uses openai provider and model override 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?


def test_build_agent_uses_openai_provider_and_model_override(tmp_path):
    """鏄惧紡 --model 鐨勪紭鍏堢骇楂樹簬鐜鍙橀噺閲岀殑 OPENAI_MODEL銆?""
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "openai",
            "model": "override-model",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "secret_env_names": [],
            "max_steps": 6,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "env-model",
        },
        clear=False,
    ):
        # patch 鎺夌湡瀹?client 绫伙紝閬垮厤娴嬭瘯鐪熺殑鍒涘缓缃戠粶 client銆?
        # 濡傛灉 build_agent 閿欒蛋 Ollama锛岃繖閲屼細鐩存帴鎶?AssertionError銆?
        with patch(
            "coda.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch("coda.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "override-model"
    assert mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client


# 楠岃瘉 build arg parser defaults provider to openai 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_arg_parser_defaults_provider_to_openai(tmp_path):
    """涓嶄紶 --provider 鏃讹紝榛樿璧?openai provider銆?""
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    assert args.provider == "openai"


# 楠岃瘉 build arg parser accepts anthropic provider 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_arg_parser_accepts_anthropic_provider(tmp_path):
    """argparse 搴旇鎺ュ彈 anthropic 浣滀负 provider銆?""
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "anthropic"])

    assert args.provider == "anthropic"


# 楠岃瘉 build arg parser accepts deepseek provider 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_arg_parser_accepts_deepseek_provider(tmp_path):
    """argparse 搴旇鎺ュ彈 deepseek 浣滀负 provider銆?""
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "deepseek"])

    assert args.provider == "deepseek"


# 楠岃瘉 build agent uses anthropic provider and openai key fallback 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_agent_uses_anthropic_provider_and_openai_key_fallback(tmp_path):
    """Anthropic-compatible 鍙互鍥為€€浣跨敤 OPENAI_API_KEY銆?

    鏈変簺浠ｇ悊鏈嶅姟澶氱鍏煎鎺ュ彛鍏辩敤涓€濂?key锛屾墍浠ヨ繖閲岄獙璇?fallback 閾捐矾銆?
    """
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "openai_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "secret_env_names": [],
            "max_steps": 6,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "dummy-openai-fallback",
        },
        clear=True,
    ):
        with patch(
            "coda.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch(
            "coda.cli.OpenAICompatibleModelClient",
            side_effect=AssertionError("openai client should not be used"),
        ), patch("coda.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            fake_client = mock_anthropic.return_value
            agent = mini_pkg.build_agent(args)

    mock_anthropic.assert_called_once()
    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert mock_anthropic.call_args.kwargs["base_url"] == "https://www.right.codes/claude/v1"
    assert mock_anthropic.call_args.kwargs["api_key"] == "dummy-openai-fallback"
    assert agent.model_client is fake_client


# 楠岃瘉 build agent uses anthropic default model when env is missing 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_agent_uses_anthropic_default_model_when_env_is_missing(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "anthropic"])

    with patch.dict(
        os.environ,
        {},
        clear=False,
    ):
        os.environ.pop("ANTHROPIC_MODEL", None)
        with patch("coda.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            mini_pkg.build_agent(args)

    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-6"


# 楠岃瘉 build agent uses deepseek provider and env configuration 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_agent_uses_deepseek_provider_and_env_configuration(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODA_DEEPSEEK_API_BASE=https://api.deepseek.com/anthropic",
                "CODA_DEEPSEEK_API_KEY=dummy-project-deepseek",
                "CODA_DEEPSEEK_MODEL=deepseek-v4-pro",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "deepseek",
            "model": None,
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "openai_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "secret_env_names": [],
            "max_steps": 6,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "DEEPSEEK_API_BASE": "https://legacy.deepseek.example/anthropic",
            "DEEPSEEK_API_KEY": "sk-legacy-deepseek",
            "DEEPSEEK_MODEL": "legacy-deepseek-model",
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "OPENAI_API_KEY": "sk-openai",
        },
        clear=True,
    ):
        with patch(
            "coda.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch(
            "coda.cli.OpenAICompatibleModelClient",
            side_effect=AssertionError("openai client should not be used"),
        ), patch("coda.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            fake_client = mock_anthropic.return_value
            agent = mini_pkg.build_agent(args)

    mock_anthropic.assert_called_once()
    assert mock_anthropic.call_args.kwargs["model"] == "deepseek-v4-pro"
    assert mock_anthropic.call_args.kwargs["base_url"] == "https://api.deepseek.com/anthropic"
    assert mock_anthropic.call_args.kwargs["api_key"] == "dummy-project-deepseek"
    assert agent.model_client is fake_client


# 楠岃瘉 build agent uses deepseek default model when env is missing 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_agent_uses_deepseek_default_model_when_env_is_missing(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "deepseek"])

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
        with patch("coda.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            mini_pkg.build_agent(args)

    assert mock_anthropic.call_args.kwargs["model"] == "deepseek-v4-pro"
    assert mock_anthropic.call_args.kwargs["base_url"] == "https://api.deepseek.com/anthropic"


# 楠岃瘉 build agent uses openai provider by default 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_build_agent_uses_openai_provider_by_default(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
        },
        clear=False,
    ):
        with patch(
            "coda.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch("coda.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "gpt-5.4"
    assert mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client


# 楠岃瘉 successful run persists run artifacts and stop reason 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_successful_run_persists_run_artifacts_and_stop_reason(tmp_path):
    """涓€娆℃垚鍔?ask() 缁撴潫鍚庯紝搴旂暀涓嬪畬鏁?run 宸ヤ欢銆?

    杩欓噷楠岃瘉 .coda/runs/<run_id>/ 涓嬬殑涓変欢濂楋細
    - task_state.json: 鏈€缁堢姸鎬佸揩鐓?
    - trace.jsonl: 杩囩▼浜嬩欢
    - report.json: 鑱氬悎鎽樿
    """
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Finished.</final>",
        ],
    )

    assert agent.ask("Do the thing") == "Finished."

    runs_root = tmp_path / ".coda" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    # task_state/report 鏄?JSON 瀵硅薄锛泃race 鏄?JSONL锛屾墍浠ヨ鎸夎璇汇€?
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    assert task_state["task_id"] != task_state["run_id"]
    assert run_dir.name == task_state["run_id"]
    assert (run_dir / "task_state.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "report.json").exists()
    assert task_state["stop_reason"] == "final_answer_returned"
    assert task_state["final_answer"] == "Finished."
    assert report["stop_reason"] == "final_answer_returned"
    assert report["task_state"]["stop_reason"] == "final_answer_returned"
    assert report["run_id"] == task_state["run_id"]

    # trace 鏄簨浠舵椂闂寸嚎銆傝繖涓満鏅腑妯″瀷鍏堣皟鐢ㄥ伐鍏枫€佸啀杩斿洖 final锛?
    # 鎵€浠ヤ細鍑虹幇涓ゆ prompt_built銆?
    trace_events = [json.loads(line)["event"] for line in trace_lines]
    assert trace_events[0] == "run_started"
    assert trace_events[-1] == "run_finished"
    assert trace_events.count("prompt_built") == 2
    assert "tool_executed" in trace_events


# 楠岃瘉 trace and report redact secret env values 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_trace_and_report_redact_secret_env_values(tmp_path):
    """trace/report 涓嶈兘鎶婄幆澧冨彉閲忛噷鐨勫瘑閽ュ師鏍峰啓鍑烘潵銆?""
    secret = "sk-test-secret-123"
    with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=True):
        agent = build_agent(
            tmp_path,
            [
                '<tool>{"name":"run_shell","args":{"command":"printf \'%s\' \'sk-test-secret-123\'","timeout":20}}</tool>',
                "<final>Masked.</final>",
            ],
        )

        assert agent.ask("Mask the secret") == "Masked."

    runs_root = tmp_path / ".coda" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    report_text = (run_dir / "report.json").read_text(encoding="utf-8")
    trace_events = [json.loads(line) for line in trace_text.splitlines()]

    assert secret not in trace_text
    assert secret not in report_text

    # prompt_built 鐨?metadata 浼氳褰曟娴嬪埌浜嗗摢浜?secret 鐜鍙橀噺鍚嶏紝
    # 浣嗕笉浼氭硠闇插叿浣撳€笺€?
    prompt_events = [event for event in trace_events if event["event"] == "prompt_built"]
    assert prompt_events
    assert prompt_events[0]["prompt_metadata"]["secret_env_count"] >= 1
    assert "OPENAI_API_KEY" in prompt_events[0]["prompt_metadata"]["secret_env_names"]

    # 宸ュ叿鍙傛暟鍜屽伐鍏风粨鏋滈兘瑕佽劚鏁忋€?
    tool_events = [event for event in trace_events if event["event"] == "tool_executed"]
    assert tool_events
    assert "<redacted>" in tool_events[0]["args"]["command"]
    assert "<redacted>" in tool_events[0]["result"]


# 楠岃瘉 prompt budget metadata records budget decisions 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_prompt_budget_metadata_records_budget_decisions(tmp_path):
    """prompt metadata 瑕佽褰曚笂涓嬫枃棰勭畻鍜?relevant memory 娓叉煋鎯呭喌銆?""
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.memory.append_note("alpha episodic note " + ("A" * 120), tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    agent.memory.append_note("beta episodic recall note " + ("B" * 120), created_at="2026-04-07T10:01:00+00:00")
    agent.memory.append_note("gamma episodic note " + ("C" * 120), tags=("recall",), created_at="2026-04-07T10:02:00+00:00")

    for index in range(4):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"history-{index}-" + ("A" * 240),
                "created_at": f"2026-04-07T10:0{index}:00+00:00",
            }
        )

    # 浜轰负鎶婇绠楄皟灏忥紝寮鸿揩 ContextManager 杩涘叆鍘嬬缉璺緞銆?
    agent.context_manager.total_budget = 1000
    agent.context_manager.section_budgets = {
        "prefix": 80,
        "memory": 80,
        "relevant_memory": 80,
        "history": 80,
    }

    assert agent.ask("recall") == "Done."

    # 浠?trace 閲屽彇 prompt_built 浜嬩欢锛屾鏌?metadata 鏄惁濡傚疄璁板綍浜嗛绠楀喅绛栥€?
    trace_events = [
        json.loads(line)
        for line in (agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines())
    ]
    prompt_events = [event for event in trace_events if event["event"] == "prompt_built"]
    assert prompt_events
    metadata = prompt_events[0]["prompt_metadata"]
    relevant_section = agent.model_client.prompts[0].split("Relevant memory:\n", 1)[1].split("\n\nTranscript:", 1)[0]

    assert metadata["relevant_memory"]["selected_count"] == 3
    assert len(metadata["relevant_memory"]["rendered_notes"]) == 3
    assert len([line for line in relevant_section.splitlines() if line.startswith("- ")]) == 3
    assert "alpha episodic" in relevant_section
    assert "beta episodic" in relevant_section
    assert "gamma episodic" in relevant_section
    assert metadata["current_request"]["text"] == "recall"
    assert metadata["current_request"]["rendered_chars"] == len("recall")


# 楠岃瘉 prompt metadata refreshes prefix when workspace changes 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_prompt_metadata_refreshes_prefix_when_workspace_changes(tmp_path):
    """宸ヤ綔鍖烘憳瑕佸彉鍖栨椂锛宲refix_hash 搴旇鍙樺寲锛涙病鍙樺寲鏃跺簲澶嶇敤銆?""
    agent = build_agent(tmp_path, [])

    first = agent.prompt_metadata("first", "")
    second = agent.prompt_metadata("second", "")

    assert first["prefix_hash"] == second["prefix_hash"]
    assert second["prefix_changed"] is False
    assert second["workspace_changed"] is False

    # 淇敼 README 浼氭敼鍙?WorkspaceContext.project_docs锛?
    # 杩涜€屾敼鍙?workspace fingerprint 鍜?prefix hash銆?
    (tmp_path / "README.md").write_text("demo changed\n", encoding="utf-8")

    third = agent.prompt_metadata("third", "")

    assert third["prefix_hash"] != second["prefix_hash"]
    assert third["prefix_changed"] is True
    assert third["workspace_changed"] is True
    assert "demo changed" in agent.prefix


# 楠岃瘉 agent creates checkpoint when context reduction happens and artifacts only reference it 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_creates_checkpoint_when_context_reduction_happens_and_artifacts_only_reference_it(tmp_path):
    """涓婁笅鏂囧帇缂╁彂鐢熸椂浼氬垱寤?checkpoint锛屼絾 run artifact 鍙紩鐢?checkpoint_id銆?

    璁捐涓?checkpoint 璇︽儏鏀惧湪 session 閲岋紝task_state/report/trace 鍙瓨 id锛?
    閬垮厤杩愯宸ヤ欢閲岄噸澶嶅鍏ュぇ閲忔仮澶嶇姸鎬併€?
    """
    agent = build_agent(tmp_path, ["<final>Done after checkpoint.</final>"])
    for index in range(10):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"history-{index}-" + ("A" * 260),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )
    agent.memory.append_note("checkpoint note " + ("B" * 220), tags=("checkpoint",), created_at="2026-04-07T11:00:00+00:00")
    agent.context_manager.total_budget = 900
    agent.context_manager.section_budgets = {
        "prefix": 120,
        "memory": 120,
        "relevant_memory": 120,
        "history": 160,
    }

    assert agent.ask("Resume the long task") == "Done after checkpoint."

    # checkpoint 璇︽儏淇濆瓨鍦?session["checkpoints"]銆?
    checkpoint_state = agent.session["checkpoints"]
    checkpoint = checkpoint_state["items"][checkpoint_state["current_id"]]
    assert checkpoint["checkpoint_id"] == checkpoint_state["current_id"]
    assert checkpoint["schema_version"] == "phase1-v1"
    assert checkpoint["current_goal"] == "Resume the long task"
    assert checkpoint["key_files"] == []
    assert checkpoint["current_blocker"] == ""
    assert checkpoint["next_step"]

    task_state = json.loads(agent.run_store.task_state_path(agent.current_task_state).read_text(encoding="utf-8"))
    report = json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))
    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]

    assert task_state["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert report["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert report["task_state"]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert "current_goal" not in task_state
    assert "current_goal" not in report
    checkpoint_events = [event for event in trace_events if event["event"] == "checkpoint_created"]
    assert checkpoint_events
    assert checkpoint_events[-1]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert "current_goal" not in checkpoint_events[-1]


# 楠岃瘉 resume prompt uses checkpoint state not just history 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_prompt_uses_checkpoint_state_not_just_history(tmp_path):
    """resume 鍚?prompt 搴旇甯?Task checkpoint锛岃€屼笉鏄彧渚濊禆鏃?history銆?""
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_manual",
        "items": {
            "ckpt_manual": {
                "checkpoint_id": "ckpt_manual",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Fix failing resume flow",
                "completed": ["Read runtime.py"],
                "excluded": ["Do not add branch summary"],
                "current_blocker": "Need to re-anchor stale file facts",
                "next_step": "Re-read runtime.py and refresh the checkpoint",
                "key_files": [{"path": "runtime.py", "freshness": "abc"}],
                "freshness": {"runtime.py": "abc"},
                "summary": "Resume from the latest checkpoint",
                "runtime_identity": {"workspace_fingerprint": "old-fingerprint"},
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."

    # 杩欓噷鐩存帴妫€鏌ユā鍨嬬湅鍒扮殑 prompt锛岀‘璁?checkpoint 淇℃伅杩涘叆浜嗕笂涓嬫枃銆?
    prompt = resumed.model_client.prompts[-1]
    assert "Task checkpoint:" in prompt
    assert "Current goal: Fix failing resume flow" in prompt
    assert "Current blocker: Need to re-anchor stale file facts" in prompt
    assert "Next step: Re-read runtime.py and refresh the checkpoint" in prompt


# 楠岃瘉 resume invalidates stale file summaries and marks partial stale 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_invalidates_stale_file_summaries_and_marks_partial_stale(tmp_path):
    """鍏抽敭鏂囦欢鍙樹簡浠ュ悗锛屾棫 file_summary 浼氬け鏁堬紝resume_status 鍙?partial-stale銆?""
    file_path = tmp_path / "runtime.py"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.memory.set_file_summary("runtime.py", "runtime.py: alpha")
    freshness = agent.memory.to_dict()["file_summaries"]["runtime.py"]["freshness"]
    agent.session["checkpoints"] = {
        "current_id": "ckpt_stale",
        "items": {
            "ckpt_stale": {
                "checkpoint_id": "ckpt_stale",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Fix stale summary handling",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Re-read runtime.py",
                "key_files": [{"path": "runtime.py", "freshness": freshness}],
                "freshness": {"runtime.py": freshness},
                "summary": "runtime.py is important",
                "runtime_identity": {"workspace_fingerprint": agent.workspace.fingerprint()},
            }
        },
    }
    agent.session_store.save(agent.session)

    # 杩欓噷妯℃嫙澶栭儴鏀瑰姩锛氭枃浠跺湪 checkpoint 涔嬪悗琚敼浜嗐€?
    file_path.write_text("beta\n", encoding="utf-8")

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."

    assert "runtime.py" not in resumed.memory.to_dict()["file_summaries"]
    assert resumed.last_prompt_metadata["resume_status"] == "partial-stale"
    assert resumed.last_prompt_metadata["stale_summary_invalidations"] == 1


# 楠岃瘉 run shell nonzero with workspace change is recorded as partial success 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_run_shell_nonzero_with_workspace_change_is_recorded_as_partial_success(tmp_path):
    """shell 鍛戒护澶辫触浣嗘敼浜嗘枃浠舵椂锛岀姸鎬佸簲鏄?partial_success銆?

    杩欐彁閱掍笅涓€杞ā鍨嬶細涓嶈兘鍙湅 exit_code锛屼篃瑕佹鏌ュ伐浣滃尯 diff銆?
    """
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    assert "exit_code: 1" in result
    assert agent._last_tool_result_metadata["tool_status"] == "partial_success"
    assert agent._last_tool_result_metadata["affected_paths"] == ["README.md"]
    assert agent._last_tool_result_metadata["workspace_changed"] is True


# 楠岃瘉 resume marks workspace mismatch when checkpoint runtime identity is stale 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_marks_workspace_mismatch_when_checkpoint_runtime_identity_is_stale(tmp_path):
    """checkpoint 閲岀殑 workspace_fingerprint 杩囨湡鏃讹紝resume_status 鏄?workspace-mismatch銆?""
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_workspace",
        "items": {
            "ckpt_workspace": {
                "checkpoint_id": "ckpt_workspace",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Continue after drift",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Rebuild runtime state",
                "key_files": [],
                "freshness": {},
                "summary": "workspace changed",
                "runtime_identity": {"workspace_fingerprint": "outdated-fingerprint"},
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "workspace-mismatch"


# 楠岃瘉 write file trace records minimum tool contract fields 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_write_file_trace_records_minimum_tool_contract_fields(tmp_path):
    """tool_executed trace 闇€瑕佽褰曞伐鍏锋不鐞嗙殑鏈€灏忓悎鍚屽瓧娈点€?""
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"write_file","args":{"path":"notes.txt","content":"hello\\n"}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Create notes.txt") == "Done."

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    tool_event = [event for event in trace_events if event["event"] == "tool_executed"][-1]

    # 杩欎簺瀛楁鐢ㄤ簬鍚庣画瀹¤鍜屾寚鏍囪仛鍚堬細椋庨櫓銆佸彧璇汇€佺姸鎬併€佸奖鍝嶈矾寰勩€乨iff 鎽樿銆?
    assert tool_event["name"] == "write_file"
    assert tool_event["risk_level"] == "high"
    assert tool_event["read_only"] is False
    assert tool_event["tool_status"] == "ok"
    assert tool_event["affected_paths"] == ["notes.txt"]
    assert tool_event["workspace_changed"] is True
    assert tool_event["diff_summary"] == ["created:notes.txt"]


# 楠岃瘉 resume marks schema mismatch when checkpoint version is incompatible 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_marks_schema_mismatch_when_checkpoint_version_is_incompatible(tmp_path):
    """checkpoint schema 鐗堟湰涓嶅吋瀹规椂锛屼笉鑳藉綋鎴?full-valid 鎭㈠銆?""
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_schema",
        "items": {
            "ckpt_schema": {
                "checkpoint_id": "ckpt_schema",
                "parent_checkpoint_id": "",
                "schema_version": "legacy-v0",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Continue after schema change",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Migrate checkpoint",
                "key_files": [],
                "freshness": {},
                "summary": "schema changed",
                "runtime_identity": {"workspace_fingerprint": agent.workspace.fingerprint()},
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "schema-mismatch"


# 楠岃瘉 resume marks no checkpoint when session has no checkpoint state 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_marks_no_checkpoint_when_session_has_no_checkpoint_state(tmp_path):
    """娌℃湁 checkpoint 鏃讹紝resume_status 鏄?no-checkpoint锛宲rompt 涓嶅睍绀?Task checkpoint銆?""
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session.pop("checkpoints", None)
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "no-checkpoint"
    assert "Task checkpoint:" not in resumed.model_client.prompts[-1]


# 楠岃瘉 freshness mismatch creates checkpoint before model completion 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_freshness_mismatch_creates_checkpoint_before_model_completion(tmp_path):
    """鍙戠幇 freshness mismatch 鏃讹紝浼氬湪妯″瀷瀹屾垚鍓嶅厛鍒涘缓鎭㈠ checkpoint銆?""
    file_path = tmp_path / "runtime.py"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, ["<final>Resumed.</final>"])
    agent.memory.set_file_summary("runtime.py", "runtime.py: alpha")
    freshness = agent.memory.to_dict()["file_summaries"]["runtime.py"]["freshness"]
    agent.session["checkpoints"] = {
        "current_id": "ckpt_freshness",
        "items": {
            "ckpt_freshness": {
                "checkpoint_id": "ckpt_freshness",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Handle freshness mismatch",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Re-read runtime.py",
                "key_files": [{"path": "runtime.py", "freshness": freshness}],
                "freshness": {"runtime.py": freshness},
                "summary": "runtime.py changed",
                "runtime_identity": {"workspace_fingerprint": agent.workspace.fingerprint()},
            }
        },
    }
    agent.session_store.save(agent.session)
    file_path.write_text("beta\n", encoding="utf-8")

    assert agent.ask("Continue the task") == "Resumed."

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    checkpoint_events = [event for event in trace_events if event["event"] == "checkpoint_created"]

    assert checkpoint_events
    assert checkpoint_events[0]["trigger"] == "freshness_mismatch"


# 楠岃瘉 runtime identity persists key execution metadata 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_runtime_identity_persists_key_execution_metadata(tmp_path):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".coda" / "sessions")
    agent = MiniAgent(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="never",
        max_steps=9,
        max_new_tokens=1024,
        feature_flags={"memory": True, "relevant_memory": False},
    )

    runtime_identity = agent.session["runtime_identity"]

    assert runtime_identity["session_id"] == agent.session["id"]
    assert runtime_identity["cwd"] == str(tmp_path)
    assert runtime_identity["approval_policy"] == "never"
    assert runtime_identity["read_only"] is False
    assert runtime_identity["max_steps"] == 9
    assert runtime_identity["max_new_tokens"] == 1024
    assert runtime_identity["feature_flags"]["memory"] is True
    assert runtime_identity["feature_flags"]["relevant_memory"] is False
    assert runtime_identity["shell_env_allowlist"] == list(agent.shell_env_allowlist)


# 楠岃瘉 resume records runtime identity mismatch fields in metadata and trace 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_resume_records_runtime_identity_mismatch_fields_in_metadata_and_trace(tmp_path):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_identity",
        "items": {
            "ckpt_identity": {
                "checkpoint_id": "ckpt_identity",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Resume with a different runtime identity",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Rebuild runtime identity",
                "key_files": [],
                "freshness": {},
                "summary": "identity changed",
                "runtime_identity": {
                    "workspace_fingerprint": agent.workspace.fingerprint(),
                    "approval_policy": "auto",
                    "read_only": False,
                    "max_steps": 6,
                    "max_new_tokens": 512,
                    "model": "old-model",
                    "model_client": "FakeModelClient",
                    "feature_flags": {"memory": True, "relevant_memory": True},
                    "shell_env_allowlist": ["PATH"],
                    "session_id": agent.session["id"],
                    "cwd": str(tmp_path),
                },
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="never",
        max_steps=9,
        max_new_tokens=1024,
        feature_flags={"memory": True, "relevant_memory": False},
    )

    resumed.ask("Continue the task")

    assert resumed.last_prompt_metadata["resume_status"] == "workspace-mismatch"
    assert resumed.last_prompt_metadata["runtime_identity_mismatch_fields"] == [
        "approval_policy",
        "feature_flags",
        "max_new_tokens",
        "max_steps",
        "model",
        "shell_env_allowlist",
    ]

    trace_events = [
        json.loads(line)
        for line in resumed.run_store.trace_path(resumed.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    mismatch_events = [event for event in trace_events if event["event"] == "runtime_identity_mismatch"]
    assert mismatch_events
    assert mismatch_events[0]["fields"] == [
        "approval_policy",
        "feature_flags",
        "max_new_tokens",
        "max_steps",
        "model",
        "shell_env_allowlist",
    ]


# 楠岃瘉 partial success creates process note for exploration history 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_partial_success_creates_process_note_for_exploration_history(tmp_path):
    """partial_success 浼氬彉鎴?process note锛屽府鍔╀笅涓€杞ā鍨嬬煡閬撹妫€鏌ョ幇鍦恒€?""
    agent = build_agent(tmp_path, [])

    agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    process_notes = [
        note
        for note in agent.memory.to_dict()["episodic_notes"]
        if note.get("kind") == "process"
    ]

    assert process_notes
    assert process_notes[-1]["text"] == "run_shell partial_success on README.md; inspect diff before retry"
    assert "partial_success" in process_notes[-1]["tags"]
    assert "README.md" in process_notes[-1]["tags"]


# 楠岃瘉 explicit memory promotion persists durable memory topics 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_explicit_memory_promotion_persists_durable_memory_topics(tmp_path):
    """鐢ㄦ埛鏄庣‘瑕佹眰淇濆瓨闀挎湡璁板繂鏃讹紝绋冲畾浜嬪疄浼氬啓杩?.coda/memory/topics銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.\n"
            "Project convention: Preserve local agent state under .coda/.\n"
            "Decision: Keep durable memory topic-based and lightweight.</final>",
        ],
    )

    answer = agent.ask(
        # Capture/long-term facts 杩欎簺璇嶄細瑙﹀彂 durable memory promotion銆?
        "Capture the stable facts you already discovered as durable memory. "
        "Respond with exactly the long-term facts."
    )

    assert "Project convention:" in answer

    index_path = tmp_path / ".coda" / "memory" / "MEMORY.md"
    conventions_path = tmp_path / ".coda" / "memory" / "topics" / "project-conventions.md"
    decisions_path = tmp_path / ".coda" / "memory" / "topics" / "key-decisions.md"
    report = json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))

    # MEMORY.md 鏄储寮曪紱topics/*.md 鏄寜涓婚鎷嗗紑鐨勯暱鏈熻蹇嗘枃浠躲€?
    assert index_path.exists()
    assert conventions_path.exists()
    assert decisions_path.exists()
    assert "project-conventions" in index_path.read_text(encoding="utf-8")
    assert "Use constrained tools instead of guessing." in conventions_path.read_text(encoding="utf-8")
    assert "Keep durable memory topic-based and lightweight." in decisions_path.read_text(encoding="utf-8")
    assert report["durable_promotions"] == [
        "project-conventions: Use constrained tools instead of guessing.",
        "project-conventions: Preserve local agent state under .coda/.",
        "key-decisions: Keep durable memory topic-based and lightweight.",
    ]


# 楠岃瘉 explicit memory promotion supports chinese intent and labels 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_explicit_memory_promotion_supports_chinese_intent_and_labels(tmp_path):
    """闀挎湡璁板繂鎻愬彇鍚屾椂鏀寔涓枃瑙﹀彂璇嶅拰涓枃鏍囩銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>椤圭洰绾﹀畾锛氫紭鍏堜娇鐢ㄥ彈绾︽潫宸ュ叿锛屼笉瑕侀潬鐚溿€俓n"
            "鍐崇瓥锛氭寔涔呰蹇嗕繚鎸佽交閲忋€佹寜 topic 绠＄悊銆?/final>",
        ],
    )

    answer = agent.ask("璇锋妸涓嬮潰杩欎簺绋冲畾浜嬪疄璁颁綇锛屼綔涓洪暱鏈熻蹇嗕繚瀛樹笅鏉ャ€?)

    assert "椤圭洰绾﹀畾锛? in answer

    conventions_path = tmp_path / ".coda" / "memory" / "topics" / "project-conventions.md"
    decisions_path = tmp_path / ".coda" / "memory" / "topics" / "key-decisions.md"

    assert "浼樺厛浣跨敤鍙楃害鏉熷伐鍏凤紝涓嶈闈犵寽銆? in conventions_path.read_text(encoding="utf-8")
    assert "鎸佷箙璁板繂淇濇寔杞婚噺銆佹寜 topic 绠＄悊銆? in decisions_path.read_text(encoding="utf-8")


# 楠岃瘉 explicit memory promotion rejects secret shaped and transient lines 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_explicit_memory_promotion_rejects_secret_shaped_and_transient_lines(tmp_path):
    """闀挎湡璁板繂涓嶈兘淇濆瓨瀵嗛挜褰㈢姸鏂囨湰銆佷复鏃朵换鍔＄姸鎬佹垨鍣０杈撳嚭銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.\n"
            "Dependency: API key is sk-live-secret-abc.\n"
            "Decision: Current goal is fix flaky tests.\n"
            "Dependency: stdout: FAIL test_one FAIL test_two FAIL test_three.</final>",
        ],
    )

    agent.ask("Capture these stable facts into durable memory.")

    report = json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))
    conventions_path = tmp_path / ".coda" / "memory" / "topics" / "project-conventions.md"
    dependency_path = tmp_path / ".coda" / "memory" / "topics" / "dependency-facts.md"

    # 鍙湁绋冲畾椤圭洰绾﹀畾琚彁鍗囷紱secret/current goal/stdout 杩欑被鍐呭琚嫆缁濄€?
    assert report["durable_promotions"] == [
        "project-conventions: Use constrained tools instead of guessing.",
    ]
    assert report["durable_rejections"] == [
        "dependency-facts:secret_shaped",
        "key-decisions:transient_task_state",
        "dependency-facts:noisy_output",
    ]
    assert "Use constrained tools instead of guessing." in conventions_path.read_text(encoding="utf-8")
    assert not dependency_path.exists()


# 楠岃瘉 explicit memory promotion supersedes matching durable fact 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_explicit_memory_promotion_supersedes_matching_durable_fact(tmp_path):
    """鍚屼竴涓婚浜嬪疄鏇存柊鏃讹紝鏂扮殑闀挎湡璁板繂浼氭浛鎹㈡棫浜嬪疄銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>Dependency: Python runtime is 3.11.</final>",
            "<final>Dependency: Python runtime is 3.12.</final>",
        ],
    )

    assert agent.ask("Capture this stable dependency fact into durable memory.") == "Dependency: Python runtime is 3.11."
    assert agent.ask("Save the updated dependency fact into durable memory.") == "Dependency: Python runtime is 3.12."

    dependency_path = tmp_path / ".coda" / "memory" / "topics" / "dependency-facts.md"
    report = json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))
    text = dependency_path.read_text(encoding="utf-8")

    assert "Python runtime is 3.12." in text
    assert "Python runtime is 3.11." not in text
    assert report["durable_superseded"] == [
        "dependency-facts: Python runtime is 3.11. -> Python runtime is 3.12.",
    ]


# 楠岃瘉 explicit memory promotion dedupes duplicate durable note 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_explicit_memory_promotion_dedupes_duplicate_durable_note(tmp_path):
    """閲嶅淇濆瓨鍚屼竴鏉￠暱鏈熻蹇嗘椂锛屼笉搴旇鍐欏嚭閲嶅 note銆?""
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.</final>",
            "<final>Project convention: Use constrained tools instead of guessing.</final>",
        ],
    )

    agent.ask("Capture the stable fact into durable memory.")
    agent.ask("Capture the stable fact into durable memory again.")

    conventions_path = tmp_path / ".coda" / "memory" / "topics" / "project-conventions.md"
    text = conventions_path.read_text(encoding="utf-8")

    assert text.count("Use constrained tools instead of guessing.") == 1


# 楠岃瘉 agent records model cache metadata in last prompt metadata 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_agent_records_model_cache_metadata_in_last_prompt_metadata(tmp_path):
    """妯″瀷 client 杩斿洖鐨?cache usage 浼氬悎骞惰繘 agent.last_prompt_metadata銆?""
    class CacheAwareFakeModelClient(FakeModelClient):
        # 鎵ц complete 瀵瑰簲鐨勪富娴佺▼锛屽苟鎶婄粨鏋滆繑鍥炵粰璋冪敤鏂广€?
        def complete(self, prompt, max_new_tokens, **kwargs):
            # 杩欓噷妯℃嫙鐪熷疄鍚庣杩斿洖鐨?usage/cache 鍏冩暟鎹€?
            self.last_completion_metadata = {
                "prompt_cache_supported": True,
                "cached_tokens": 512,
                "cache_hit": True,
                "input_tokens": 1024,
            }
            return super().complete(prompt, max_new_tokens, **kwargs)

    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".coda" / "sessions")
    agent = MiniAgent(
        model_client=CacheAwareFakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )

    assert agent.ask("Cache aware run") == "Done."

    assert agent.last_prompt_metadata["prompt_cache_supported"] is True
    assert agent.last_prompt_metadata["cached_tokens"] == 512
    assert agent.last_prompt_metadata["cache_hit"] is True
    assert agent.last_prompt_metadata["prefix_hash"]
    assert agent.last_prompt_metadata["prompt_cache_key"] == agent.last_prompt_metadata["prefix_hash"]


# 楠岃瘉 recent transcript entries stay richer than older ones 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_recent_transcript_entries_stay_richer_than_older_ones(tmp_path):
    """涓婁笅鏂囩槮韬椂锛屾渶杩?history 搴旇姣旀棫 history 淇濈暀鏇村缁嗚妭銆?""
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    old_text = "OLD-" + ("A" * 320)
    recent_text = "RECENT-" + ("B" * 320)

    agent.record({"role": "user", "content": old_text, "created_at": "2026-04-07T09:00:00+00:00"})
    agent.record({"role": "assistant", "content": old_text, "created_at": "2026-04-07T09:01:00+00:00"})
    agent.record({"role": "user", "content": recent_text, "created_at": "2026-04-07T09:02:00+00:00"})
    agent.record({"role": "assistant", "content": recent_text, "created_at": "2026-04-07T09:03:00+00:00"})
    agent.record({"role": "user", "content": recent_text, "created_at": "2026-04-07T09:04:00+00:00"})
    agent.record({"role": "assistant", "content": recent_text, "created_at": "2026-04-07T09:05:00+00:00"})
    agent.record({"role": "user", "content": recent_text, "created_at": "2026-04-07T09:06:00+00:00"})
    agent.record({"role": "assistant", "content": recent_text, "created_at": "2026-04-07T09:07:00+00:00"})

    assert agent.ask("Check the transcript") == "Done."

    prompt = agent.model_client.prompts[-1]

    # recent_text 灞炰簬鏈€杩戠獥鍙ｏ紝搴斾繚鐣欙紱old_text 灞炰簬鏇存棫鍘嗗彶锛屽簲琚帇缂╂帀銆?
    assert recent_text in prompt
    assert old_text not in prompt


# ---------------------------------------------------------------------------
# 5. 鍖呭鍑恒€佹枃妗ｉ鏋跺拰妯″潡鍏ュ彛锛氫繚璇佸畨瑁呭悗鐢ㄦ埛鑳戒粠鍏紑 API/CLI 璁块棶
# 楠岃瘉 public api exports resolve through package path 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?


def test_public_api_exports_resolve_through_package_path():
    """coda.__init__ 搴旇瀵煎嚭甯哥敤绫诲拰鍏ュ彛锛屾柟渚垮閮ㄧ洿鎺?import銆?""
    assert callable(build_welcome)
    assert FakeModelClient is not None
    assert MiniAgent is not None
    assert OllamaModelClient is not None
    assert SessionStore is not None
    assert WorkspaceContext is not None
    assert Path(mini_pkg.__file__).as_posix().endswith("/coda/__init__.py")


# 楠岃瘉 reviewer skeleton docs exist 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_reviewer_skeleton_docs_exist():
    """璇勫/鏋舵瀯鏂囨。楠ㄦ灦搴斿瓨鍦ㄣ€?

    褰撳墠浠撳簱濡傛灉缂?docs/ 鐩綍锛岃繖涓祴璇曚細澶辫触锛涘畠鏄湪鎻愰啋鍙戝竷鍓嶈ˉ榻愭枃妗ｃ€?
    """
    review_pack = Path("docs/review-pack/README.md")
    architecture = Path("docs/architecture/agent-harness-v1-overview.md")

    assert review_pack.exists()
    assert architecture.exists()

    review_text = review_pack.read_text(encoding="utf-8")
    assert "Project pitch" in review_text
    assert "Architecture map" in review_text
    assert "Benchmark evidence" in review_text
    assert "Sample run artifact list" in review_text

    architecture_text = architecture.read_text(encoding="utf-8")
    assert "Agent Harness v1" in architecture_text
    assert "task state" in architecture_text.lower()


# 楠岃瘉 package import surface includes cli entrypoints 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_package_import_surface_includes_cli_entrypoints():
    """鍖呯骇 API 搴旇鏆撮湶 CLI 鍏ュ彛鐩稿叧鍑芥暟銆?""
    assert callable(mini_pkg.main)
    assert callable(mini_pkg.build_agent)
    assert callable(mini_pkg.build_arg_parser)


# 楠岃瘉 module execution help works 鍦烘櫙锛岀‘淇濆搴斿姛鑳芥寜棰勬湡宸ヤ綔銆?
def test_module_execution_help_works():
    """python -m coda --help 搴旇鑳芥甯告樉绀哄府鍔╀俊鎭€?""
    result = subprocess.run(
        [sys.executable, "-m", "coda", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()

