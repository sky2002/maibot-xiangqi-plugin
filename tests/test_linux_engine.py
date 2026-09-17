"""CI 经 run_isolated.py 启动，验证真实 Linux 亲和性和引擎。"""

import asyncio
import json
import os
import sys

import pytest

from xiangqi.config import EngineSection
from xiangqi.engine import Engine
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


async def test_real_isolated_engine_search():
    board = Board()
    engine = Engine()
    for _ in range(2):
        result = await engine.analyse(board, EngineSection())
        assert len(result) == 3 and all(c.choice.move in board.legal_moves() for c in result)
        board.push(result[0].choice.move)
