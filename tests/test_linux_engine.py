"""CI 普通启动，验证真实 Linux 绑核、宿主调度和引擎。"""

import asyncio
import json
import os
import platform
import random
import sys

import pytest

from xiangqi.config import EngineSection
from xiangqi.difficulty import LEVELS
from xiangqi.engine import Engine, select_candidates
from xiangqi.isolation import engine_command
from xiangqi.rules import Board


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"),
    reason="需要 Linux x86_64，使用插件内置引擎",
)


async def test_child_is_pinned_and_host_affinity_unchanged():
    before = os.sched_getaffinity(0)
    cpu = max(before)
    command = engine_command(sys.executable)
    process = await asyncio.create_subprocess_exec(
        *command,
        "-c",
        "import os,json;print(json.dumps(sorted(os.sched_getaffinity(0))))",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    assert process.returncode == 0
    assert json.loads(stdout) == [cpu]
    assert os.sched_getaffinity(0) == before


@pytest.mark.parametrize("difficulty", LEVELS)
async def test_real_plugin_managed_engine_search(difficulty, monkeypatch, tmp_path):
    board = Board()
    engine = Engine(tmp_path)
    evaluated = []

    def select(ranked, settings):
        evaluated[:] = ranked
        return select_candidates(ranked, settings, random.Random(1))

    monkeypatch.setattr("xiangqi.engine.select_candidates", select)
    await engine.start(EngineSection(difficulty=difficulty))
    try:
        for _ in range(2):
            result = await engine.analyse(board, EngineSection(difficulty=difficulty))
            assert 1 <= len(result) <= 3 and all(c.choice.move in board.legal_moves() for c in result)
            if not LEVELS[difficulty].mistake_rate:
                assert len(result) == 3
            else:
                assert len(evaluated) == len(board.legal_moves())
                if len(board.legal_moves()) == 44 and board.red_turn:
                    assert all(evaluated[0].score - c.score >= LEVELS[difficulty].min_loss for c in result)
            if LEVELS[difficulty].depth:
                assert all(c.depth <= LEVELS[difficulty].depth for c in result)
            board.push(result[0].choice.move)
    finally:
        await engine.close()
