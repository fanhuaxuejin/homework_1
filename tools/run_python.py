"""工具：run_python —— 在受控条件下运行一小段 Python 代码。

用途（代码解释方向的"证据"能力）：
    解释一段"看不出来干嘛"的代码时，与其猜，不如实际跑一遍看到输出。
    这让 Agent 从"根据文本猜测"升级为"用执行结果验证"，是 agentic 的关键体现。

安全边界（重要，答辩时值得讲）：
    1. 独立子进程执行，带硬超时，超时即杀——防止死循环挂住 Agent
    2. 输出长度截断——防止无限打印撑爆上下文
    3. 工作目录固定在工作区，且不继承含密钥的环境变量——降低凭据泄露风险
    4. 临时脚本写入工作目录下固定的 .agent_tmp/，执行完立即删除
    限制说明：这不是生产级沙箱（无容器/无 seccomp），仅适合本地教学与自查；
    若要对外提供服务，应替换为容器或子进程权限受限的执行环境。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from config import load_settings
from tools import register

MAX_OUTPUT_CHARS = 6_000
# 把可能泄露凭据的环境变量从子进程环境中剔除
_SECRET_ENV_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _sanitize_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(hint in k.upper() for hint in _SECRET_ENV_HINTS)
    }
    # 强制子进程的无缓冲输出，便于捕获崩溃前的最后一行
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@register(
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "执行一小段 Python 代码并返回 stdout/stderr。"
                "当你需要验证某段代码的真实行为、打印中间结果、确认某函数返回值时使用。"
                "只应提交短小、无副作用、不依赖外部文件的片段；"
                "代码中的 __main__ 副作用会被自动隔离。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 源码，例如 'print(sum(range(10)))'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认取配置值（通常 10 秒），最大 30 秒",
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        }
    }
)
def run_python(code: str, timeout: int | None = None) -> str:
    settings = load_settings()

    if not code or not code.strip():
        return "[错误] code 为空。请提供要执行的 Python 代码。"

    limit = settings.run_python_timeout if timeout is None else int(timeout)
    limit = max(1, min(limit, 30))

    # 临时脚本放在工作目录下的 .agent_tmp/，而不是系统 temp 目录。
    # 原因：在受限的执行环境（沙箱、只读系统盘的容器）里系统 temp 可能不可写，
    # 而工作目录一定是当前 Agent 有权访问的目录。系统 temp 作为兜底方案。
    # 临时脚本放在工作目录下固定的 .agent_tmp/ 里，而不是新建随机名目录。
    # 原因：在受限执行环境（沙箱、只读系统盘）中，运行时新建的随机目录可能
    # 不被允许写入，导致工具直接不可用。固定目录 + 以进程号命名的文件
    # 既保证并发安全（不同进程不会撞名），也保证可写。
    tmp_dir = settings.workspace / ".agent_tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = tmp_dir.resolve()
    except OSError as exc:
        return f"[错误] 无法创建临时执行目录 {tmp_dir}：{exc}"

    script = tmp_dir / f"snippet_{os.getpid()}_{int(time.time() * 1000) % 100000}.py"
    try:
        script.write_text(code, encoding="utf-8")
    except OSError as exc:
        return f"[错误] 无法写入临时脚本 {script}：{exc}"

    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            cwd=str(settings.workspace),
            env=_sanitize_env(),
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = f"执行超时（超过 {limit} 秒）已被强制终止。可能存在死循环或阻塞等待输入。"
        exit_code = -1
        timed_out = True
    except OSError as exc:
        return f"[错误] 无法启动 Python 子进程：{exc}"
    finally:
        try:
            script.unlink(missing_ok=True)
        except OSError:
            pass

    def clip(text: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        return (
            text[:MAX_OUTPUT_CHARS]
            + f"\n...[输出过长已截断，共 {len(text)} 字符]"
        )

    parts = [f"执行状态：{'超时' if timed_out else '完成'} | 退出码：{exit_code}"]
    parts.append("-" * 60)
    parts.append("stdout:\n" + (clip(stdout) if stdout else "(空)"))
    if stderr:
        parts.append("stderr:\n" + clip(stderr))
    return "\n".join(parts)
