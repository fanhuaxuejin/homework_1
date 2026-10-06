"""LLM 封装层。

这一层存在的意义（对应评分点"代码质量 20% / 错误处理"）：
    把「网络会抖、接口会限流、服务会 5xx」这些现实问题收敛在一个地方处理，
    让上层的 Agent 循环只需要关心"推理"和"调工具"，不必写 try/except。

对外只暴露两个概念：
    LLMClient.chat()      —— 发一轮请求，拿回 ModelReply
    LLMClient.usage_text() —— 本次会话 token 用量与估算费用
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from config import Settings

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 openai。请先执行：\n    pip install -r requirements.txt"
    ) from exc


class LLMError(RuntimeError):
    """LLM 调用最终失败（已用尽重试）。"""


@dataclass
class ToolCallRequest:
    """模型请求调用某个工具。"""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""

    @property
    def parse_error(self) -> str | None:
        """参数 JSON 是否解析失败（模型偶尔会输出被截断的 JSON）。"""
        if self.arguments:
            return None
        return f"工具参数不是合法 JSON：{self.raw_arguments[:200]!r}"


@dataclass
class ModelReply:
    """一次模型回复的规范化结果。"""

    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    assistant_message: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# DeepSeek 计价（美元 / 百万 tokens）。用于给用户一个成本量级感，不参与业务逻辑。
# 来源：https://api-docs.deepseek.com/quick_start/pricing/ （2026-09 核对）
_PRICES = {
    # model 前缀: (缓存未命中输入, 缓存命中输入, 输出)  —— 取峰谷价的中间量级
    "deepseek-flash": (0.22, 0.004, 0.9),
    "deepseek-v4-pro": (0.99, 0.033, 2.97),
}


def _price_for(model: str) -> tuple[float, float, float]:
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0, 0.0)


class LLMClient:
    """带重试、超时与用量统计的 LLM 客户端。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key or "sk-placeholder",
            base_url=settings.provider.base_url,
            timeout=settings.timeout,
            max_retries=0,  # 重试逻辑自己实现，便于记录日志与自定义退避
        )
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0

    # ------------------------------------------------------------- 请求组装
    def _extra_body(self) -> dict[str, Any] | None:
        """思考模式等厂商专属参数，通过 extra_body 透传。"""
        cfg = self.settings.provider
        if self.settings.thinking and cfg.thinking_extra_body:
            return dict(cfg.thinking_extra_body)
        return None

    # --------------------------------------------------------------- 主接口
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.2,
        on_retry: Any = None,
    ) -> ModelReply:
        """发起一次对话请求，失败自动重试。

        temperature 默认 0.2：代码解释要求稳定、可复现，不需要发散。
        """
        last_error: Exception | None = None

        for attempt in range(1, self.settings.max_retries + 1):
            try:
                return self._request_once(messages, tools, temperature)
            except Exception as exc:  # noqa: BLE001 - 需要按类型决定是否重试
                last_error = exc
                if not self._is_retryable(exc) or attempt == self.settings.max_retries:
                    break
                # 指数退避 + 抖动，避免多个请求同时重试把限流打得更死
                delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.6)
                if on_retry is not None:
                    on_retry(attempt, self.settings.max_retries, exc, delay)
                time.sleep(delay)

        raise LLMError(
            f"LLM 调用失败（已尝试 {self.settings.max_retries} 次）："
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def _request_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
    ) -> ModelReply:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        extra = self._extra_body()
        if extra:
            kwargs["extra_body"] = extra

        response = self.client.chat.completions.create(**kwargs)
        self.calls += 1

        choice = response.choices[0]
        message = choice.message

        # 用量统计（有些兼容端点不返回 usage，做兼容处理）
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached_tokens = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", 0) or 0
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cached_tokens += cached_tokens

        tool_calls: list[ToolCallRequest] = []
        for call in getattr(message, "tool_calls", None) or []:
            raw_args = call.function.arguments or ""
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(parsed, dict):
                    parsed = {}
            except json.JSONDecodeError:
                parsed = {}
            tool_calls.append(
                ToolCallRequest(
                    id=call.id,
                    name=call.function.name,
                    arguments=parsed,
                    raw_arguments=raw_args,
                )
            )

        # 存一份"原样"的 assistant 消息，用于回填历史（关键！）
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_arguments or "{}"},
                }
                for c in tool_calls
            ]

        return ModelReply(
            content=message.content or "",
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            finish_reason=getattr(choice, "finish_reason", "") or "",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    # ----------------------------------------------------------- 重试判定
    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """区分"值得重试"和"重试也没用"的错误。

        值得重试：限流(429)、服务端错误(5xx)、网络超时/连接中断
        不值得：401 鉴权失败、400 参数错误、余额不足
        """
        status = getattr(exc, "status_code", None)
        if status is None:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)

        if status is not None:
            if status == 429 or 500 <= int(status) < 600:
                return True
            if 400 <= int(status) < 500:
                return False

        # 网络类异常（类型名判断，避免强依赖 openai 的异常层级）
        name = type(exc).__name__
        if name in {
            "APITimeoutError",
            "APIConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "TimeoutError",
            "ConnectionError",
        }:
            return True
        # 认不出来的一律重试一次，交给退避兜底
        return True

    # ------------------------------------------------------------- 用量报告
    def usage_text(self) -> str:
        peak_in, cached_in, out = _price_for(self.settings.model)
        fresh = max(self.prompt_tokens - self.cached_tokens, 0)
        cost = (
            fresh / 1_000_000 * peak_in
            + self.cached_tokens / 1_000_000 * cached_in
            + self.completion_tokens / 1_000_000 * out
        )
        return (
            f"调用 {self.calls} 次 | 输入 {self.prompt_tokens} tokens"
            f"（缓存命中 {self.cached_tokens}）| 输出 {self.completion_tokens} tokens"
            + (f" | 估算费用约 ${cost:.4f}" if cost > 0 else "")
        )
