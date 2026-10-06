"""工具：list_dir —— 查看目录结构。

让模型在动手读文件之前先建立"项目长什么样"的全局视图，
避免盲目猜测路径、反复试错（既费 token 也容易让 Agent 陷入循环）。
"""

from __future__ import annotations

from pathlib import Path

from config import load_settings
from tools import register, resolve_workspace_path

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache",
}
MAX_ENTRIES = 300


@register(
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "列出目录结构（默认递归 2 层），显示文件名与大小。"
                "当你不确定项目里有哪些文件、需要先了解整体结构时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，相对于工作目录。使用 '.' 表示工作目录本身。",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "递归深度，默认 2，最大 4",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    }
)
def list_dir(path: str = ".", max_depth: int = 2) -> str:
    settings = load_settings()

    try:
        target = resolve_workspace_path(path, settings.workspace)
    except Exception as exc:  # noqa: BLE001
        return f"[错误] {exc}"

    if not target.exists():
        return f"[错误] 路径不存在：{path}"
    if not target.is_dir():
        return f"[错误] {path} 不是目录，而是文件。请直接用 read_code 读取。"

    depth_limit = max(1, min(int(max_depth or 2), 4))
    lines: list[str] = []
    count = 0
    truncated = False

    def walk(directory: Path, depth: int, prefix: str) -> None:
        nonlocal count, truncated
        if depth > depth_limit or truncated:
            return
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError as exc:
            lines.append(f"{prefix}[无法访问：{exc}]")
            return
        for entry in entries:
            if entry.name in SKIP_DIRS:
                continue
            if count >= MAX_ENTRIES:
                truncated = True
                return
            count += 1
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                walk(entry, depth + 1, prefix + "    ")
            else:
                try:
                    size = entry.stat().st_size
                    human = (
                        f"{size} B" if size < 1024
                        else f"{size / 1024:.1f} KB"
                        if size < 1024 * 1024
                        else f"{size / 1024 / 1024:.1f} MB"
                    )
                except OSError:
                    human = "?"
                lines.append(f"{prefix}{entry.name}  ({human})")

    walk(target, 1, "")

    if not lines:
        return f"目录 {path} 为空（或仅包含被忽略的目录）。"

    header = (
        f"目录：{path}（深度 {depth_limit}）\n"
        f"共 {count} 个条目\n"
        + "-" * 60
        + "\n"
    )
    footer = "\n[提示] 条目过多已截断。" if truncated else ""
    return header + "\n".join(lines) + footer
