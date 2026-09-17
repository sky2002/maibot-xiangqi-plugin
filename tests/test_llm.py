from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json

import pytest

from xiangqi.config import ChessSection
from xiangqi.llm import ModelFailure, Player
from xiangqi.rules import Board


def player(responses, **options):
    llm = AsyncMock(side_effect=responses)
    ctx = SimpleNamespace(
        llm=SimpleNamespace(generate=llm), config=SimpleNamespace(get=AsyncMock(return_value="人设"))
    )
    return Player(ctx, ChessSection(**options)), llm


async def test_selection_retries_invalid_number_and_routes_model():
    p, llm = player(
        [
            {"success": True, "response": '{"id": 999}'},
            {"success": True, "response": '```json\n{"id": 1}\n```'},
        ],
        model_name="chess-model",
    )
    result = await p.select(Board(), [])
    assert result.move in Board().legal_moves()
    assert llm.await_count == 2
    assert llm.call_args.kwargs["model_name"] == "chess-model"
    data = json.loads(llm.call_args.kwargs["prompt"][1]["content"])
    assert data["history"] == [] and len(data["legal_moves"]) == 44
    assert "instruction" not in data and "personality" not in data


@pytest.mark.parametrize(
    "response", ['{"id":true}', '{"id":1.2}', '{"id":"1"}', "I'll play a rook", '{"id":0}']
)
async def test_invalid_selection_never_falls_back(response):
    p, llm = player([{"success": True, "response": response}] * 2)
    with pytest.raises(ModelFailure):
        await p.select(Board(), [])
    assert llm.await_count == 2


async def test_timeout_retries_once():
    p, llm = player([])

    async def hang(**kwargs):
        await asyncio.Event().wait()

    llm.side_effect = hang
    p.settings.request_timeout = 1
    with pytest.raises(ModelFailure):
        await p.select(Board(), [])
    assert llm.await_count == 2


async def test_parser_returns_candidates_and_does_not_use_chess_override():
    p, llm = player(
        [{"success": True, "response": '{"ids":[1,2],"ambiguous":false}'}], model_name="chess-model"
    )
    candidates, ambiguous = await p.interpret(Board(), "左边的车走一步")
    assert len(candidates) == 2 and ambiguous
    assert llm.call_args.kwargs["model_name"] == ""
    data = json.loads(llm.call_args.kwargs["prompt"][1]["content"])
    assert data["instruction"] == "左边的车走一步"
    assert "history" not in data


async def test_parser_can_decline_to_guess():
    p, _ = player([{"success": True, "response": '{"ids":[],"ambiguous":true}'}])
    assert await p.interpret(Board(), "帮我走最好的一步") == ([], True)


async def test_commentary_off_does_not_read_persona_or_call_llm():
    p, llm = player([], commentary=False)
    assert await p.comment(Board(), Board().choices()[0]) == ""
    llm.assert_not_called()
    p.ctx.config.get.assert_not_called()
