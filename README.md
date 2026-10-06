# 代码解释 Agent（Code Explanation Agent）

一个基于 **DeepSeek** 的代码解释助手：给它一个文件或一个问题，它会**自己去读代码、检索调用链、必要时实际运行代码验证**，然后给出带行号引用的结构化解释。

> 软件工程课程 Homework 1 —— 方向：**代码解释 Agent**

---

## 一、它解决什么问题

把代码丢给聊天窗口问"这段什么意思"，模型只能靠猜测，常见后果是：**编造不存在的函数、行号对不上、大文件读不完就瞎答**。

本项目的做法是让模型**具备自主获取信息的能力**，而不是被动接受一段文本：

| 场景 | 普通对话式提问 | 本 Agent |
|------|--------------|---------|
| 解释一个 3000 行文件 | 粘贴部分内容，其余靠猜 | 先 `list_symbols` 建骨架，再按行号区间精读相关部分 |
| "这个函数在哪被调用？" | 编造或说"看不到" | `search_code` 实际检索全仓库，给出 `文件:行号` |
| "这段位运算到底做什么？" | 用自然语言描述推测 | `run_python` 实际执行，用真实输出作为依据 |
| 文件是 GBK 编码 | 报编码错误 | 自动尝试 utf-8 / gbk / latin-1 |
| 文件有语法错误 | 崩溃或硬答 | 如实报告"第 22 行括号未闭合，无法完整解析" |

---

## 二、快速开始

### 1. 环境要求

