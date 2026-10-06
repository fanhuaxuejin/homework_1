"""最小连通性探针：只做最简单的 API 调用，用来把"接口层问题"和"Agent 逻辑问题"分开。

排查顺序很重要：如果直接跑 Agent 报错，你无法判断是 Key、模型名、参数格式
还是 Agent 循环写错了。先用本脚本确认最底层能通，再往上排查。

运行：
    python tests/probe_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings  # noqa: E402

try:
    from openai import OpenAI
except ImportError:
    print("缺少 openai 依赖，请先 pip install -r requirements.txt")
    raise SystemExit(2)

settings = load_settings()
print("配置：", f"provider={settings.provider.name} model={settings.model}")
print("base_url：", settings.provider.base_url)
print("Key 长度：", len(settings.api_key), "| 首 4 位：", settings.api_key[:4])
print()

client = OpenAI(
    api_key=settings.api_key,
    base_url=settings.provider.base_url,
    timeout=60,
    max_retries=0,
)

# ---------------------------------------------------------------- 测试 1
print("[测试 1] 最简对话（不带 tools、不带思考模式参数）")
try:
    resp = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "只回复两个字：收到"}],
        max_tokens=50,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    print("  ✓ 成功")
    print("  返回内容：", repr(resp.choices[0].message.content))
    print("  finish_reason：", resp.choices[0].finish_reason)
    usage = getattr(resp, "usage", None)
    if usage:
        print(f"  usage：prompt={usage.prompt_tokens} completion={usage.completion_tokens}")
except Exception as exc:  # noqa: BLE001
    print("  ✗ 失败：", type(exc).__name__, exc)
    print("\n  → 若为 401/403：Key 无效或未启用。若为 404：模型名不对。")
    raise SystemExit(1)

# ---------------------------------------------------------------- 测试 2
print()
print("[测试 2] 带 tools 的工具调用（Agent 循环的前提能力）")
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某个城市的天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    }
]
try:
    resp2 = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "杭州现在天气怎么样？"}],
        tools=tools,
        tool_choice="auto",
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    msg = resp2.choices[0].message
    calls = getattr(msg, "tool_calls", None) or []
    if calls:
        print("  ✓ 模型正确返回了工具调用")
        print("    工具名：", calls[0].function.name)
        print("    参数：", calls[0].function.arguments)
    else:
        print("  ⚠ 模型未返回工具调用，而是直接回答：", repr(msg.content)[:200])
        print("    （不一定是错误，但说明该模型的工具调用倾向较弱）")
except Exception as exc:  # noqa: BLE001
    print("  ✗ 失败：", type(exc).__name__, exc)
    raise SystemExit(1)

# ---------------------------------------------------------------- 测试 3
print()
print("[测试 3] 思考模式参数（本项目 .env 中 LLM_THINKING=1 会发送它）")
try:
    resp3 = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "1+1=?只回复数字"}],
        max_tokens=200,
        temperature=0,
        extra_body={"thinking": {"type": "enabled"}},
    )
    print("  ✓ 服务端接受了 thinking 参数")
    print("  返回内容：", repr(resp3.choices[0].message.content)[:200])
except Exception as exc:  # noqa: BLE001
    print("  ✗ 服务端拒绝 thinking 参数：", type(exc).__name__, exc)
    print("  → 说明该模型/账号不支持此参数格式。")
    print("     解决办法：在 .env 中设置 LLM_THINKING=0，本项目会自动不发送该参数。")
    raise SystemExit(1)

print()
print("=" * 60)
print("三项测试全部通过：Key、模型名、工具调用、思考模式均正常。")
print("可以运行： python main.py \"解释 demo/sample_buggy.py\"")
print("=" * 60)
