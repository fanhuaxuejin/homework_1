"""诊断脚本：核对关键文件的真实内容与 git 忽略规则是否按预期生效。

存在的意义：PowerShell 控制台默认以 GBK 渲染 UTF-8 文本，会造成"文件看起来
乱码了"的误判。用本脚本（显式 UTF-8 读取 + 逐行标号）可以确认真实内容。

运行：
    python tests/diagnose_files.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def show(name: str) -> None:
    path = ROOT / name
    print(f"--- {name} （{path.stat().st_size if path.exists() else 0} 字节）---")
    if not path.exists():
        print("  不存在")
        return
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        # 用 repr 展示，避免终端编码影响判断
        marker = "" if line.strip() and not line.startswith("#") else "  (注释/空行)"
        print(f"  {i:>3}| {line!r}{marker}")
    print()


print("=" * 70)
print("关键文件真实内容（repr 形式，不受终端编码影响）")
print("=" * 70)
for f in [".gitignore", ".env.example", "requirements.txt"]:
    show(f)

# .env 里的 Key 做脱敏展示
env_path = ROOT / ".env"
if env_path.exists():
    print("--- .env （Key 已脱敏）---")
    for i, line in enumerate(env_path.read_bytes().decode("utf-8-sig").splitlines(), 1):
        if line.strip().startswith("LLM_API_KEY="):
            value = line.split("=", 1)[1]
            masked = value[:6] + "*" * max(0, len(value) - 9) + value[-3:]
            print(f"  {i:>3}| LLM_API_KEY={masked}   (长度 {len(value)})")
        else:
            print(f"  {i:>3}| {line!r}")
    print()

# ---------------------------------------------------- git 忽略规则验证
print("=" * 70)
print("git 忽略规则验证")
print("=" * 70)

if not (ROOT / ".git").exists():
    print("  尚未初始化 git 仓库，跳过。初始化后再运行本脚本可验证忽略规则。")
    sys.exit(0)

critical = [
    ".env",                 # 绝不能提交：含真实 Key
    "reports/x.md",         # 运行产物
    ".agent_tmp/snippet.py",  # 临时脚本
    ".agent_memory/last.json",  # 会话记录
    ".deps/foo.py",         # 本地依赖
    "__pycache__/x.pyc",    # 字节码
]
should_track = [
    ".env.example",         # 模板必须提交
    "main.py",
    "config.py",
    "agent/core.py",
    "tools/read_code.py",
    "demo/sample_buggy.py",
    "tests/offline_test.py",
    "README.md",
    "Design.md",
    "requirements.txt",
    ".gitignore",
]

print("\n  [应被忽略]")
problems = []
for path in critical:
    r = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True
    )
    ignored = r.returncode == 0
    print(f"    {'OK ' if ignored else '✗ 未忽略!'}  {path}")
    if not ignored:
        problems.append(path)

print("\n  [应被跟踪]")
for path in should_track:
    r = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True
    )
    tracked = r.returncode != 0  # 没被忽略 = 可跟踪
    exists = (ROOT / path).exists()
    status = "OK " if (tracked and exists) else ("✗ 被误忽略" if not tracked else "✗ 文件缺失")
    print(f"    {status}  {path}")
    if not tracked or not exists:
        problems.append(path)

print()
if problems:
    print(f"  发现 {len(problems)} 个问题：{problems}")
    sys.exit(1)
print("  所有忽略规则均符合预期。")
