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


@pytest.mark.parametrize("response", ['{"id":true}', '{"id":1.2}', "I'll play a rook", '{"id":0}'])
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
    p.settings.move_timeout = 1
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


async def test_selection_accepts_single_json_answer_with_explanatory_wrapper():
    response = '我选择这一步：\n```json\n{"id": 1}\n```'
    p, llm = player([{"success": True, "response": response}] * 2)
    choice = await p.select(Board(), [])
    assert choice == Board().choices()[0]
    assert llm.await_count == 1


async def test_transport_timeout_is_not_reported_as_invalid_move():
    p, _ = player([TimeoutError(), TimeoutError()])
    with pytest.raises(ModelFailure, match="超时"):
        await p.select(Board(), [])


@pytest.mark.parametrize(
    "response",
    [
        '{"id":"1"}',
        '最终选择：{"id": 1}。',
        '<think>可能选 {"id": 2}，需要再比较。</think>\n{"id": 1}',
        '```JSON\n{"id":1}\n```',
    ],
)
async def test_unambiguous_answer_formats(response):
    p, llm = player([{"success": True, "response": response}] * 2)
    assert await p.select(Board(), []) == Board().choices()[0]
    assert llm.await_count == 1


@pytest.mark.parametrize(
    "response",
    [
        '{"id":1}\n{"id":2}',
        '```json\n{"id":1}\n```\n```json\n{"id":2}\n```',
        '{"id":1,"id":2}',
        '<think>还在考虑 {"id":1}',
        '说明：<think>{"id":1}</think>',
        '<think><think>{"id":2}</think>{"id":1}</think>',
        '[{"id":1}]',
        '{"answer":{"id":1}}',
        "这一步的得分为 1",
        '```json\n{"id":1}\n``` 另一个选择 {"id":2}',
    ],
)
async def test_conflicting_or_unfinished_answers_never_choose_a_move(response):
    p, _ = player([{"success": True, "response": response}] * 2)
    with pytest.raises(ModelFailure):
        await p.select(Board(), [])


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"success": False, "error": "HTTP 401 unauthorized"}, "鉴权"),
        ({"success": False, "error": "HTTP 429 too many requests"}, "限流"),
        ({"success": False, "error": "upstream timed out"}, "超时"),
        ({"success": False, "error": "model not configured"}, "接口"),
        ({"success": True, "response": "", "reasoning": "hidden reasoning"}, "最终答案"),
        ({"success": True, "response": ""}, "空内容"),
    ],
)
async def test_failure_keeps_actionable_reason(payload, reason):
    p, _ = player([payload] * 2)
    with pytest.raises(ModelFailure, match=reason):
        await p.select(Board(), [])


async def test_retry_explains_id_range():
    p, llm = player([{"success": True, "response": '{"id":999}'}, {"success": True, "response": '{"id":1}'}])
    await p.select(Board(), [])
    second = llm.call_args.kwargs["prompt"]
    data = json.loads(second[1]["content"])
    assert "1" in data["retry_feedback"] and "44" in data["retry_feedback"]


async def test_diagnostics_never_expose_provider_secrets_or_reasoning(caplog):
    secret = "private-token-for-test"
    p, _ = player(
        [
            {
                "success": False,
                "error": f"HTTP 401 Authorization: Bearer {secret}",
                "reasoning": "hidden-thought",
            }
        ]
        * 2
    )
    with pytest.raises(ModelFailure) as exc:
        await p.select(Board(), [])
    assert "鉴权" in str(exc.value)
    assert secret not in str(exc.value) + caplog.text
    assert "hidden-thought" not in caplog.text
    assert "[xiangqi.llm]" in caplog.text and "auth_error" in caplog.text


async def test_reasoning_channel_is_never_used_as_the_move():
    p, _ = player([{"success": True, "response": "", "reasoning": '{"id":1}'}] * 2)
    with pytest.raises(ModelFailure, match="最终答案"):
        await p.select(Board(), [])


async def test_selection_uses_longer_deadline_than_parser():
    p, llm = player([], request_timeout=1, move_timeout=2)

    async def slow_final_answer(**kwargs):
        await asyncio.sleep(1.1)
        return {"success": True, "response": '{"id":1}'}

    llm.side_effect = slow_final_answer
    assert await p.select(Board(), []) == Board().choices()[0]
    assert llm.await_count == 1
    assert llm.call_args.kwargs["timeout_ms"] == 3000


async def test_parser_keeps_its_own_deadline():
    p, llm = player(
        [{"success": True, "response": '{"ids":[],"ambiguous":true}'}], request_timeout=7, move_timeout=120
    )
    await p.interpret(Board(), "帮我走棋")
    assert llm.call_args.kwargs["timeout_ms"] == 8000
