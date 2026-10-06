"""代码解释 Agent —— 命令行入口。

用法：
    python main.py                                  # 交互模式
    python main.py "解释 demo/sample_buggy.py"       # 单次提问
    python main.py -w E:\\some\\repo "解释 main.py"  # 指定工作目录
    python main.py --verbose                        # 打印工具调用的完整参数
    python main.py --debug                          # 打印事件原始数据（排错用）

设计说明：
    CLI 只做三件事——解析参数、渲染事件、循环读取输入。
    所有 Agent 逻辑都在 agent/core.py，因此换 Web 界面不需要改这里以外的任何代码。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python main.py` 直接运行（把项目根目录加入模块搜索路径）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.core import CodeExplainAgent  # noqa: E402
from agent.prompts import WELCOME  # noqa: E402
from config import describe, load_settings  # noqa: E402

# --------------------------------------------------------------- 终端着色
_COLORS = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
}


def _supports_color() -> bool:
    # Windows Terminal / PowerShell 7 支持 ANSI；重定向输出时关闭颜色
    return sys.stdout.isatty()


def colorize(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


class EventRenderer:
    """把 Agent 的事件渲染成人能读的终端输出。

    工具返回的长文本默认折叠，避免一次 read_code 就把屏幕刷满；
    想看全文用 --verbose 或 /full 命令切换。
    """

    def __init__(self, verbose: bool = False, show_full_tool_output: bool = False) -> None:
        self.verbose = verbose
        self.show_full_tool_output = show_full_tool_output

    def __call__(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "step":
            steps = colorize(f"[{event['step']}/{event['max_steps']}]", "dim")
            print(f"\n{steps} {colorize(event['text'], 'cyan')}")

        elif etype == "tool_call":
            args = event.get("arguments", {})
            args_text = ", ".join(f"{k}={v!r}" for k, v in args.items())
            if not self.verbose and len(args_text) > 160:
                args_text = args_text[:160] + " …"
            print(colorize(f"  ⚙ 调用工具 {event['name']}({args_text})", "magenta"))

        elif etype == "tool_result":
            text = event.get("text", "")
            if self.show_full_tool_output or self.verbose:
                print(colorize("  ↳ 结果：", "dim"))
                for line in text.splitlines():
                    print(colorize(f"     {line}", "dim"))
            else:
                lines = text.splitlines()
                shown = lines[:6]
                print(colorize("  ↳ 结果：", "dim"))
                for line in shown:
                    print(colorize(f"     {line}", "dim"))
                if len(lines) > len(shown):
                    print(
                        colorize(
                            f"     …（共 {len(lines)} 行，用 --verbose 查看全部）", "dim"
                        )
                    )

        elif etype == "tool_error":
            print(colorize(f"  ✗ 工具调用失败 [{event['name']}]", "red"))
            print(colorize(f"     {event.get('text', '')[:300]}", "red"))

        elif etype == "retry":
            print(colorize(f"  ⟳ {event['text']}", "yellow"))

        elif etype == "info":
            print(colorize(f"  ℹ {event['text']}", "dim"))

        elif etype == "final":
            print()
            print(colorize("─" * 68, "dim"))
            print(event["text"])
            print(colorize("─" * 68, "dim"))

        elif etype == "error":
            print(colorize(f"\n[错误] {event['text']}", "red"), file=sys.stderr)

        elif self.verbose:
            print(colorize(f"  [event] {event}", "dim"))


# --------------------------------------------------------------- 交互模式
def interactive(agent: CodeExplainAgent, renderer: EventRenderer) -> int:
    settings = agent.settings
    print(colorize("=" * 68, "dim"))
    print(colorize("  代码解释 Agent  ·  Code Explanation Agent", "bold"))
    print(colorize("=" * 68, "dim"))
    print(colorize(f"  {describe(settings)}", "dim"))
    if not settings.has_api_key:
        print(
            colorize(
                "\n  [警告] 未检测到有效的 LLM_API_KEY。\n"
                "         请复制 .env.example 为 .env 并填入 DeepSeek API Key。\n"
                "         获取地址：https://platform.deepseek.com/api_keys\n"
                "         （仍可使用 /symbols、/files、/tools 等不发请求的本地命令）",
                "yellow",
            )
        )
    print(WELCOME)

    while True:
        try:
            raw = input(colorize("\n你 > ", "green")).strip()
        except (EOFError, KeyboardInterrupt):
            print(colorize("\n再见。", "dim"))
            return 0

        if not raw:
            continue

        # ---- 本地命令（不消耗 API）
        if raw.startswith("/"):
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in {"/quit", "/exit", "/q"}:
                path = agent.save_session()
                if path:
                    print(colorize(f"会话已保存：{path}", "dim"))
                print(colorize("再见。", "dim"))
                return 0

            if cmd == "/help":
                print(WELCOME)
                continue

            if cmd == "/clear":
                agent.clear()
                print(colorize("已清空对话记忆。", "dim"))
                continue

            if cmd == "/usage":
                print(colorize(agent.usage_text(), "dim"))
                continue

            if cmd == "/tools":
                for schema in agent.tools:
                    fn = schema["function"]
                    print(colorize(f"  · {fn['name']}: {fn['description'][:70]}…", "dim"))
                continue

            if cmd == "/files":
                print(agent.quick_files(arg or "."))
                continue

            if cmd == "/symbols":
                if not arg:
                    print(colorize("用法：/symbols <文件路径>", "yellow"))
                    continue
                print(agent.quick_symbols(arg))
                continue

            if cmd == "/full":
                renderer.show_full_tool_output = not renderer.show_full_tool_output
                state = "开启" if renderer.show_full_tool_output else "关闭"
                print(colorize(f"工具完整输出已{state}。", "dim"))
                continue

            if cmd == "/load":
                ok = agent.load_session()
                print(colorize("已载入上次会话。" if ok else "没有可载入的会话。", "dim"))
                continue

            print(colorize(f"未知命令：{cmd}，输入 /help 查看可用命令。", "yellow"))
            continue

        # ---- 正常提问
        result = agent.ask(raw)
        if result.error is None and not result.stopped_by_limit:
            print(
                colorize(
                    f"  · 用时 {result.elapsed_seconds:.1f}s | "
                    f"{result.steps} 步 | {len(result.tool_invocations)} 次工具调用",
                    "dim",
                )
            )


# --------------------------------------------------------------- 主函数
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="代码解释 Agent —— 基于 DeepSeek 的工具调用型代码理解助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python main.py\n"
            "  python main.py \"解释 demo/sample_buggy.py\"\n"
            "  python main.py --workspace ../myproject \"解释 src/main.py\"\n"
        ),
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="要提问的问题；省略则进入交互模式",
    )
    parser.add_argument(
        "-w", "--workspace",
        default=None,
        help="Agent 可访问的工作目录（默认当前目录），也是路径安全边界",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=12,
        help="单次提问最多推理步数，防止死循环（默认 12）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印工具调用的完整结果",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="打印事件原始数据，用于排查问题",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="不把会话保存到 .agent_memory/",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅做环境自检（配置、Key、工具装载）后退出，不调用 API",
    )
    return parser


def self_check(settings, agent: CodeExplainAgent) -> int:
    """离线自检：不需要 API Key 也能验证环境是否正确。"""
    print(colorize("环境自检", "bold"))
    print(f"  {describe(settings)}")
    print(f"  .env 加载：{'成功' if settings.dotenv_loaded else '未找到（将只读系统环境变量）'}")
    print(f"  已装载工具 {len(agent.tools)} 个：")
    for schema in agent.tools:
        print(f"    · {schema['function']['name']}")
    print(f"  工作目录存在：{settings.workspace.exists()}")

    # 逐个工具做一次无害调用，确认注册与路径安全都正常
    print("\n  工具冒烟测试（不发 API 请求）：")
    print("    list_dir('.') ->", agent.quick_files(".").splitlines()[0])
    print("    路径越权防护 ->", end=" ")
    from tools import invoke

    print(invoke("read_code", {"path": "../../../../Windows/System32/drivers/etc/hosts"}).splitlines()[0])

    ok = settings.has_api_key
    print(
        "\n  结果："
        + (
            colorize("环境就绪，可以开始使用。", "green")
            if ok
            else colorize("工具层就绪，但缺少 API Key，无法调用模型。", "yellow")
        )
    )
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    workspace = args.workspace
    if workspace and not Path(workspace).exists():
        print(colorize(f"[错误] 工作目录不存在：{workspace}", "red"), file=sys.stderr)
        return 2

    settings = load_settings(workspace)
    if not settings.workspace.exists():
        print(colorize(f"[错误] 工作目录不存在：{settings.workspace}", "red"), file=sys.stderr)
        return 2

    renderer = EventRenderer(verbose=args.verbose or args.debug)

    try:
        agent = CodeExplainAgent(
            settings,
            max_steps=max(1, args.max_steps),
            on_event=renderer,
            enable_memory_persist=not args.no_persist,
        )
    except ImportError as exc:
        print(colorize(f"[错误] {exc}", "red"), file=sys.stderr)
        return 2

    if args.check:
        return self_check(settings, agent)

    question = " ".join(args.question).strip()
    if not question:
        return interactive(agent, renderer)

    result = agent.ask(question)
    if result.error:
        return 1
    if result.error is None:
        print(
            colorize(
                f"\n  · 用时 {result.elapsed_seconds:.1f}s | "
                f"{result.steps} 步 | {len(result.tool_invocations)} 次工具调用",
                "dim",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
