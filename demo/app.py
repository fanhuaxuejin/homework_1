"""示例：跨文件调用链，用于演示 search_code 工具的定位能力。

问题示例（可在交互模式里直接问）：
    "process_batch 是怎么被调用的？它依赖了哪些函数？"
    Agent 应当用 search_code 找到 process_batch 的定义与调用点，
    再读入它们，最后串出完整的调用链，而不是凭函数名猜测。
"""

from collections.abc import Iterable

# 同目录导入：因此 search_code 检索 "normalize_record" 时应命中两个文件
from demo.utils import clean_text, normalize_record


def process_batch(records: Iterable[dict]) -> list[dict]:
    """清洗并规范化一批记录。

    调用链：process_batch -> normalize_record -> clean_text
    """
    output: list[dict] = []
    for record in records:
        try:
            cleaned = normalize_record(record)
        except (TypeError, ValueError, KeyError) as exc:
            # 只捕获"数据本身有问题"的异常，不吞掉程序缺陷
            output.append({"error": f"记录被跳过：{exc}", "raw": record})
            continue
        if cleaned and clean_text(str(cleaned.get("name", ""))):
            output.append(cleaned)
    return output


def main() -> None:
    sample = [
        {"name": "  Alice  ", "age": "30"},
        {"name": "", "age": "x"},
        {"age": "28"},
    ]
    for item in process_batch(sample):
        print(item)


if __name__ == "__main__":
    main()
