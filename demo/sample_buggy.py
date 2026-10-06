"""示例：一段**故意埋了 Bug** 的代码，用于检验 Agent 的代码理解与问题发现能力。

埋点清单（不告诉 Agent，用于人工核对它的输出）：
    1. 可变默认参数 items=[]      —— 跨调用共享状态，经典陷阱
    2. 浮点数累加金额            —— 0.1 + 0.2 精度问题
    3. 除零风险                  —— len(items) 为 0 时崩溃
    4. 越界访问                  —— 空列表取 [-1] 不报错但语义错误
    5. 宽泛的 except            —— 吞掉所有异常，掩盖真实故障
    6. 变量作用域/闭包问题        —— 循环变量被闭包捕获
    7. 字符串拼接构造 SQL         —— 注入风险（即便只是示例）
    8. 时间比较缺少时区处理        —— 混用 naive / aware datetime 会抛 TypeError
"""

import datetime
import sqlite3


def add_item(item, items=[]):
    """把 item 加入购物车。BUG 1：默认参数是可变对象。"""
    items.append(item)
    return items


def calc_total(prices):
    """计算总价。BUG 2：浮点累加；BUG 3：空列表除零。"""
    total = 0.0
    for p in prices:
        total += p
    return total, total / len(prices)


def last_order_id(orders):
    """取最后一单的编号。BUG 4：空列表时 orders[-1] 抛 IndexError。"""
    return orders[-1]


def load_config(path):
    """读取配置。BUG 5：except 过于宽泛，且吞掉异常后返回 None，
    调用方无法区分"文件不存在"和"内容格式错误"。"""
    try:
        with open(path) as f:
            return f.read()
    except:
        return None


def make_validators(rules):
    """生成一组校验函数。BUG 6：闭包捕获的是循环变量本身，
    所有函数最终都会用 rules 里的最后一个值。"""
    validators = []
    for rule in rules:
        def validator(value):
            return value == rule
        validators.append(validator)
    return validators


def find_user(conn: sqlite3.Connection, username):
    """按用户名查询。BUG 7：字符串拼接 SQL 导致注入风险。"""
    cursor = conn.cursor()
    sql = "SELECT id, name FROM users WHERE name = '" + username + "'"
    cursor.execute(sql)
    return cursor.fetchone()


def is_expired(expire_at, now=None):
    """判断是否过期。BUG 8：expire_at 若为带时区的 datetime，
    与 naive 的 datetime.now() 比较会直接抛 TypeError。"""
    now = now or datetime.datetime.now()
    return now > expire_at


def process_orders(orders):
    """批量处理订单——把上面几个问题串起来的"业务入口"。"""
    results = []
    for order in orders:
        try:
            total, avg = calc_total(order.get("prices", []))
            results.append({"id": order["id"], "total": total, "avg": avg})
        except:
            results.append({"id": order.get("id"), "error": True})
    return results
