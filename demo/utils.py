"""示例：被 app.py 依赖的工具模块，用于演示跨文件调用链分析。"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_AGE_PATTERN = re.compile(r"^\d{1,3}$")


def clean_text(value: str) -> str:
    """折叠连续空白并去掉首尾空格。输入非字符串时返回空串。"""
    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", value).strip()


def parse_age(value: object) -> int:
    """把各种形态的年龄值转成 int。

    支持的输入：int、' 28 '、'28岁'（提取数字部分）。
    非法输入抛 ValueError，由调用方决定是跳过还是报错——
    库函数不应该替上层决定"错误怎么处理"。
    """
    if isinstance(value, bool):  # bool 是 int 的子类，必须先排除
        raise ValueError(f"年龄不能是布尔值：{value!r}")
    if isinstance(value, int):
        age = value
    else:
        match = _AGE_PATTERN.search(clean_text(str(value)))
        if match is None:
            raise ValueError(f"无法解析年龄：{value!r}")
        age = int(match.group())

    if not 0 <= age <= 150:
        raise ValueError(f"年龄超出合理范围：{age}")
    return age


def normalize_record(record: dict) -> dict:
    """把原始记录规范化为 {'name': str, 'age': int}。

    异常约定：
        TypeError  —— record 不是 dict
        KeyError   —— 缺少 name 字段
        ValueError —— age 无法解析
    """
    if not isinstance(record, dict):
        raise TypeError(f"记录必须是 dict，收到 {type(record).__name__}")

    name = clean_text(record["name"])  # 缺字段时抛 KeyError，符合上面的约定
    age = parse_age(record.get("age", 0))

    return {"name": name, "age": age, "source": "normalized"}
