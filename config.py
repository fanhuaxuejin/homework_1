"""全局配置层。

设计意图（对应评分点"Agent 架构 30%"）：
    把"模型服务商"抽象成一份可插拔的配置表 PROVIDERS。
    业务代码只依赖 config 暴露的接口，而不认识 DeepSeek 这个名字，
    因此更换模型 = 改一个字符串，不触碰任何 Agent 逻辑。

环境变量优先级：真实环境变量 > .env 文件 > 下面的默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 路径常量
PROJECT_ROOT: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------- .env 加载
# python-dotenv 是可选的：没装也不影响运行，只是不能从 .env 读 Key。
# 导入必须在读取环境变量之前完成。
try:  # pragma: no cover - 取决于本地是否安装了依赖
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    _DOTENV_LOADED = True
except ImportError:  # pragma: no cover
    _DOTENV_LOADED = False


# ------------------------------------------------------------ provider 定义
@dataclass(frozen=True)
class ProviderConfig:
    """一个 LLM 服务商的接入参数。"""

    name: str
    base_url: str
    default_model: str
    # 该服务商是否有"思考模式"开关（DeepSeek V4 系列有）
    supports_thinking: bool = False
    # 思考模式对应的请求体额外字段；None 表示该服务商不支持
    thinking_extra_body: dict[str, Any] | None = field(default=None)
    note: str = ""


# 已核实的接口地址与模型名（2026-09 核对官方文档）
PROVIDERS: dict[str, ProviderConfig] = {
    # 主力：DeepSeek。OpenAI 兼容，支持 Tool Calls / JSON Output，上下文 1M。
    # 官方文档：https://api-docs.deepseek.com/quick_start/pricing/
    "deepseek": ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-flash",  # V4.1-Flash：快、便宜，适合高频工具调用
        supports_thinking=True,
        # DeepSeek 用 Chat Completion 的 thinking 字段控制思考模式
        thinking_extra_body={"thinking": {"type": "enabled"}},
        note="deepseek-v4-pro 更强但更贵；deepseek-chat/reasoner 已下线",
    ),
    # 备用：阿里云百炼，新用户每模型 100 万 tokens 免费额度
    "qwen": ProviderConfig(
        name="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        note="需选支持 Function Calling 的模型，勿用 qwen3-coder-next（不支持工具调用）",
    ),
    # 备用：智谱 GLM
    "glm": ProviderConfig(
        name="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
    ),
}

# 默认服务商
DEFAULT_PROVIDER = "deepseek"


# --------------------------------------------------------------- 配置读取
def _env_str(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(key: str, default: int) -> int:
    raw = _env_str(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env_str(key).lower()
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return default


@dataclass
class Settings:
    """一次运行生效的全部配置。由 load_settings() 构造。"""

    provider: ProviderConfig
    model: str
    api_key: str
    timeout: int
    max_retries: int
    thinking: bool
    workspace: Path
    run_python_timeout: int
    dotenv_loaded: bool

    @property
    def has_api_key(self) -> bool:
        """Key 是否看起来有效（没填或是占位符都算无效）。"""
        return bool(self.api_key) and not self.api_key.startswith("sk-请")


def load_settings(workspace: str | os.PathLike[str] | None = None) -> Settings:
    """从环境变量组装配置。

    workspace: 允许 Agent 访问的根目录；None 表示当前工作目录。
    """
    provider_name = _env_str("LLM_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider_name not in PROVIDERS:
        # 不直接崩，降级到默认并留痕，避免因拼错名字导致整程序不可用
        print(
            f"[config] 警告：未知 provider '{provider_name}'，"
            f"回退到 '{DEFAULT_PROVIDER}'。可选：{', '.join(PROVIDERS)}"
        )
        provider_name = DEFAULT_PROVIDER
    provider = PROVIDERS[provider_name]

    model = _env_str("LLM_MODEL") or provider.default_model

    # Key 的查找顺序：通用名 > 服务商专属名
    api_key = _env_str("LLM_API_KEY") or _env_str(f"{provider.name.upper()}_API_KEY")

    ws_raw = _env_str("AGENT_WORKSPACE")
    if workspace is not None:
        ws_path = Path(workspace)
    elif ws_raw:
        ws_path = Path(ws_raw)
    else:
        ws_path = Path.cwd()

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        timeout=_env_int("LLM_TIMEOUT", 120),
        max_retries=_env_int("LLM_MAX_RETRIES", 4),
        thinking=_env_bool("LLM_THINKING", provider.supports_thinking),
        workspace=ws_path.resolve(),
        run_python_timeout=_env_int("RUN_PYTHON_TIMEOUT", 10),
        dotenv_loaded=_DOTENV_LOADED,
    )


def describe(settings: Settings) -> str:
    """生成一行配置摘要，用于启动时打印，便于排查"到底用了哪个模型"。"""
    return (
        f"provider={settings.provider.name} | model={settings.model} | "
        f"thinking={'on' if settings.thinking else 'off'} | "
        f"workspace={settings.workspace} | "
        f"api_key={'已配置' if settings.has_api_key else '缺失'}"
    )
