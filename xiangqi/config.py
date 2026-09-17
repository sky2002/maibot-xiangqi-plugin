from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用中国象棋插件")
    config_version: str = Field(default="0.2.0", description="配置版本")


class ChessSection(PluginConfigBase):
    model_name: str = Field(default="", description="下棋模型名称，留空使用宿主 utils；不是 API 密钥")
    request_timeout: float = Field(default=30, ge=1, le=300, description="自然语言解析请求的超时秒数")
    move_timeout: float = Field(
        default=120, ge=1, le=300, description="每次 bot 选招请求的超时秒数，思考型模型可适当增加"
    )
    idle_minutes: int = Field(default=30, ge=1, le=10080, description="无有效操作多少分钟后结束，不计胜负")
    repetition: int = Field(default=3, ge=2, le=10, description="相同局面出现几次后和棋")
    no_capture_halfmoves: int = Field(default=120, ge=2, le=1000, description="连续多少个半回合无吃子后和棋")
    commentary: bool = Field(default=True, description="在棋盘消息中附上简短人设棋评")
    auto_chat: bool = Field(default=True, description="对局中自动回应棋手的棋局聊天，无需聊天指令")
    chat_cooldown: int = Field(default=15, ge=1, le=300, description="同群自动聊天请求的最短间隔秒数")
    admins: List[str] = Field(
        default_factory=list, description="可强制结束的管理员，格式：平台:群号:用户号，例如 qq:123:456"
    )


class EngineSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="引擎给候选、LLM 拍板；关闭后使用原纯 LLM 模式")
    executable: str = Field(default="", description="Fairy-Stockfish largeboard 路径，留空使用安装器默认位置")
    candidates: int = Field(default=3, ge=1, le=5, description="交给 LLM 的引擎候选数")
    movetime_ms: int = Field(default=800, ge=100, le=3000, description="单次搜索毫秒数；所有群共用串行搜索")
    hash_mb: int = Field(default=64, ge=16, le=256, description="引擎哈希表 MB；不等于进程总内存")


class Config(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    chess: ChessSection = Field(default_factory=ChessSection)
    engine: EngineSection = Field(default_factory=EngineSection)
