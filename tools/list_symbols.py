"""工具：list_symbols —— 提取文件的结构骨架（类、函数、方法、导入）。

这是本 Agent 相对"把文件直接丢给模型"的核心增量之一：
    先用结构化手段（Python 用 ast 精确解析，其他语言用正则粗提取）
    得到"符号 → 行号区间"的索引，模型就能只精读真正相关的几十行，
    而不是把 3000 行塞进上下文。既降成本，也提高解释的定位精度。

对应作业中"代码理解"这一核心技术要点。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from config import load_settings
from tools import _PATTERNS, register, resolve_workspace_path
from tools.read_code import _read_text

MAX_SYMBOLS = 200


def _docstring_summary(node: ast.AST) -> str:
    """取 docstring 首行，作为符号的一句话说明，帮助模型判断是否需要细看。"""
    doc = ast.get_docstring(node)  # type: ignore[arg-type]
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    return first[:80]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """把 ast 参数还原成 Python 签名文本，例如 (a, b=1, *args, **kwargs) -> int。

    注意：返回类型注解 returns 挂在**函数节点**上，而不是 node.args 上
    （曾经写错成 a.returns，导致所有 Python 文件解析直接抛 AttributeError）。
    """
    a = node.args
    parts: list[str] = []

    posonly = getattr(a, "posonlyargs", [])
    for arg in posonly:
        parts.append(arg.arg)
    if posonly:
        parts.append("/")

    for arg in a.args:
        parts.append(arg.arg)

    if a.vararg:
        parts.append(f"*{a.vararg.arg}")
    elif a.kwonlyargs:
        parts.append("*")

    for arg in a.kwonlyargs:
        parts.append(arg.arg)

    if a.kwarg:
        parts.append(f"**{a.kwarg.arg}")

    text = ", ".join(parts)
    if node.returns is not None:
        try:
            text += f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001 - unparse 在极端语法下可能失败
            pass
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({text})"


def _analyze_python(text: str) -> list[str]:
    """用 ast 精确解析 Python 文件。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"[语法错误] 无法解析该 Python 文件：第 {exc.lineno} 行 {exc.msg}"]

    rows: list[str] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "."
            imports.extend(f"{module}.{alias.name}" for alias in node.names)

    if imports:
        rows.append(f"IMPORTS ({len(imports)}): " + ", ".join(sorted(set(imports))[:25]))

    def emit(node: ast.AST, indent: str, kind: str) -> None:
        name = getattr(node, "name", "?")
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start) or start
        if kind == "class":
            bases = []
            for base in node.bases:  # type: ignore[attr-defined]
                try:
                    bases.append(ast.unparse(base))
                except Exception:  # noqa: BLE001
                    bases.append("?")
            head = f"{indent}class {name}" + (f"({', '.join(bases)})" if bases else "")
        else:
            head = f"{indent}{_signature(node)}"  # type: ignore[arg-type]
        doc = _docstring_summary(node)
        if doc:
            head += f"   # {doc}"
        rows.append(f"L{start}-{end}  {head}")

    # 先顶层，再类内方法，保持源码顺序，便于模型理解结构
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            emit(node, "", "class")
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    emit(child, "    ", "def")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            emit(node, "", "def")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    rows.append(f"L{node.lineno}  CONST {target.id}")

    return rows


def _analyze_generic(text: str) -> list[str]:
    """非 Python 文件用正则粗提取，够用即可（不追求语法精确）。"""
    rows: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("//", "#", "*", "/*")):
            continue
        for kind, pattern in _PATTERNS:
            match = pattern.match(line)
            if match:
                rows.append(f"L{lineno}  [{kind}] {match.group(1)}")
                break
    return rows


@register(
    {
        "type": "function",
        "function": {
            "name": "list_symbols",
            "description": (
                "提取一个源码文件的结构骨架：类、函数、方法的名称与所在行号区间"
                "（Python 用 ast 精确解析，并还原函数签名与 docstring 摘要）。"
                "解释代码前应先调用本工具建立全局认识，"
                "然后用 read_code 的 start_line/end_line 精读目标符号，这样更省上下文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "源码文件路径，相对于工作目录",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    }
)
def list_symbols(path: str) -> str:
    settings = load_settings()

    try:
        target = resolve_workspace_path(path, settings.workspace)
    except Exception as exc:  # noqa: BLE001
        return f"[错误] {exc}"

    if not target.exists():
        return f"[错误] 文件不存在：{path}"
    if target.is_dir():
        return f"[错误] {path} 是目录。list_symbols 只作用于单个文件。"

    try:
        text, _encoding = _read_text(target)
    except OSError as exc:
        return f"[错误] 读取失败：{exc}"

    if target.suffix.lower() in {".py", ".pyi"}:
        rows = _analyze_python(text)
        engine = "Python ast"
    else:
        rows = _analyze_generic(text)
        engine = "正则粗提取"

    if not rows:
        return f"文件 {path} 中未提取到任何符号（可能是纯数据/配置文件）。"

    overflow = ""
    if len(rows) > MAX_SYMBOLS:
        overflow = f"\n...（另有 {len(rows) - MAX_SYMBOLS} 个符号未列出）"
        rows = rows[:MAX_SYMBOLS]

    header = (
        f"文件：{path}（解析方式：{engine}）\n"
        f"共 {len(text.splitlines())} 行，提取到 {len(rows)} 个条目\n"
        f"{'-' * 60}\n"
    )
    return header + "\n".join(rows) + overflow
