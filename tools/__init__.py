"""tools 包：Agent 可调用的工具集合。

每个工具都是一份"给模型看的说明书" + 一个"真正干活的函数"：
    schema  -> 提供给 LLM 的 JSON Schema（决定模型会不会/能不能正确调用）
    handler -> 本地执行的 Python 函数（决定调用结果对不对）

新增工具只需三步：
    1. 写好 handler，签名用关键字参数，返回 str 或可 JSON 序列化的 dict
    2. 写好 schema，description 要写清"什么时候用、参数怎么填"
    3. 用 @register 装饰器注册，Agent 自动获得该能力
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# 工具名 -> (schema, handler)
_REGISTRY: dict[str, "Tool"] = {}


@dataclass
class Tool:
    """一个已注册的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    schema: dict[str, Any]


def register(schema: dict[str, Any]) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """把 handler 注册进工具表。schema 需为 OpenAI tools 格式。"""

    def decorator(handler: Callable[..., str]) -> Callable[..., str]:
        fn = schema["function"]
        name = fn["name"]
        if name in _REGISTRY:
            raise ValueError(f"工具名重复：{name}")
        _REGISTRY[name] = Tool(
            name=name,
            description=fn.get("description", ""),
            parameters=fn.get("parameters", {}),
            handler=handler,
            schema=schema,
        )
        return handler

    return decorator


def all_schemas() -> list[dict[str, Any]]:
    """返回全部工具 schema，直接喂给 LLM 的 tools 参数。"""
    return [tool.schema for tool in _REGISTRY.values()]


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def invoke(name: str, arguments: dict[str, Any]) -> str:
    """按名字执行工具。任何异常都转为可读文本，由 Agent 回传给模型自行纠错。"""
    tool = _REGISTRY.get(name)
    if tool is None:
        available = ", ".join(sorted(_REGISTRY)) or "（无）"
        return f"[错误] 不存在名为 {name!r} 的工具。可用工具：{available}"

    try:
        result = tool.handler(**arguments)
    except TypeError as exc:
        # 参数名/个数不对——模型最常犯的错，把正确参数列出来提示它
        params = ", ".join(tool.parameters.get("properties", {})) or "（无）"
        return f"[错误] 调用 {name} 的参数不合法：{exc}。该工具接受的参数：{params}"
    except Exception as exc:  # noqa: BLE001 - 故意兜住所有异常，交给模型决策
        return f"[错误] 执行 {name} 失败：{type(exc).__name__}: {exc}"

    return result if isinstance(result, str) else str(result)


# --------------------------------------------------------------- 公共工具函数
class UnsafePathError(ValueError):
    """请求的路径越出了允许访问的工作目录。"""


def resolve_workspace_path(raw: str, workspace: Path) -> Path:
    """把用户/模型给的路径解析为绝对路径，并校验未越权。

    安全边界：一切文件访问都被限制在 workspace 之内，
    这样"执行模型生成的路径字符串"不会变成任意文件读取漏洞。
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # Windows 上的非法路径字符等
        raise UnsafePathError(f"路径无法解析：{raw}（{exc}）") from exc

    ws = workspace.resolve()
    if resolved != ws and ws not in resolved.parents:
        raise UnsafePathError(
            f"拒绝访问工作目录之外的路径：{resolved}（工作目录：{ws}）"
        )
    return resolved


# 各语言的"符号"粗提取规则。Python 另有基于 ast 的精确实现（见 list_symbols.py）
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("class", re.compile(r"^\s*class\s+([A-Za-z_]\w*)")),
    ("def", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)")),
    ("method", re.compile(r"^\s*(?:public|private|protected|static|async|\s)*([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{")),
    ("arrow", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(")),
    ("java_type", re.compile(r"^\s*(?:public|private|protected)?\s*(?:final\s+|abstract\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)")),
    ("import", re.compile(r"^\s*(?:import|from|use|package)\s+([\w./\-]+)")),
]
