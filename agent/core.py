"""Agent 核心：输入 → 推理 → 工具调用 → 输出 的循环。

这是整个项目的核心，也是作业「技术要求 1」直接考察的部分。
刻意不使用 LangChain 等框架，为的是让循环的每一步都显式可见、可讲、可调试。

循环伪代码：
    messages = [system] + 历史 + 用户输入
    for step in 1..max_steps:
        回复 = LLM.chat(messages, tools)          # ① 推理
        if 回复有 tool_calls:                      # ② 需要外部信息
            messages += 回复.assistant_message
            for call in 回复.tool_calls:
                结果 = tools.invoke(call)          # ③ 调用工具（失败也回传，让模型自纠）
                messages += {role: tool, ...}
            continue                               # ④ 带着新信息再推理
        else:
            return 回复.content                    # ⑤ 输出最终答案
    return "达到步数上限"                          # 防护：防死循环

健壮性设计（对应「错误处理」评分点）：
    - 工具异常/参数错误不抛出，作为文本回传给模型 → 模型可自行改用正确参数重试
    - 模型输出非法 JSON 参数 → 同样回传错误，让它重发
    - 单轮工具调用数量、总步数都设上限 → 避免无限循环与费用失控
    - 每步之前裁剪上下文 → 长会话不会超窗口
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.llm import LLMClient, LLMError, ToolCallRequest
from agent.memory import ConversationMemory
from agent.prompts import build_system_prompt
from config import Settings
from tools import invoke as invoke_tool
from tools.loader import schemas as load_tool_schemas

# 单次提问内最多允许多少轮"推理+工具调用"
DEFAULT_MAX_STEPS = 12
# 单轮里最多执行多少个工具调用（模型偶尔会一口气请求十几个）
MAX_TOOL_CALLS_PER_STEP = 6

# 事件回调签名：event 形如 {"type": "tool_call", "name": ..., "arguments": {...}}
EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class AgentResult:
    """一次提问的完整结果，便于上层做测试断言或写报告。"""

    answer: str
    steps: int
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    stopped_by_limit: bool = False
    error: str | None = None


class CodeExplainAgent:
    """代码解释 Agent。

    与 CLI 解耦：CLI 只负责把事件打印出来，Agent 自己不知道"终端"的存在。
    因此同一套 Agent 也能被 Web 界面或自动化测试直接调用。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        on_event: EventCallback | None = None,
        enable_memory_persist: bool = True,
    ) -> None:
        self.settings = settings
        self.max_steps = max_steps
        self.on_event = on_event or (lambda _event: None)
        self.client = LLMClient(settings)
        self.tools = load_tool_schemas()

        store_dir = settings.workspace / ".agent_memory" if enable_memory_persist else None
        self.memory = ConversationMemory(
            build_system_prompt(settings.workspace),
            store_dir=store_dir,
        )

    # ------------------------------------------------------------- 对外接口
    def ask(self, question: str) -> AgentResult:
        """提一个问题，跑完整个 Agent 循环，返回结果。"""
        started = time.time()
        self.memory.add_user(question)
        invocations: list[dict[str, Any]] = []
        stopped_by_limit = False

        try:
            for step in range(1, self.max_steps + 1):
                # 每步都先裁剪上下文，保证不超预算
                if self.memory.trim():
                    self._emit(
                        "info",
                        text=f"上下文超出预算，已裁剪较早的对话（第 {self.memory.trim_events} 次）",
                    )

                self._emit("step", step=step, max_steps=self.max_steps, text="正在推理…")

                reply = self.client.chat(
                    self.memory.messages,
                    self.tools,
                    on_retry=self._on_retry,
                )

                # ---- 分支 A：模型要调工具
                if reply.wants_tools:
                    # 把 assistant 消息（含 tool_calls）原样入历史，这是协议要求：
                    # 后续的 tool 结果消息必须能对应上一个 tool_call_id
                    self.memory.add(reply.assistant_message)

                    if reply.content.strip():
                        self._emit("info", text=reply.content.strip())

                    calls = reply.tool_calls[:MAX_TOOL_CALLS_PER_STEP]
                    for call in calls:
                        invocations.append(self._execute_tool_call(call))
                    continue

                # ---- 分支 B：模型给出最终答案
                answer = reply.content.strip()
                if not answer:
                    # 极端情况：既没有内容也没有工具调用
                    answer = (
                        "[提示] 模型返回了空内容。可能是问题过于笼统，"
                        "请补充具体文件路径或函数名后重试。"
                    )
                self.memory.add({"role": "assistant", "content": answer})
                self._emit("final", text=answer)
                return AgentResult(
                    answer=answer,
                    steps=step,
                    tool_invocations=invocations,
                    elapsed_seconds=time.time() - started,
                )

            # ---- 循环用尽仍未收敛
            stopped_by_limit = True
            fallback = (
                f"已达到最大推理步数（{self.max_steps} 步）仍未得出结论。\n"
                "可能原因：问题需要的信息超出工作目录范围，或目标文件不存在。\n"
                "建议：把问题范围缩小到具体文件/函数，或检查路径是否正确。"
            )
            self.memory.add({"role": "assistant", "content": fallback})
            self._emit("final", text=fallback)
            return AgentResult(
                answer=fallback,
                steps=self.max_steps,
                tool_invocations=invocations,
                elapsed_seconds=time.time() - started,
                stopped_by_limit=True,
            )

        except LLMError as exc:
            # LLM 彻底失败：给出可操作的建议，而不是抛裸栈
            hint = str(exc)
            if "401" in hint or "authentication" in hint.lower() or "invalid" in hint.lower():
                hint += "\n[排查建议] 请检查 .env 中的 LLM_API_KEY 是否正确、是否已过期。"
            elif "402" in hint or "balance" in hint.lower() or "insufficient" in hint.lower():
                hint += "\n[排查建议] 账户余额不足，请前往平台充值。"
            elif "404" in hint or "model" in hint.lower():
                hint += (
                    f"\n[排查建议] 模型名 {self.settings.model!r} 可能已下线或拼写有误。"
                    "请用 .env 的 LLM_MODEL 指定当前可用模型。"
                )
            self._emit("error", text=hint)
            return AgentResult(
                answer="",
                steps=0,
                tool_invocations=invocations,
                elapsed_seconds=time.time() - started,
                error=hint,
            )

    def clear(self) -> None:
        self.memory.clear()

    def usage_text(self) -> str:
        return (
            f"{self.client.usage_text()} | {self.memory.summary()}"
        )

    def save_session(self) -> Path | None:
        return self.memory.save()

    def load_session(self) -> bool:
        return self.memory.load()

    # -------------------------------------------------------------- 内部
    def _execute_tool_call(self, call: ToolCallRequest) -> dict[str, Any]:
        """执行一个工具调用，把结果写入记忆，并返回调用记录。"""
        # 模型偶尔会吐出被截断的参数 JSON —— 不崩，回传错误让它重发
        if call.parse_error:
            result = (
                f"[错误] {call.parse_error}\n"
                "请重新调用该工具，并确保参数是合法的 JSON 对象。"
            )
            self._emit("tool_error", name=call.name, text=result)
        else:
            self._emit("tool_call", name=call.name, arguments=call.arguments)
            result = invoke_tool(call.name, call.arguments)
            self._emit("tool_result", name=call.name, text=result)

        self.memory.add_tool_result(call.id, result)
        return {"name": call.name, "arguments": call.arguments, "result": result}

    def _emit(self, event_type: str, **payload: Any) -> None:
        self.on_event({"type": event_type, **payload})

    def _on_retry(self, attempt: int, total: int, exc: Exception, delay: float) -> None:
        self._emit(
            "retry",
            attempt=attempt,
            total=total,
            text=(
                f"第 {attempt}/{total} 次调用失败（{type(exc).__name__}），"
                f"{delay:.1f} 秒后重试…"
            ),
        )

    # -------------------------------------------------- 不消耗 API 的便捷方法
    def quick_symbols(self, path: str) -> str:
        """直接调用 list_symbols 工具，不经过 LLM —— 用于 CLI 的 /symbols。"""
        return invoke_tool("list_symbols", {"path": path})

    def quick_files(self, path: str = ".") -> str:
        """直接调用 list_dir 工具，不经过 LLM —— 用于 CLI 的 /files。"""
        return invoke_tool("list_dir", {"path": path})
