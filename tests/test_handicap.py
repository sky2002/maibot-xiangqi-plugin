import json
import random

import pytest

from xiangqi.config import EngineSection
from xiangqi.engine import Candidate, select_candidates
from xiangqi.llm import Player
from xiangqi.rules import Board


def ranked(scores):
    choices = Board().choices()
    return [Candidate(c, kind, score, 1, [c.move]) for c, (kind, score) in zip(choices, scores, strict=False)]


@pytest.mark.parametrize("level,min_loss,max_loss", [(1, 100, 600), (2, 50, 300)])
def test_mistake_pool_excludes_good_moves_even_for_perfect_llm(level, min_loss, max_loss):
    pool = ranked([("cp", v) for v in (100, 99, 98, 50, 0, -50, -150, -200, -400, -500, -900)])
    result = select_candidates(pool, EngineSection(difficulty=level), random.Random(1))
    assert 1 <= len(result) <= 3
    assert all(min_loss <= pool[0].score - c.score <= max_loss for c in result)
    assert not {c.choice.move for c in pool[:3]} & {c.choice.move for c in result}


def test_beginner_can_make_larger_mistakes_than_easy():
    pool = ranked([("cp", 100), ("cp", -350)])
    assert select_candidates(pool, EngineSection(difficulty=1), random.Random(1)) == pool[1:]
    assert select_candidates(pool, EngineSection(difficulty=2), random.Random(1)) == pool


@pytest.mark.parametrize("level", [3, 4, 5])
def test_standard_and_above_keep_exact_original_candidates(level):
    pool = ranked([("cp", v) for v in (100, 98, 95, 0, -100)])
    for seed in range(20):
        assert select_candidates(pool, EngineSection(difficulty=level), random.Random(seed)) == pool[:3]


def test_equal_scores_and_forced_reply_do_not_invent_errors():
    pool = ranked([("cp", 10)] * 5)
    assert select_candidates(pool, EngineSection(difficulty=1), random.Random(1)) == pool[:3]
    assert select_candidates(pool[:1], EngineSection(difficulty=2), random.Random(1)) == pool[:1]


def test_mate_scores_are_not_treated_as_centipawns():
    pool = ranked([("mate", 2), ("cp", 200), ("cp", 100), ("cp", -1500), ("mate", -1)])
    result = select_candidates(pool, EngineSection(difficulty=1), random.Random(1))
    assert result == pool[1:3]  # 可以错过杀棋，但不因 mate 分数单位混用而任意大送子。
    pool = ranked([("cp", 0), ("mate", -1), ("mate", -2)])
    assert select_candidates(pool, EngineSection(difficulty=1), random.Random(1)) == pool[:1]
    pool = ranked([("mate", -10), ("mate", -2)])
    assert select_candidates(pool, EngineSection(difficulty=1), random.Random(1)) == pool


def test_handicap_occurs_often_without_becoming_every_move():
    pool = ranked([("cp", v) for v in (100, 99, 98, 0, -20, -40)])
    counts = {}
    for level in (1, 2):
        rng = random.Random(2026)
        counts[level] = sum(
            select_candidates(pool, EngineSection(difficulty=level), rng)[0].score <= 0 for _ in range(1000)
        )
    assert 780 < counts[1] < 920
    assert 520 < counts[2] < 680
    assert counts[1] > counts[2]


async def test_llm_cannot_recover_excluded_best_move(service):
    pool = ranked([("cp", v) for v in (100, 99, 98, 0, -20, -40)])
    offered = select_candidates(pool, EngineSection(difficulty=2), random.Random(1))
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id":1}'}
    move = await Player(service.ctx, service.settings).select(Board(), [], offered)
    assert move == offered[0].choice and move != pool[0].choice
    prompt = json.loads(service.ctx.llm.generate.call_args.kwargs["prompt"][1]["content"])
    assert {c["move"] for c in prompt["legal_moves"]} == {c.choice.move for c in offered}
    assert all(c["score"] <= 0 for c in prompt["engine_analysis"])
