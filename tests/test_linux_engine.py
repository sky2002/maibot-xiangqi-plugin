"""CI 经 run_isolated.py 启动，验证真实 Linux 亲和性和引擎。"""

import asyncio
import json
import os
import random
import sys

import pytest

from xiangqi.config import EngineSection
from xiangqi.difficulty import LEVELS
from xiangqi.engine import Engine, select_candidates
from xiangqi.isolation import engine_command, topology
from xiangqi.rules import Board


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not os.environ.get("MAIBOT_XIANGQI_ENGINE_CPU"),
    reason="需要经隔离启动器运行的 Linux 环境",
)


async def test_taskset_child_and_host_have_disjoint_physical_cores():
    cpu = int(os.environ["MAIBOT_XIANGQI_ENGINE_CPU"])
    assert os.sched_getaffinity(0).isdisjoint(topology({cpu})[cpu])
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


@pytest.mark.parametrize("difficulty", LEVELS)
async def test_real_isolated_engine_search(difficulty, monkeypatch):
    board = Board()
    engine = Engine()
    evaluated = []

    def select(ranked, settings):
        evaluated[:] = ranked
        return select_candidates(ranked, settings, random.Random(1))

    monkeypatch.setattr("xiangqi.engine.select_candidates", select)
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
