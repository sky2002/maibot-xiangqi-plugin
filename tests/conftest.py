from types import SimpleNamespace
from unittest.mock import AsyncMock

import logging

import pytest

from xiangqi.config import ChessSection, EngineSection
from xiangqi.service import Service


@pytest.fixture
async def service(tmp_path, monkeypatch):
    # 流程测试隔离绘图耗时；真实字体/PNG 另有单独集成测试。
    monkeypatch.setattr("xiangqi.service.render_board", lambda *args: b"test-png")
    ctx = SimpleNamespace(
        logger=logging.getLogger("xiangqi-test"),
        send=SimpleNamespace(text=AsyncMock(return_value=True), image=AsyncMock(return_value=True)),
        config=SimpleNamespace(get=AsyncMock(return_value="")),
        llm=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "response": '{"id": 1}'})),
    )
    instance = Service(ctx, tmp_path, ChessSection(commentary=False), EngineSection(enabled=False))
    await instance.start()
    yield instance
    await instance.close()
