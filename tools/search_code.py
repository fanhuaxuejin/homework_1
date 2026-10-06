"""工具：search_code —— 在代码库中检索关键字/正则。

典型用途（代码解释场景）：
    - 用户问"这个函数在哪里被调用" → 检索符号名
    - 用户问"这个变量从哪来的" → 检索赋值位置
    - 定位一个不知道在哪个文件的类定义

这是让 Agent 具备"跨文件理解"能力的关键工具，单靠 read_code 只能看到孤立文件。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from config import load_settings
from tools import register, resolve_workspace_path
from tools.read_code import SUPPORTED_SUFFIXES, _read_text

MAX_MATCHES = 60
MAX_CHARS = 20_000
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "reports", ".agent_memory", ".agent_tmp",
}


def _relative_display(file: Path, root: Path) -> str:
    """计算用于展示的相对路径。

    用 os.path.relpath 而非 Path.relative_to：Windows 上路径大小写不一致时
    relative_to 会抛 ValueError（例如工作目录被写成 C:\\users\\... 而磁盘上是
    C:\\Users\\...），导致检索结果里出现刺眼的绝对路径。
    """
    try:
        return Path(os.path.relpath(file, root)).as_posix()
    except (ValueError, OSError):
        return file.as_posix()


def _iter_files(root: Path, glob: str) -> list[Path]:
    """收集待检索文件。glob 为空时按常见源码后缀过滤。"""
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if glob:
            if not path.match(glob):
                continue
        elif path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        # 跳过过大的文件（生成的产物、数据集等）
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        files.append(path)
    return sorted(files)


@register(
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "在工作目录内按正则表达式检索代码，返回 文件:行号 及上下文行。"
                "用于定位符号定义、查找调用点、追踪变量来源等跨文件问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式，例如 'def parse_\\w+' 或 'calculate_total'",
                    },
                    "glob": {
                        "type": "string",
                        "description": "可选的文件过滤通配符，例如 '*.py'、'demo/*.js'。省略则搜索全部源码文件。",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "每个命中点前后附带的上下文行数，默认 2，最大 6",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条命中，默认 30",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        }
    }
)
def search_code(
    pattern: str,
    glob: str | None = None,
    context_lines: int = 2,
    max_results: int = 30,
) -> str:
    settings = load_settings()

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"[错误] 正则表达式不合法：{exc}。请改用更简单的模式，或对特殊字符转义。"

    context = max(0, min(int(context_lines or 0), 6))
    limit = max(1, min(int(max_results or 30), MAX_MATCHES))

    try:
        root = resolve_workspace_path(".", settings.workspace)
    except Exception as exc:  # noqa: BLE001
        return f"[错误] {exc}"

    files = _iter_files(root, glob or "")
    if not files:
        return f"[无结果] 在 {root} 下没有找到匹配 glob={glob!r} 的文件。"

    blocks: list[str] = []
    total_hits = 0
    scanned = 0
    truncated = False

    for file in files:
        if total_hits >= limit:
            truncated = True
            break
        scanned += 1
        try:
            text, _ = _read_text(file)
        except OSError:
            continue
        lines = text.splitlines()
        rel = _relative_display(file, root)

        hits_here = 0
        for idx, line in enumerate(lines):
            if not regex.search(line):
                continue
            total_hits += 1
            hits_here += 1
            lo = max(0, idx - context)
            hi = min(len(lines), idx + context + 1)
            snippet = "\n".join(
                f"{'>>' if i == idx else '  '} {i + 1:>4} | {lines[i]}"
                for i in range(lo, hi)
            )
            blocks.append(f"--- {rel}:{idx + 1} ---\n{snippet}")
            if total_hits >= limit:
                truncated = True
                break
        if hits_here == 0:
            continue

    if not blocks:
        return (
            f"[无结果] 在 {scanned} 个文件中未找到匹配 {pattern!r} 的内容。\n"
            f"[提示] 可尝试更短的关键字、检查大小写，或放宽 glob 过滤。"
        )

    body = "\n\n".join(blocks)
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS]
        truncated = True

    header = (
        f"检索：{pattern!r}"
        + (f" | glob={glob}" if glob else "")
        + f"\n命中 {total_hits} 处，扫描 {scanned} 个文件\n"
        + "-" * 60
        + "\n"
    )
    footer = (
        f"\n{'-' * 60}\n[提示] 结果已被截断，请缩小 pattern 范围或调小 max_results。"
        if truncated
        else ""
    )
    return header + body + footer
