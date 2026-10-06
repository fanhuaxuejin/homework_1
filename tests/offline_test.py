"""离线冒烟测试：在【不联网、不装 openai、不消耗 API 额度】的前提下，
验证除"真正调用大模型"之外的所有逻辑。

为什么需要它：
    工具层（文件读取、编码容错、符号解析、检索、执行隔离、路径安全）
    和记忆层（裁剪、落盘）都不依赖大模型，它们的正确性应当能被独立验证。
    这也是分层设计带来的实际好处。

运行：
    python tests/offline_test.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# ---------------------------------------------------------------- 依赖打桩
# agent/llm.py 需要 openai 包。为了让本脚本在没装依赖的机器上也能跑，
# 先注入一个最小的占位模块。注意：这不会让真实 API 调用变得可用。
if "openai" not in sys.modules:
    stub = types.ModuleType("openai")

    class _OpenAI:  # noqa: D401 - 占位类
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("离线测试不应发起真实 API 调用")

    stub.OpenAI = _OpenAI  # type: ignore[attr-defined]
    sys.modules["openai"] = stub

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.memory import ConversationMemory, estimate_tokens  # noqa: E402
from config import load_settings  # noqa: E402
from tools import UnsafePathError, invoke, resolve_workspace_path  # noqa: E402
from tools.loader import schemas  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name}  -> {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# -------------------------------------------------------------- 环境准备
settings = load_settings(workspace=PROJECT_ROOT)
DEMO = "demo"


# ------------------------------------------------------------------ 1. 工具装载
section("1. 工具装载与 schema 合法性")
schema_list = schemas()
names = sorted(s["function"]["name"] for s in schema_list)
expected = sorted(
    ["list_dir", "list_symbols", "read_code", "run_python", "search_code", "write_report"]
)
check("装载 6 个工具", len(schema_list) == 6, f"实际 {len(schema_list)}: {names}")
check("工具名完全匹配", names == expected, f"{names}")

for s in schema_list:
    fn = s["function"]
    ok = (
        s.get("type") == "function"
        and bool(fn.get("name"))
        and bool(fn.get("description"))
        and fn.get("parameters", {}).get("type") == "object"
        and "properties" in fn.get("parameters", {})
    )
    check(f"schema 结构合法: {fn.get('name')}", ok, str(fn)[:120])


# -------------------------------------------------------------- 2. 路径安全边界
section("2. 路径安全边界（防止模型构造越权路径）")
attacks = [
    "../../../../Windows/System32/drivers/etc/hosts",
    "demo/../../../../etc/passwd",
    "..\\..\\..\\Windows\\win.ini",
]
for attack in attacks:
    try:
        resolved = resolve_workspace_path(attack, settings.workspace)
        # 允许解析成功但必须仍在工作区内；否则视为漏洞
        inside = settings.workspace in resolved.parents or resolved == settings.workspace
        check(f"拒绝越权路径: {attack[:40]}", not inside, f"逃逸到 {resolved}")
    except UnsafePathError:
        check(f"拒绝越权路径: {attack[:40]}", True)

try:
    resolve_workspace_path("demo/utils.py", settings.workspace)
    check("允许工作区内合法路径", True)
except UnsafePathError as exc:
    check("允许工作区内合法路径", False, str(exc))

# 通过工具入口实际调用，确认错误被转成文本而不是抛异常
out = invoke("read_code", {"path": "../../../../Windows/win.ini"})
check("工具入口拦截越权并返回文本", isinstance(out, str) and "拒绝访问" in out, out[:120])


# ------------------------------------------------------------------ 3. read_code
section("3. read_code：读取、行号、分段、编码容错")
out = invoke("read_code", {"path": f"{DEMO}/sample_good.py"})
check("读取正常文件成功", "文件：" in out and "L" not in out.split("\n")[0][:0] and "总行数" in out, out[:100])
check("输出带行号", "  1 |" in out or " 1 |" in out, out[:200])

out_seg = invoke("read_code", {"path": f"{DEMO}/sample_good.py", "start_line": 10, "end_line": 14})
check("分段读取生效", "本次显示：10-14" in out_seg, out_seg[:200])

out_bad_line = invoke("read_code", {"path": f"{DEMO}/sample_good.py", "start_line": 99999})
check("超界行号给出明确错误", "[错误]" in out_bad_line and "超出" in out_bad_line, out_bad_line[:120])

out_neg = invoke("read_code", {"path": f"{DEMO}/sample_good.py", "start_line": -5, "end_line": 3})
check("负数行号被夹到合法区间", "本次显示：1-3" in out_neg, out_neg[:120])

out_dir = invoke("read_code", {"path": DEMO})
check("传目录时提示改用 list_dir", "[错误]" in out_dir and "目录" in out_dir, out_dir[:120])

out_missing = invoke("read_code", {"path": "demo/不存在的文件.py"})
check("文件不存在返回明确错误", "[错误]" in out_missing and "不存在" in out_missing, out_missing[:120])

# 编码容错：GBK 文件含中文注释
gbk_files = list((PROJECT_ROOT / DEMO).glob("*中文*"))
if gbk_files:
    out_gbk = invoke("read_code", {"path": f"{DEMO}/{gbk_files[0].name}"})
    check(
        "GBK 文件解码成功（编码容错）",
        "[错误]" not in out_gbk and "编码：gbk" in out_gbk,
        out_gbk[:200],
    )
    check("GBK 中文内容正确还原", "容量受限的 FIFO 缓存" in out_gbk, out_gbk[:300])
else:
    check("GBK 测试文件存在", False, "未找到 demo/*中文* 文件")

# 不支持的类型
unsupported = PROJECT_ROOT / DEMO / "_tmp.bin"
unsupported.write_bytes(b"\x00\x01\x02")
try:
    out_bin = invoke("read_code", {"path": f"{DEMO}/_tmp.bin"})
    check("不支持的文件类型被拒绝", "[错误]" in out_bin and "不支持" in out_bin, out_bin[:120])
finally:
    unsupported.unlink(missing_ok=True)


# -------------------------------------------------------------- 4. list_symbols
section("4. list_symbols：Python ast 精确解析 / 非 Python 正则分支")
out = invoke("list_symbols", {"path": f"{DEMO}/utils.py"})
check("Python 走 ast 分支", "解析方式：Python ast" in out, out[:150])
check("提取到类/函数", "def clean_text" in out and "def parse_age" in out, out[:400])
check("输出含行号区间", "L11-" in out and "L18-" in out, out[:300])
check("还原了函数签名", "-> str" in out or "-> int" in out, out[:300])
check("附带了 docstring 摘要", "#" in out, out[:300])

out_cls = invoke("list_symbols", {"path": f"{DEMO}/sample_good.py"})
check("识别 class 与 dataclass 字段", "class Order" in out_cls, out_cls[:400])

out_js = invoke("list_symbols", {"path": f"{DEMO}/sample_frontend.js"})
check("JS 走正则分支", "正则粗提取" in out_js, out_js[:150])
check("JS 函数被提取", "flattenDeep" in out_js, out_js[:400])

out_syn = invoke("list_symbols", {"path": f"{DEMO}/sample_syntax_error.py"})
check("语法错误文件不崩溃且报告行号", "语法错误" in out_syn and "22" in out_syn, out_syn[:200])

out_nofile = invoke("list_symbols", {"path": "demo/nope.py"})
check("文件不存在时明确报错", "[错误]" in out_nofile, out_nofile[:120])


# -------------------------------------------------------------- 5. search_code
section("5. search_code：跨文件检索")
out = invoke("search_code", {"pattern": "normalize_record"})
check("检索到跨文件命中", "app.py" in out and "utils.py" in out, out[:400])
check("返回文件:行号格式", ".py:" in out, out[:300])

out_glob = invoke("search_code", {"pattern": "def ", "glob": "*.js"})
check("glob 过滤生效", "app.py" not in out_glob, out_glob[:200])

out_none = invoke("search_code", {"pattern": "zzz_绝不存在的符号_zzz"})
check("无结果时给出建议", "无结果" in out_none and "提示" in out_none, out_none[:200])

out_badre = invoke("search_code", {"pattern": "["})
check("非法正则被捕获", "[错误]" in out_badre and "正则" in out_badre, out_badre[:150])


# ------------------------------------------------------------------ 6. list_dir
section("6. list_dir：目录浏览")
out = invoke("list_dir", {"path": "."})
check("列出工作目录", "agent/" in out and "tools/" in out, out[:300])
check("显示文件大小", "KB" in out or "B)" in out, out[:300])

out_file = invoke("list_dir", {"path": f"{DEMO}/utils.py"})
check("传文件时提示改用 read_code", "[错误]" in out_file and "read_code" in out_file, out_file[:150])

out_no = invoke("list_dir", {"path": "不存在的目录"})
check("目录不存在时明确报错", "[错误]" in out_no, out_no[:120])


# ------------------------------------------------------------------ 7. run_python
section("7. run_python：执行、超时、输出截断")
out = invoke("run_python", {"code": "print(sum(range(10)))"})
check("正常执行并返回 stdout", "45" in out and "退出码：0" in out, out[:200])

out_err = invoke("run_python", {"code": "raise ValueError('boom')"})
check("异常退出码非 0 且捕获 stderr", "boom" in out_err and "退出码：1" in out_err, out_err[:250])

out_timeout = invoke("run_python", {"code": "while True:\n    pass", "timeout": 2})
check("死循环被硬超时终止", "超时" in out_timeout, out_timeout[:250])

out_flood = invoke("run_python", {"code": "for i in range(200000):\n    print('x' * 50)"})
check("无限打印被截断", "截断" in out_flood, out_flood[:200])

out_empty = invoke("run_python", {"code": "   "})
check("空代码被拒绝", "[错误]" in out_empty, out_empty[:120])

# 凭据隔离：父进程设一个假 Key，子进程应看不到
import os  # noqa: E402

os.environ["MY_FAKE_API_KEY"] = "should-not-leak"
out_env = invoke("run_python", {"code": "import os; print(os.environ.get('MY_FAKE_API_KEY', 'NOT_VISIBLE'))"})
check("子进程看不到含 API_KEY 的环境变量", "NOT_VISIBLE" in out_env, out_env[:250])


# -------------------------------------------------------------- 8. write_report
section("8. write_report：报告落盘与文件名消毒")
out = invoke("write_report", {"filename": "测试报告", "content": "# 标题\n\n正文内容"})
check("报告写入成功", "已写入" in out and "reports/" in out, out[:200])
report_path = None
if "绝对路径：" in out:
    report_path = Path(out.split("绝对路径：")[1].splitlines()[0].strip())
    check("文件真实存在", report_path.exists(), str(report_path))
    if report_path.exists():
        check("内容正确", "正文内容" in report_path.read_text(encoding="utf-8"), "")

# 路径穿越尝试
out_trav = invoke("write_report", {"filename": "../../../evil", "content": "x"})
check("文件名中的 ../ 被消毒", "已写入" in out_trav and "reports/" in out_trav, out_trav[:200])
if "绝对路径：" in out_trav:
    evil = Path(out_trav.split("绝对路径：")[1].splitlines()[0].strip())
    inside = settings.workspace in evil.parents
    check("写入位置未逃出工作区", inside, str(evil))
    evil.unlink(missing_ok=True)

out_empty_rep = invoke("write_report", {"filename": "x", "content": "  "})
check("空内容被拒绝", "[错误]" in out_empty_rep, out_empty_rep[:120])

if report_path and report_path.exists():
    report_path.unlink(missing_ok=True)


# --------------------------------------------------------- 9. invoke 错误处理
section("9. 工具调度的容错（让模型能自我纠错）")
out_unknown = invoke("no_such_tool", {})
check("未知工具名返回可用列表", "[错误]" in out_unknown and "可用工具" in out_unknown, out_unknown[:200])

out_badarg = invoke("read_code", {"wrong_param": 1})
check("参数名错误提示正确参数", "[错误]" in out_badarg and "接受" in out_badarg, out_badarg[:250])

try:
    invoke("read_code", {"path": None})
    check("传 None 参数不抛异常", True)
except Exception as exc:  # noqa: BLE001
    check("传 None 参数不抛异常", False, f"抛出了 {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ 10. 记忆层
section("10. 记忆层：估算、裁剪、落盘")
msgs = [{"role": "user", "content": "a" * 1000}]
est = estimate_tokens(msgs)
check("token 估算为正数且在合理量级", 400 < est < 700, f"估算 {est}")

mem = ConversationMemory("系统提示", max_context_tokens=500, keep_recent_messages=4)
for i in range(20):
    mem.add_user(f"问题 {i} " + "填充" * 200)
    mem.add({"role": "assistant", "content": f"回答 {i} " + "填充" * 200})
trimmed = mem.trim()
check("超预算触发裁剪", trimmed and mem.trim_events == 1, f"trim_events={mem.trim_events}")
check("裁剪后仍在预算内或已无可裁", mem.context_tokens() < 1200, f"{mem.context_tokens()}")
check("system prompt 被保留", mem.messages[0]["role"] == "system" and "系统提示" in mem.messages[0]["content"], "")

# 关键：裁剪后不能出现"孤儿 tool 消息"
mem2 = ConversationMemory("sys", max_context_tokens=400, keep_recent_messages=2)
mem2.add_user("q1")
mem2.add({"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_code", "arguments": "{}"}}]})
mem2.add_tool_result("c1", "结果" * 500)
mem2.add_user("q2")
mem2.trim()
tool_msgs = [i for i, m in enumerate(mem2.messages) if m.get("role") == "tool"]
orphan = False
for i in tool_msgs:
    # 该 tool 消息之前必须能找到对应的 tool_calls
    found = False
    for m in mem2.messages[:i]:
        for call in m.get("tool_calls") or []:
            if call.get("id") == mem2.messages[i].get("tool_call_id"):
                found = True
    if not found:
        orphan = True
check("裁剪不会产生孤儿 tool 消息", not orphan, "出现了没有对应 tool_calls 的 tool 消息")

mem3 = ConversationMemory("sys", store_dir=PROJECT_ROOT / ".agent_memory")
mem3.add_user("测试落盘")
mem3.add({"role": "assistant", "content": "好的"})
path = mem3.save("offline_test")
check("会话落盘成功", path is not None and path.exists(), str(path))

mem4 = ConversationMemory("新的系统提示", store_dir=PROJECT_ROOT / ".agent_memory")
loaded = mem4.load("offline_test")
check("会话可载入", loaded and any(m.get("content") == "测试落盘" for m in mem4.messages), "")
check("载入后使用新 system prompt", mem4.messages[0]["content"] == "新的系统提示", "")
if path and path.exists():
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


# ------------------------------------------------------------------ 11. 配置层
section("11. 配置层")
check("默认 provider 为 deepseek", settings.provider.name == "deepseek", settings.provider.name)
check("默认模型为 deepseek-flash", settings.model == "deepseek-flash", settings.model)
check("base_url 正确", settings.provider.base_url == "https://api.deepseek.com", settings.provider.base_url)
check("思考模式默认开启", settings.thinking is True, str(settings.thinking))
check("工作目录解析为绝对路径", settings.workspace.is_absolute(), str(settings.workspace))
# 注意：这里不断言"Key 必须存在"或"必须不存在"，因为两种环境都合法：
#   未配置 Key 时 has_api_key 应为 False（提示用户去配）
#   已配置 Key 时 has_api_key 应为 True（且不能把占位符误判为有效）
_placeholder = "sk-" + "请替换为你的真实Key"
if settings.api_key == _placeholder:
    check("占位符 Key 不应被误判为有效", settings.has_api_key is False, "占位符被当成有效 Key")
else:
    check(
        "has_api_key 与 api_key 是否为空保持一致",
        settings.has_api_key == bool(settings.api_key),
        f"api_key 长度={len(settings.api_key)} has_api_key={settings.has_api_key}",
    )

# 未知 provider 应回退而不是崩溃
os.environ["LLM_PROVIDER"] = "nonexistent_provider"
s2 = load_settings(workspace=PROJECT_ROOT)
check("未知 provider 回退到默认值", s2.provider.name == "deepseek", s2.provider.name)
del os.environ["LLM_PROVIDER"]

# 非法数字环境变量应回退到默认
os.environ["LLM_TIMEOUT"] = "abc"
s3 = load_settings(workspace=PROJECT_ROOT)
check("非法 LLM_TIMEOUT 回退到默认 120", s3.timeout == 120, str(s3.timeout))
del os.environ["LLM_TIMEOUT"]

# LLM 层的重试分类
section("12. LLM 层：重试分类策略")
from agent.llm import LLMClient  # noqa: E402


class FakeErr(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


check("429 限流 → 重试", LLMClient._is_retryable(FakeErr(429)) is True, "")
check("500 服务端错误 → 重试", LLMClient._is_retryable(FakeErr(500)) is True, "")
check("503 服务端错误 → 重试", LLMClient._is_retryable(FakeErr(503)) is True, "")
check("401 鉴权失败 → 不重试", LLMClient._is_retryable(FakeErr(401)) is False, "")
check("400 参数错误 → 不重试", LLMClient._is_retryable(FakeErr(400)) is False, "")
check("404 模型不存在 → 不重试", LLMClient._is_retryable(FakeErr(404)) is False, "")


class TimeoutErr(Exception):
    pass


TimeoutErr.__name__ = "APITimeoutError"
check("网络超时 → 重试", LLMClient._is_retryable(TimeoutErr()) is True, "")


# ------------------------------------------------------------------ 汇总
print("\n" + "=" * 64)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n失败明细：")
    for item in FAILED:
        print(f"  · {item}")
    print("\n（注：涉及 API Key 的检查在离线环境下失败属预期）")
print("=" * 64)
sys.exit(1 if FAILED else 0)
