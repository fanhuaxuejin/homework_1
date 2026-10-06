# -*- coding: gbk -*-
"""示例：GBK 编码且含中文注释的文件，用于验证工具层的编码容错。

这段代码实现一个简单的先进先出（FIFO）缓存，超过容量时淘汰最早写入的项。
"""

from collections import OrderedDict


class SimpleCache:
    """容量受限的 FIFO 缓存（注意：不是 LRU，读取不会改变淘汰顺序）。"""

    def __init__(self, capacity=100):
        if capacity <= 0:
            raise ValueError("容量必须为正整数")
        self.capacity = capacity
        self._store = OrderedDict()
        # 统计命中率，便于观察缓存效果
        self.hits = 0
        self.misses = 0

    def get(self, key):
        """读取缓存。命中返回值，未命中返回 None 并记一次 miss。"""
        if key in self._store:
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return None

    def put(self, key, value):
        """写入缓存，超容量时淘汰最早写入的键。"""
        if key in self._store:
            self._store[key] = value
            return
        if len(self._store) >= self.capacity:
            self._store.popitem(last=False)
        self._store[key] = value

    @property
    def hit_rate(self):
        """命中率。注意：分母为 0 时返回 0 而不是抛异常。"""
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total