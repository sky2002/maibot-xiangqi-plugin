import asyncio

import pytest

from xiangqi.config import EngineSection
from xiangqi.engine import Engine, EngineFailure
from xiangqi.rules import Board


@pytest.fixture
def engine(monkeypatch, fake_uci):
    monkeypatch.setattr("xiangqi.engine.engine_command", fake_uci)
    return Engine()


async def test_start_search_reuse_and_close(engine, child_processes, tmp_path):
    settings = EngineSection(difficulty=6)
    await engine.start(settings)
    try:
        assert len(child_processes) == 1 and child_processes[0].returncode is None
        await engine.start(settings)
        for _ in range(2):
            result = await engine.analyse(Board(), settings)
            assert result.choice.move == "b0c2"
        assert len(child_processes) == 1
        log = (tmp_path / "uci.log").read_text()
        assert log.count(" uci\n") == 1
        assert log.count(" ucinewgame\n") == 2
        assert "setoption name Threads value 1" in log
        assert "setoption name Ponder value false" in log
    finally:
        await engine.close()
    await engine.close()
    assert child_processes[0].returncode is not None
    with pytest.raises(EngineFailure, match="卸载"):
        await engine.analyse(Board(), settings)


async def test_failed_configuration_keeps_previous_process(engine, child_processes):
    settings = EngineSection(difficulty=6)
    await engine.start(settings)
    try:
        with pytest.raises(EngineFailure, match="不兼容"):
            await engine.start(settings.model_copy(update={"executable": "bad"}))
        assert child_processes[0].returncode is None
        assert child_processes[1].returncode is not None
        await engine.analyse(Board(), settings)
        assert len(child_processes) == 2
        await engine.start(settings.model_copy(update={"cpu": 2, "executable": "new"}))
        assert child_processes[0].returncode is not None
        assert child_processes[-1].returncode is None
        await engine.start(EngineSection(enabled=False))
        assert child_processes[-1].returncode is not None
        await engine.start(settings)
        assert child_processes[-1].returncode is None
    finally:
        await engine.close()


@pytest.mark.parametrize("mode", ["hang", "crash"])
async def test_interrupted_search_reaps_and_next_request_recovers(engine, child_processes, monkeypatch, mode):
    from xiangqi.uci import UciProcess

    searching = asyncio.Event()
    send = UciProcess.send

    async def tracked_send(process, line):
        await send(process, line)
        if line.startswith("go "):
            searching.set()

    monkeypatch.setattr(UciProcess, "send", tracked_send)
    settings = EngineSection(executable=mode, difficulty=6)
    await engine.start(settings)
    try:
        task = asyncio.create_task(engine.analyse(Board(), settings))
        if mode == "hang":
            await asyncio.wait_for(searching.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(EngineFailure, match="提前退出"):
                await task
        assert child_processes[0].returncode is not None
        recovered = settings.model_copy(update={"executable": ""})
        await engine.start(recovered)
        await engine.analyse(Board(), recovered)
        assert len(child_processes) == 2
    finally:
        await engine.close()


async def test_disabled_engine_never_launches(engine, child_processes):
    await engine.start(EngineSection(enabled=False))
    await engine.close()
    assert child_processes == []


async def test_search_timeout_reaps_process(engine, child_processes, monkeypatch):
    settings = EngineSection(executable="hang", difficulty=6)
    await engine.start(settings)
    timeout = asyncio.timeout
    monkeypatch.setattr("xiangqi.engine.asyncio.timeout", lambda seconds: timeout(0.1))
    try:
        with pytest.raises(EngineFailure, match="响应超时"):
            await engine.analyse(Board(), settings)
        assert child_processes[0].returncode is not None
    finally:
        await engine.close()


async def test_queued_old_request_cannot_undo_configuration(engine, child_processes):
    old_settings = EngineSection(difficulty=6)
    await engine.start(old_settings)
    try:
        await engine.start(old_settings.model_copy(update={"executable": "new"}))
        await engine.analyse(Board(), old_settings)
        assert len(child_processes) == 2
        assert child_processes[0].returncode is not None
        assert child_processes[1].returncode is None
        await engine.start(EngineSection(enabled=False))
        with pytest.raises(EngineFailure, match="未启用"):
            await engine.analyse(Board(), old_settings)
        assert len(child_processes) == 2
        assert child_processes[1].returncode is not None
    finally:
        await engine.close()
