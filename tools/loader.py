"""工具装载器：显式导入所有工具模块，使其 @register 生效。

为什么单独一个文件而不是在 tools/__init__.py 里 import：
    避免循环导入（各工具模块要 from tools import register）。
    显式列出的另一个好处是——"Agent 具备哪些能力"一目了然，
    评审时不用翻遍代码目录。
"""

from __future__ import annotations

from typing import Any

_LOADED = False


def load_all() -> None:
    """导入全部工具模块（幂等）。"""
    global _LOADED
    if _LOADED:
        return

    # 以下导入的副作用就是"把自己注册进工具表"
    from tools import list_dir, list_symbols, read_code, run_python, search_code, write_report  # noqa: F401

    _LOADED = True


def schemas() -> list[dict[str, Any]]:
    """返回所有已注册工具的 schema。"""
    load_all()
    from tools import all_schemas

    return all_schemas()
