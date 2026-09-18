from unittest.mock import AsyncMock

import asyncio
import os
import random
import sys

import pytest

from xiangqi.config import EngineSection
from xiangqi.difficulty import LEVELS
from xiangqi.engine import Engine, EngineFailure, candidates_from_info, select_candidates
from xiangqi.rules import Board, native_move


def info(move, depth=5, index=1, score=25):
    return f"info depth {depth} seldepth 6 multipv {index} score cp {score} nodes 20 pv {native_move(move)}"


def test_engine_uses_completed_multipv_depth_and_converts_rank_ten():
    board = Board()
    board.push("b2e2")
    choices = [c for c in board.choices() if c.move[1] == "9"][:3]
    lines = [info(c.move, index=i) for i, c in enumerate(choices, 1)]
    lines += [info(choices[0].move, depth=6, score=500)]
    result = candidates_from_info(board, lines, native_move(choices[0].move), 3)
    assert [c.choice for c in result] == choices
    assert all(c.depth == 5 and c.score == 25 for c in result)
    assert "10" in native_move(choices[0].move)


@pytest.mark.parametrize(
    "best,lines",
    [
        ("(none)", []),
        ("a1a10", []),
        ("b1c3", ["info depth 2 multipv 1 score cp 40 lowerbound pv b1c3"]),
        ("b1c3", ["info depth 2 multipv 1 score cp 40 nodes 1 pv a1a10"]),
    ],
)
def test_bad_or_incomplete_engine_output_rejected(best, lines):
    with pytest.raises(EngineFailure):
        candidates_from_info(Board(), lines, best, 1)


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
async def test_real_engine_red_and_black_candidates(difficulty, monkeypatch):
    monkeypatch.setattr("xiangqi.engine.engine_command", lambda *args: [os.environ["XIANGQI_TEST_ENGINE"]])
    board = Board()
    engine = Engine()
    try:
        for _ in range(2):
            found = await engine.analyse(board, EngineSection(difficulty=difficulty))
            assert 1 <= len(found) <= 3
            assert len({c.choice.move for c in found}) == len(found)
            if not LEVELS[difficulty].mistake_rate:
                assert len(found) == 3
            for c in found:
                if LEVELS[difficulty].depth:
                    assert c.depth <= LEVELS[difficulty].depth
                assert c.choice.move in board.legal_moves()
                future = Board(board.fen)
                for move in c.pv:
                    future.push(move)
            board.push(found[-1].choice.move)
    finally:
        await engine.close()


@pytest.mark.skipif(not os.environ.get("XIANGQI_TEST_ENGINE"), reason="可选真实引擎验证")
@pytest.mark.parametrize("difficulty", [1, 2, 3, 4])
async def test_real_handicap_excludes_best_move_and_meets_loss_target(difficulty, monkeypatch):
    evaluated = []

    def select(ranked, settings):
        evaluated.extend(ranked)
        return select_candidates(ranked, settings, random.Random(1))

    monkeypatch.setattr("xiangqi.engine.select_candidates", select)
    board = Board()
    monkeypatch.setattr("xiangqi.engine.engine_command", lambda *args: [os.environ["XIANGQI_TEST_ENGINE"]])
    engine = Engine()
    try:
        result = await engine.analyse(board, EngineSection(difficulty=difficulty))
    finally:
        await engine.close()
    assert len(evaluated) == len(board.legal_moves())
    assert all(c.score_kind == "cp" for c in result)
    assert all(evaluated[0].score - c.score >= LEVELS[difficulty].min_loss for c in result)
    assert evaluated[0].choice.move not in {c.choice.move for c in result}
    if difficulty <= 2:
        assert not {c.choice.move for c in result} & {c.choice.move for c in evaluated[:3]}
