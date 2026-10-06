"""代码解释 Agent —— 一个不依赖 Agent 框架、自行实现 Agent 循环的代码助手。

包结构：
    config.py        配置层：provider 可插拔、Key 与参数的读取
    agent/llm.py     LLM 封装层：重试、超时、思考模式、用量统计
    agent/core.py    Agent 核心：输入 → 推理 → 工具调用 → 输出 循环
    agent/memory.py  上下文记忆：多轮对话 + token 预算裁剪
    agent/prompts.py 提示词层：角色、输出规范、few-shot
    tools/           工具层：代码读取、符号提取、检索、执行等
    main.py          CLI 入口
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
