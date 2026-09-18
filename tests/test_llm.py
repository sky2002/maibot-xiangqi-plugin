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


async def test_interpret_retries_invalid_number_without_choosing_for_player():
    p, llm = player(
        [
            {"success": True, "response": '{"ids":[999],"ambiguous":false}'},
            {"success": True, "response": '```json\n{"ids":[1],"ambiguous":false}\n```'},
        ]
    )
    choices, ambiguous = await p.interpret(Board(), "左边的车走一步")
    assert choices == [Board().choices()[0]] and not ambiguous
    assert llm.await_count == 2 and llm.call_args.kwargs["model_name"] == ""
    data = json.loads(llm.call_args.kwargs["prompt"][1]["content"])
    assert "1" in data["retry_feedback"] and "44" in data["retry_feedback"]


@pytest.mark.parametrize(
    "response",
    [
        '{"ids":[true],"ambiguous":false}',
        '{"ids":[1.2],"ambiguous":false}',
        '{"ids":[0],"ambiguous":false}',
        '{"ids":[1],"ambiguous":"false"}',
        '{"ids":[1],"ids":[2],"ambiguous":false}',
        '{"ids":[1],"ambiguous":false} {"ids":[2],"ambiguous":false}',
        '<think>还在考虑 {"ids":[1]}',
        '[{"ids":[1],"ambiguous":false}]',
    ],
)
async def test_invalid_interpretation_never_guesses(response):
    p, llm = player([{"success": True, "response": response}] * 2)
    with pytest.raises(ModelFailure):
        await p.interpret(Board(), "测试指令")
    assert llm.await_count == 2


async def test_timeout_retries_once():
    p, llm = player([], request_timeout=1)

    async def hang(**kwargs):
        await asyncio.Event().wait()

    llm.side_effect = hang
    with pytest.raises(ModelFailure, match="超时"):
        await p.interpret(Board(), "测试指令")
    assert llm.await_count == 2


async def test_multiple_matches_stay_ambiguous():
    p, llm = player([{"success": True, "response": '{"ids":[1,2],"ambiguous":false}'}])
    candidates, ambiguous = await p.interpret(Board(), "左边的车走一步")
    assert len(candidates) == 2 and ambiguous
    data = json.loads(llm.call_args.kwargs["prompt"][1]["content"])
    assert data["instruction"] == "左边的车走一步" and "history" not in data


async def test_parser_can_decline_to_guess():
    p, _ = player([{"success": True, "response": '{"ids":[],"ambiguous":true}'}])
    assert await p.interpret(Board(), "帮我走最好的一步") == ([], True)


async def test_commentary_off_does_not_read_persona_or_call_llm():
    p, llm = player([], commentary=False)
    assert await p.comment(Board(), Board().choices()[0]) == ""
    llm.assert_not_called()
    p.ctx.config.get.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        '最终选择：{"ids":[1],"ambiguous":false}。',
        '<think>考虑不同解释。</think>\n{"ids":[1],"ambiguous":false}',
        '```JSON\n{"ids":[1],"ambiguous":false}\n```',
    ],
)
async def test_single_wrapped_final_interpretation_is_accepted(response):
    p, llm = player([{"success": True, "response": response}])
    assert await p.interpret(Board(), "测试指令") == ([Board().choices()[0]], False)
    assert llm.await_count == 1


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
        await p.interpret(Board(), "测试指令")


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
        await p.interpret(Board(), "测试指令")
    assert "鉴权" in str(exc.value)
    assert secret not in str(exc.value) + caplog.text
    assert "hidden-thought" not in caplog.text
    assert "[xiangqi.llm]" in caplog.text and "auth_error" in caplog.text


async def test_reasoning_channel_is_never_used_as_the_move():
    p, _ = player([{"success": True, "response": "", "reasoning": '{"id":1}'}] * 2)
    with pytest.raises(ModelFailure, match="最终答案"):
        await p.interpret(Board(), "测试指令")


async def test_parser_keeps_its_own_deadline():
    p, llm = player([{"success": True, "response": '{"ids":[],"ambiguous":true}'}], request_timeout=7)
    await p.interpret(Board(), "帮我走棋")
    assert llm.call_args.kwargs["timeout_ms"] == 8000
