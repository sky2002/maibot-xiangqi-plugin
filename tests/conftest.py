from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import logging
import sys

import pytest

from xiangqi.config import ChessSection, EngineSection
from xiangqi.engine import EngineMove
from xiangqi.service import Service


@pytest.fixture
def child_processes(monkeypatch):
    processes = []
    create = asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked)
    return processes


@pytest.fixture
def fake_uci(tmp_path):
    """以真实 Python 子进程模拟 UCI；仍走实际管道、取消和进程回收。"""
    script = tmp_path / "fake_uci.py"
    script.write_text(
        """
from pathlib import Path
import os
import sys
import time

log, mode = Path(sys.argv[1]), sys.argv[2]
for line in sys.stdin:
    line = line.strip()
    with log.open("a", encoding="utf-8") as output:
        output.write(f"{os.getpid()} {line}\\n")
    if line == "uci":
        if mode != "bad":
            for name in ("Threads", "Hash", "MultiPV", "UCI_Variant", "Use NNUE", "Skill Level", "UCI_LimitStrength"):
                print(f"option name {name} type combo var xiangqi")
        print("uciok", flush=True)
    elif line == "isready":
        print("readyok", flush=True)
    elif line.startswith("go "):
        if mode == "hang":
            time.sleep(30)
        if mode == "crash":
            sys.exit(1)
        print("info depth 1 multipv 1 score cp 25 nodes 1 pv b1c3")
        print("bestmove h1g3" if mode == "weaker" else "bestmove b1c3", flush=True)
""",
        encoding="utf-8",
    )

    def command(executable="", cpu=-1, data_dir=None):
        return [sys.executable, "-u", str(script), str(tmp_path / "uci.log"), executable]

    return command


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
    instance.engine_settings.enabled = True

    async def choose(board, settings):
        return EngineMove(board.choices()[0])

    instance.engine.analyse = AsyncMock(side_effect=choose)
    yield instance
    await instance.close()
