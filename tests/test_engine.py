from unittest.mock import AsyncMock

import asyncio
import os
import sys

import pytest

from xiangqi.config import EngineSection
from xiangqi.difficulty import LEVELS
from xiangqi.engine import Engine, EngineFailure, move_from_info
from xiangqi.rules import Board, native_move


def info(move, depth=5, index=1, score=25):
    return f"info depth {depth} seldepth 6 multipv {index} score cp {score} nodes 20 pv {native_move(move)}"


def test_native_bestmove_wins_over_multipv_ranking():
    board = Board()
    board.push("b2e2")
    choices = [c for c in board.choices() if c.move[1] == "9"][:3]
    lines = [info(c.move, index=i, score=100 - i * 50) for i, c in enumerate(choices, 1)]
    result = move_from_info(board, lines, native_move(choices[2].move))
    assert result.choice == choices[2]
    assert result.score == -50 and result.depth == 5
    assert "10" in native_move(choices[2].move)


@pytest.mark.parametrize("best", ["(none)", "a1a10", "bad", "b1b9"])
def test_illegal_bestmove_is_rejected(best):
    with pytest.raises(EngineFailure):
        move_from_info(Board(), [], best)


def test_missing_or_bound_analysis_does_not_invent_score_or_block_legal_move():
    result = move_from_info(Board(), ["info depth 2 score cp 40 lowerbound pv b1c3"], "b1c3")
    assert result.choice.move == "b0c2"
    assert "score" not in result.evidence()
    different = move_from_info(Board(), [info("b0c2")], "h1g3")
    assert different.choice.move == "h0g2" and "score" not in different.evidence()


async def test_global_engine_searches_are_serial(monkeypatch, fake_uci):
    active = 0

    async def fake_search(*args):
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0.01)
        active -= 1
        return []

    monkeypatch.setattr("xiangqi.engine.engine_command", fake_uci)
    monkeypatch.setattr("xiangqi.engine._analyse", fake_search)
    engine = Engine()
    try:
        await engine.start(EngineSection())
        await asyncio.gather(*(engine.analyse(Board(), EngineSection()) for _ in range(4)))
    finally:
        await engine.close()


async def test_engine_isolation_failure_never_starts_process(monkeypatch):
    from xiangqi.isolation import IsolationError

    def reject(*_):
        raise IsolationError("未隔离")

    launch = AsyncMock()
    monkeypatch.setattr("xiangqi.engine.engine_command", reject)
    monkeypatch.setattr("xiangqi.engine._analyse", launch)
    with pytest.raises(EngineFailure, match="未隔离"):
        await Engine().analyse(Board(), EngineSection())
    launch.assert_not_called()


async def test_cancel_kills_and_reaps_engine(monkeypatch):
    created = asyncio.Event()
    process = None
    real_create = asyncio.create_subprocess_exec

    async def create(*args, **kwargs):
        nonlocal process
        process = await real_create(*args, **kwargs)
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "xiangqi.engine.engine_command", lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    engine = Engine()
    task = asyncio.create_task(engine.start(EngineSection()))
    await asyncio.wait_for(created.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert process.returncode is not None
    await engine.close()


@pytest.mark.skipif(not os.environ.get("XIANGQI_TEST_ENGINE"), reason="可选真实引擎验证")
@pytest.mark.parametrize("difficulty", LEVELS)
async def test_real_engine_red_and_black_moves(difficulty, monkeypatch):
    monkeypatch.setattr("xiangqi.engine.engine_command", lambda *args: [os.environ["XIANGQI_TEST_ENGINE"]])
    board = Board()
    engine = Engine()
    try:
        for _ in range(2):
            found = await engine.analyse(board, EngineSection(difficulty=difficulty))
            assert found.choice.move in board.legal_moves()
            future = Board(board.fen)
            for move in found.pv:
                future.push(move)
            board.push(found.choice.move)
    finally:
        await engine.close()
