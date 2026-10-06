"""上下文记忆层（对应作业要求"支持上下文记忆"）。

两级记忆设计：
    1. 短期记忆：self.messages —— 当前会话的完整消息序列，供模型推理使用。
       但消息不能无限增长，否则会超出上下文窗口并让成本线性上升，
       所以用 `trim()` 做"保系统提示 + 保最近对话 + 丢弃最旧轮次"的滑窗裁剪。
    2. 长期记忆：可选地把会话落盘到 .agent_memory/*.json，
       使 Agent 重启后仍能载入历史（也便于写报告时复盘工具调用轨迹）。

token 估算不引入 tiktoken 之类的重依赖，用"字符数 / 经验系数"近似即可，
目的是做量级控制，不是精确计费。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 经验系数：中英混排文本大约每 2 个字符 1 个 token（中文约 1.5 字/token，
# 英文约 4 字符/token，代码通常是英文标识符+符号，取折中值 2.0）
CHARS_PER_TOKEN = 2.0


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算一段消息列表占用的 token 数（含 tool_calls 参数）。"""
    total = 0
    for msg in messages:
        total += 4  # 每条消息的角色/分隔符开销
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):  # 多模态内容块
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", "")))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            total += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return int(total / CHARS_PER_TOKEN) + 1


class ConversationMemory:
    """会话记忆：存消息、裁剪历史、统计用量、可选落盘。"""

    def __init__(
        self,
        system_prompt: str,
        *,
        max_context_tokens: int = 120_000,
        keep_recent_messages: int = 24,
        store_dir: Path | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_context_tokens = max_context_tokens
        self.keep_recent_messages = keep_recent_messages
        self.store_dir = store_dir
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.trim_events = 0

    # ------------------------------------------------------------ 基本操作
    @property
    def turns(self) -> int:
        """用户提问轮数。"""
        return sum(1 for m in self.messages if m.get("role") == "user")

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add(self, message: dict[str, Any]) -> None:
        """追加一条原始消息（assistant 或 tool）。"""
        self.messages.append(message)

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )

    def clear(self, keep_system: bool = True) -> None:
        """清空会话（保留 system prompt），对应 CLI 的 /clear。"""
        self.messages = [m for m in self.messages if m.get("role") == "system"] if keep_system else []
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]

    def context_tokens(self) -> int:
        return estimate_tokens(self.messages)

    # -------------------------------------------------------------- 裁剪
    def trim(self) -> bool:
        """超出预算时裁剪历史。返回是否发生了裁剪。

        裁剪策略（保守，优先保证不破坏 tool_calls 与 tool 结果的配对关系）：
            - 永远保留 system prompt（第 0 条）
            - 永远保留最近 keep_recent_messages 条
            - 从最旧的可裁剪位置开始，成组丢弃（一个 user 轮次 + 其后的
              assistant/tool 消息一起丢），避免出现"孤儿 tool 消息"导致接口 400
        """
        if self.context_tokens() <= self.max_context_tokens:
            return False

        head = self.messages[:1]                      # system prompt
        body = self.messages[1:]
        if len(body) <= self.keep_recent_messages:
            # 已经只剩余最近消息却还超预算：说明单轮内容过长，
            # 此时由工具层负责截断文件内容，这里不再强行删
            return False

        # 从头部找裁剪边界：边界必须落在 user 消息上
        cut = len(body) - self.keep_recent_messages
        while cut < len(body) and body[cut].get("role") != "user":
            cut += 1
        if cut >= len(body):
            cut = len(body) - self.keep_recent_messages

        dropped = body[:cut]
        self.messages = head + body[cut:]
        self.trim_events += 1
        self.messages.insert(
            1,
            {
                "role": "system",
                "content": (
                    f"[上下文提示] 为控制长度，已省略本会话最早的 {len(dropped)} 条消息。"
                    "如需要其中的内容，请重新调用工具读取文件。"
                ),
            },
        )
        return True

    # -------------------------------------------------------------- 摘要
    def summary(self) -> str:
        return (
            f"会话轮数 {self.turns} | 消息数 {len(self.messages)} | "
            f"估算上下文 {self.context_tokens()} tokens"
            + (f" | 已裁剪 {self.trim_events} 次" if self.trim_events else "")
        )

    # ------------------------------------------------------------ 持久化
    def save(self, name: str = "last_session") -> Path | None:
        """把会话落盘（长期记忆 / 复盘素材）。"""
        if self.store_dir is None:
            return None
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path = self.store_dir / f"{name}.json"
        payload = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "turns": self.turns,
            "estimated_tokens": self.context_tokens(),
            "messages": self.messages,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def load(self, name: str = "last_session") -> bool:
        """载入上次会话（不含 system prompt，用当前最新的提示词）。"""
        if self.store_dir is None:
            return False
        path = self.store_dir / f"{name}.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        restored = [
            m for m in payload.get("messages", []) if m.get("role") != "system"
        ]
        if not restored:
            return False
        self.messages = [{"role": "system", "content": self.system_prompt}] + restored
        return True
