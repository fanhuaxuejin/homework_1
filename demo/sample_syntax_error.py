"""示例：内含语法错误的文件，用于验证 Agent 在"边界情况"下的表现。

期望行为（作业评分点明确提到"边界情况处理"）：
    - list_symbols 应返回"[语法错误] 无法解析该 Python 文件：第 N 行 ..."
      而不是崩溃或抛出未捕获异常
    - read_code 仍应能正常读出原文（读取不依赖语法正确）
    - Agent 应如实告知用户"该文件有语法错误，无法完整解析结构"，
      并指出错误所在行，而不是编造结构

下面第 20 行的括号故意不闭合。
"""


def broken_function(a, b):
    """这个函数的右括号被删掉了。"""
    result = a + b
    return result


def another_function():
    # 上一行的语法错误会连带影响下面的解析
    print(broken_function(1, 2)
