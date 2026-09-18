"""MaiBot SDK 2.8 中国象棋插件入口。"""

from typing import Any, Dict, Optional

from maibot_sdk import Command, HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode

from .xiangqi.config import Config
from .xiangqi.service import Service


class XiangqiPlugin(MaiBotPlugin):
    config_model = Config

    def __init__(self) -> None:
        super().__init__()
        self.service: Optional[Service] = None

    async def on_load(self) -> None:
        if self.service is not None or not self.config.plugin.enabled:
            return
        service = Service(self.ctx, self.ctx.paths.data_dir, self.config.chess, self.config.engine)
        try:
            await service.start()
        except BaseException:
            await service.close()
            raise
        self.service = service
        self.ctx.logger.info("象棋插件已启动，引擎生命周期由插件系统管理")

    async def on_unload(self) -> None:
        if self.service:
            service, self.service = self.service, None
            await service.close()

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        if self.service:
            await self.service.configure(self.config.chess, self.config.engine)

    @HookHandler(
        "chat.receive.after_process",
        name="xiangqi_auto_chat",
        mode=HookMode.BLOCKING,
        timeout_ms=20000,
        error_policy=ErrorPolicy.SKIP,
        description="对局中自动回应棋手的棋局聊天",
    )
    async def handle_chat(self, message=None, **kwargs: Any):
        if not self.config.plugin.enabled or self.service is None or not isinstance(message, dict):
            return {"action": "continue"}
        if message.get("is_command") or message.get("is_notify"):
            return {"action": "continue"}
        info = message.get("message_info")
        if not isinstance(info, dict):
            return {"action": "continue"}
        group, user = info.get("group_info"), info.get("user_info")
        text = message.get("processed_plain_text")
        if not isinstance(group, dict) or not isinstance(user, dict) or not isinstance(text, str):
            return {"action": "continue"}
        replied = await self.service.chat(
            str(message.get("session_id") or ""),
            str(group.get("group_id") or ""),
            str(message.get("platform") or ""),
            str(user.get("user_id") or ""),
            text,
            str(message.get("message_id") or ""),
        )
        return {"action": "abort" if replied else "continue"}

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
