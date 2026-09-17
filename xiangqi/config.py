from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用中国象棋插件")
    config_version: str = Field(default="0.1.0", description="配置版本")


class ChessSection(PluginConfigBase):
    model_name: str = Field(default="", description="下棋模型名称，留空使用宿主 utils；不是 API 密钥")
    request_timeout: float = Field(default=30, ge=1, le=300, description="每次 LLM 请求的超时秒数")
    idle_minutes: int = Field(default=30, ge=1, le=10080, description="无有效操作多少分钟后结束，不计胜负")
    repetition: int = Field(default=3, ge=2, le=10, description="相同局面出现几次后和棋")
    no_capture_halfmoves: int = Field(default=120, ge=2, le=1000, description="连续多少个半回合无吃子后和棋")
    commentary: bool = Field(default=True, description="在棋盘消息中附上简短人设棋评")
    admins: List[str] = Field(
        default_factory=list, description="可强制结束的管理员，格式：平台:群号:用户号，例如 qq:123:456"
    )


class Config(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    chess: ChessSection = Field(default_factory=ChessSection)
