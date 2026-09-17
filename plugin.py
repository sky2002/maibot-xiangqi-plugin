"""MaiBot SDK 2.8 中国象棋插件入口。"""

from typing import Any, Dict, Optional

from maibot_sdk import Command, MaiBotPlugin

from .xiangqi.config import Config
from .xiangqi.service import Service


class XiangqiPlugin(MaiBotPlugin):
    config_model = Config

    def __init__(self) -> None:
        super().__init__()
        self.service: Optional[Service] = None

    async def on_load(self) -> None:
        self.service = Service(self.ctx, self.ctx.paths.data_dir, self.config.chess)
        await self.service.start()

    async def on_unload(self) -> None:
        if self.service:
            await self.service.close()
            self.service = None

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        if self.service:
            self.service.settings = self.config.chess

    @Command(
        "xiangqi",
        description="与 maibot 下中国象棋，发送「下棋 帮助」查看指令",
        pattern=r"^下棋(?:\s+(?P<instruction>[\s\S]*))?\s*$",
    )
    async def handle_chess(
        self, stream_id: str = "", group_id: str = "", platform: str = "", user_id: str = "", **kwargs: Any
    ):
        if not self.config.plugin.enabled or self.service is None:
            return False, "象棋插件未启用", False
        groups = kwargs.get("matched_groups")
        instruction = str(groups.get("instruction") or "") if isinstance(groups, dict) else ""
        if not instruction and isinstance(kwargs.get("text"), str):
            instruction = kwargs["text"].removeprefix("下棋").strip()
        message = kwargs.get("message")
        message_id = str(message.get("message_id") or "") if isinstance(message, dict) else ""
        await self.service.handle(
            stream_id,
            group_id,
            platform,
            user_id,
            instruction,
            message_id,
            kwargs.get("is_local_operator") is True,
        )
        # 第三个值在宿主 Command 桥接中表示拦截后续聊天处理。
        return True, "象棋指令已处理", True


def create_plugin() -> XiangqiPlugin:
    return XiangqiPlugin()
