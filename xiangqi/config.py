from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用中国象棋插件")
    config_version: str = Field(default="0.5.0", description="配置版本")


class ChessSection(PluginConfigBase):
    request_timeout: float = Field(default=30, ge=1, le=300, description="自然语言解析请求的超时秒数")
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
    enabled: bool = Field(default=True, description="启用引擎自动落子；关闭后保留棋局，不使用 LLM 代走")
    difficulty: int = Field(
        default=3,
        ge=1,
        le=6,
        description="新局默认难度：1入门、2简单、3标准、4困难、5挑战、6全力；名称不是等级认证",
    )
    executable: str = Field(
        default="", description="Fairy-Stockfish largeboard 路径，留空自动使用插件内置引擎"
    )
    cpu: int = Field(default=-1, ge=-1, description="引擎逻辑 CPU，-1 自动选择；不限制 MaiBot 使用该核心")
    movetime_ms: int = Field(default=800, ge=100, le=3000, description="单次搜索毫秒数；所有群共用串行搜索")
    hash_mb: int = Field(default=64, ge=16, le=256, description="引擎哈希表 MB；不等于进程总内存")


class Config(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    chess: ChessSection = Field(default_factory=ChessSection)
    engine: EngineSection = Field(default_factory=EngineSection)
