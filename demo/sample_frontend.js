// 示例：一段 JavaScript 代码，用于验证 list_symbols 的通用（正则）解析分支。
// 同时它本身也有讨论价值：闭包、事件循环、错误处理、原型污染风险。

const DEFAULT_RETRY = 3;

class ApiClient {
  constructor(baseUrl, { retry = DEFAULT_RETRY, timeout = 5000 } = {}) {
    this.baseUrl = baseUrl;
    this.retry = retry;
    this.timeout = timeout;
    // 这里用普通对象缓存，键来自不可信输入时可能造成原型污染
    this.cache = {};
  }

  async request(path, options = {}) {
    for (let attempt = 1; attempt <= this.retry; attempt += 1) {
      try {
        return await this.#fetchWithTimeout(path, options);
      } catch (err) {
        // 最后一次仍失败才抛出，否则退避重试
        if (attempt === this.retry) throw err;
        await sleep(2 ** attempt * 100);
      }
    }
    return null;
  }

  async #fetchWithTimeout(path, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      const res = await fetch(this.baseUrl + path, {
        ...options,
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(timer); // 必须清理，否则进程无法退出
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 经典闭包陷阱：var 声明的 i 被所有回调共享
function attachHandlers(nodes) {
  for (var i = 0; i < nodes.length; i += 1) {
    nodes[i].onclick = function () {
      console.log(`clicked index ${i}`); // 永远打印 nodes.length
    };
  }
}

// 递归 + 尾调用问题：深度过大时会栈溢出
function flattenDeep(input, output = []) {
  for (const item of input) {
    if (Array.isArray(item)) {
      flattenDeep(item, output);
    } else {
      output.push(item);
    }
  }
  return output;
}

export { ApiClient, attachHandlers, flattenDeep, sleep };
