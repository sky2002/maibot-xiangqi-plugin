import pytest

from xiangqi.config import EngineSection
from xiangqi.difficulty import LEVELS, parse_level
from xiangqi.engine import Engine
from xiangqi.rules import Board


async def test_each_search_applies_its_own_skill_and_never_reselects_bestmove(
    monkeypatch, fake_uci, tmp_path
):
    monkeypatch.setattr("xiangqi.engine.engine_command", fake_uci)
    engine = Engine()
    try:
        # 最终走法故意不在输出的 MultiPV 首位，模拟原生降强。
        for level in (1, 6, 2, 5, 3, 4):
            result = await engine.analyse(Board(), EngineSection(executable="weaker", difficulty=level))
            assert result.choice.move == "h0g2"
            assert "score" not in result.evidence()
    finally:
        await engine.close()
    lines = [line.split(" ", 1)[1] for line in (tmp_path / "uci.log").read_text().splitlines()]
    skills = [line for line in lines if line.startswith("setoption name Skill Level")]
    assert skills[1:] == [
        f"setoption name Skill Level value {LEVELS[level].skill}" for level in (1, 6, 2, 5, 3, 4)
    ]
    assert [line for line in lines if line.startswith("go ")] == ["go movetime 800"] * 6
    assert lines.count("setoption name MultiPV value 1") == 6


def test_legacy_difficulty_name_is_accepted():
    assert parse_level("超人类") == parse_level("全力") == 6


@pytest.mark.parametrize("level,skill", [(1, -20), (2, -12), (3, -4), (4, 4), (5, 12), (6, 20)])
def test_initial_skill_mapping(level, skill):
    assert LEVELS[level].skill == skill