- Python 3.10+（开发环境为 3.12.6）
- 一个 DeepSeek API Key：[platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

### 2. 安装依赖

```powershell
cd code-agent
python -m pip install -r requirements.txt
```

### 3. 配置 API Key

```powershell
copy .env.example .env
notepad .env          # 把 LLM_API_KEY 换成你的真实 Key
```

`.env` 已在 `.gitignore` 中，**不会被提交**。也可以直接用环境变量：

```powershell
$env:LLM_API_KEY = "sk-你的Key"
```

### 4. 环境自检（不消耗 API 额度）

```powershell
python main.py --check
```

预期输出：列出 6 个已装载工具、逐个冒烟测试，并确认 Key 是否有效。**建议第一次务必先跑这一步**，它能在不花钱的前提下区分"环境问题"和"Agent 逻辑问题"。

### 5. 开始使用

```powershell
python main.py                                    # 交互模式（推荐）
python main.py "解释 demo/sample_buggy.py"         # 单次提问
```

---

## 三、交互模式与命令

进入 `python main.py` 后：

```
解释 demo/sample_buggy.py                直接提问（Agent 会自己决定调哪些工具）
demo/utils.py 第 20-40 行是什么意思        追问具体区间
normalize_record 在哪里被调用了？          跨文件问题，会触发 search_code
把 sample_good.py 的解释写成文档           会触发 write_report

/symbols demo/sample_good.py             查看文件结构（本地执行，不发请求）
/files [目录]                            列出目录（本地执行，不发请求）
/tools                                   查看已装载工具
/usage                                   查看 token 用量与费用估算
/clear                                   清空对话记忆
/load                                    载入上次会话
/full                                    切换"显示工具完整输出"
/help   /quit
```

命令行参数：

| 参数 | 说明 |
|------|------|
| `-w, --workspace PATH` | 指定工作目录，同时作为**路径安全边界** |
| `--max-steps N` | 单次提问最大推理步数（默认 12），防死循环 |
| `--verbose` | 打印工具调用的完整返回内容 |
| `--debug` | 打印事件原始数据，排错用 |
| `--no-persist` | 不把会话写入 `.agent_memory/` |
| `--check` | 只做环境自检后退出 |

---

## 四、工具清单

Agent 的能力边界由这 6 个工具决定（新增工具见 `tools/__init__.py` 顶部说明）：

| 工具 | 作用 | 关键设计 |
|------|------|---------|
| `list_dir` | 查看目录结构（递归 1–4 层） | 让 Agent 先建立全局视图，避免盲猜路径 |
| `list_symbols` | 提取类/函数/方法骨架与**行号区间** | Python 用 `ast` 精确解析，还原签名与 docstring；其他语言用正则粗提取 |
| `read_code` | 按行号区间读取代码，**输出带行号** | 支持分段读取；自动尝试 utf-8 / gbk / latin-1 编码 |
| `search_code` | 正则检索全仓库，返回 `文件:行号` 与上下文 | 支撑跨文件调用链分析 |
| `run_python` | 在受控子进程中执行小段代码 | 硬超时 + 输出截断 + 剔除含密钥的环境变量 |
| `write_report` | 把结论写成 Markdown 到 `reports/` | 文件名白名单化，防路径穿越 |


---

## 五、项目结构

```
code-agent/
├─ main.py                  CLI 入口：参数解析、事件渲染、交互循环
├─ config.py                配置层：provider 可插拔、Key 与参数读取
├─ requirements.txt
├─ .env.example             Key 配置模板（复制为 .env 使用）
├─ README.md                本文件：安装与使用
├─ Design.md                设计文档：架构、循环、Prompt、安全、取舍
├─ agent/
│  ├─ core.py               ★ Agent 核心循环（输入→推理→工具调用→输出）
│  ├─ llm.py                LLM 封装层：重试、超时、思考模式、用量统计
│  ├─ memory.py             两级记忆：滑窗裁剪 + 会话落盘
│  └─ prompts.py            提示词层：角色、工具策略、输出规范、few-shot
├─ tools/
│  ├─ __init__.py           工具注册表、路径安全校验
│  ├─ loader.py             工具装载器
│  ├─ read_code.py          读取代码（编码容错）
│  ├─ list_symbols.py       结构提取（ast / 正则）
│  ├─ search_code.py        全仓库正则检索
│  ├─ list_dir.py           目录浏览
│  ├─ run_python.py         受限代码执行
│  └─ write_report.py       生成 Markdown 报告
└─ demo/                    演示样例（见上一节）
```

详细设计说明见 **[Design.md](Design.md)**。

---

## 六、模型配置

主力使用 **DeepSeek**，同时支持一行切换其他厂商（见 `config.py` 的 `PROVIDERS`）。

```ini
# .env
LLM_PROVIDER=deepseek                 # 可选 deepseek / qwen / glm
LLM_MODEL=deepseek-flash              # 留空则用该 provider 默认模型
LLM_THINKING=1                        # 思考模式开关
LLM_TIMEOUT=120
LLM_MAX_RETRIES=4
```

> ⚠️ **模型名注意**：DeepSeek 当前可用模型为 `deepseek-flash`与 `deepseek-v4-pro`。
> 旧的 `deepseek-chat` / `deepseek-reasoner` **已下线**，写成旧名会报 404。
> 详见 [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。

---

## 七、常见问题

**Q：报错说缺少依赖 openai？**
执行 `python -m pip install -r requirements.txt`。

**Q：中文输出乱码？**
PowerShell 默认代码页不是 UTF-8，先执行 `chcp 65001`，或设置 `$env:PYTHONIOENCODING="utf-8"`。

**Q：报 401 / 认证失败？**
`.env` 里的 `LLM_API_KEY` 没填或已失效，去平台重新生成。

**Q：报 404 / model not found？**
`LLM_MODEL` 写了已下线的模型名，删掉该行用默认值，或改为 `deepseek-flash`。

**Q：Agent 一直反复读同一个文件？**
把 `--max-steps` 调小（如 6）能更快止损；同时检查问题是否过于笼统——问题越具体，工具调用越高效。

**Q：能不能直接解释别的仓库？**
可以：`python main.py -w "E:\some\repo" "解释 src/main.py"`。目标目录会同时成为路径安全边界。

---

## 八、安全与隐私说明

- **只读优先**：Agent 默认不修改用户任何文件，唯一的写操作是 `write_report`，且被限制在工作目录的 `reports/` 子目录内，文件名经过白名单化处理。
- **路径边界**：所有文件访问都被限制在 `--workspace` 指定的目录内，`../../` 之类的越权路径会被拒绝。
- **代码外发**：解释代码时，被读取的代码内容会发送给 DeepSeek API。请勿用它处理机密代码。
- **执行隔离**：`run_python` 在独立子进程中执行，带硬超时与输出截断，并剔除含 `API_KEY`/`TOKEN`/`SECRET` 等关键字的环境变量。**但这不是容器级沙箱**，仅适合本地自查；不要拿它执行来路不明的代码。
- **密钥管理**：Key 只从环境变量 / `.env` 读取，代码中不存在硬编码；`.env` 已被 `.gitignore` 排除。
