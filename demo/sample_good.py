"""示例：一段写得还不错、但仍有可讨论之处的代码。

用于演示"代码解释 Agent"在正常代码上的表现：
    - 是否能讲清设计意图
    - 是否能指出隐式假设（时区、浮点精度、可变默认值）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

# 会员等级 -> 折扣系数（业务规则集中在此，避免散落在各处）
MEMBER_DISCOUNT = {
    "normal": Decimal("1.00"),
    "silver": Decimal("0.95"),
    "gold": Decimal("0.90"),
    "platinum": Decimal("0.85"),
}


@dataclass
class OrderItem:
    """订单中的一行商品。"""

    sku: str
    unit_price: Decimal
    quantity: int

    @property
    def subtotal(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass
class Order:
    """一张订单。金额一律用 Decimal，避免二进制浮点误差累积。"""

    order_id: str
    member_level: str = "normal"
    items: list[OrderItem] = field(default_factory=list)  # 注意：不能用 [] 作默认值
    created_at: datetime = field(default_factory=datetime.now)
    coupon_amount: Decimal = Decimal("0.00")

    @property
    def total_amount(self) -> Decimal:
        """应付金额 = 商品小计之和 × 会员折扣 − 优惠券，且不为负。"""
        gross = sum((item.subtotal for item in self.items), start=Decimal("0.00"))
        discount = MEMBER_DISCOUNT.get(self.member_level, Decimal("1.00"))

        if self.member_level not in MEMBER_DISCOUNT:
            # 未知等级不静默按原价处理，留一条日志便于排查上游脏数据
            logger.warning("未知会员等级 %r，按原价计算：order=%s", self.member_level, self.order_id)

        net = (gross * discount - self.coupon_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return max(net, Decimal("0.00"))


def is_within_return_window(
    created_at: datetime,
    *,
    now: datetime | None = None,
    window_days: int = 7,
) -> bool:
    """判断订单是否仍在七天无理由退货期内。

    把"当前时间"作为可注入参数，是为了让这个函数可测试——
    否则测试必须依赖真实系统时间，会产生偶发失败（flaky test）。
    """
    current = now or datetime.now()
    deadline = created_at + timedelta(days=window_days)
    return current <= deadline


def summarize_orders(orders: list[Order]) -> dict[str, object]:
    """汇总一批订单，返回统计信息。空列表时返回零值而非抛异常。"""
    if not orders:
        return {"count": 0, "amount": Decimal("0.00"), "avg": Decimal("0.00")}

    amount = sum((o.total_amount for o in orders), start=Decimal("0.00"))
    today_orders = [o for o in orders if o.created_at.date() == date.today()]

    return {
        "count": len(orders),
        "amount": amount,
        "avg": (amount / len(orders)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "today_count": len(today_orders),
    }
