from unittest.mock import AsyncMock

import asyncio
import json

import pytest

from xiangqi.engine import Candidate, EngineFailure
from xiangqi.llm import ModelFailure, Player
from xiangqi.rules import Board


async def start(service):
    await service.handle("s1", "g1", "qq", "alice", "开始")


def candidate(choice):
    return Candidate(choice, "cp", 30, 7, [choice.move])


async def test_llm_only_sees_engine_subset_and_decides(service):
    options = [candidate(c) for c in Board().choices()[-3:]]
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id": 2}'}
    selected = await Player(service.ctx, service.settings).select(Board(), [], options)
    assert selected == options[1].choice
    sent = json.loads(service.ctx.llm.generate.call_args.kwargs["prompt"][1]["content"])
    assert [c["move"] for c in sent["legal_moves"]] == [c.choice.move for c in options]
    assert len(sent["engine_analysis"]) == 3
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id": 4}'}
    with pytest.raises(ModelFailure):
        await Player(service.ctx, service.settings).select(Board(), [], options)


async def test_engine_failure_preserves_human_move_without_llm_fallback(service):
    service.engine_settings.enabled = True
    service.engine.analyse = AsyncMock(side_effect=EngineFailure("未隔离 CPU"))
    await start(service)
    await service.handle("s1", "g1", "qq", "alice", "炮八平五")
    await asyncio.gather(*list(service.jobs.values()))
    assert service.store.get("s1").moves == ["b2e2"]
    service.ctx.llm.generate.assert_not_called()
    assert any("未隔离 CPU" in c.args[0] for c in service.ctx.send.text.call_args_list)


async def test_hybrid_move_automatically_explained_with_selected_evidence(service, monkeypatch):
    service.engine_settings.enabled = True
    service.settings.commentary = True
    board = Board()
    board.push("b2e2")
    options = [candidate(c) for c in board.choices()[-3:]]
    service.engine.analyse = AsyncMock(return_value=options)
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id": 2}'}
    explain = AsyncMock(return_value="先把马跳出来，准备接应。")
    monkeypatch.setattr(Player, "comment", explain)
    await start(service)
    await service.handle("s1", "g1", "qq", "alice", "炮八平五")
    await asyncio.gather(*list(service.jobs.values()))
    await asyncio.gather(*list(service.aux))
    game = service.store.get("s1")
    assert game.moves == ["b2e2", options[1].choice.move]
    assert explain.call_args.args[2]["selected"]["move"] == options[1].choice.move
    assert any("先把马" in c.args[0] for c in service.ctx.send.text.call_args_list)
    await service.handle("s1", "g1", "qq", "alice", "悔棋")
    assert service._evidence(service.store.get("s1")) is None


async def test_automatic_chat_needs_no_command_and_never_moves(service):
    await start(service)
    before = service.store.get("s1")
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"reply":"这步是为了护住中路。"}'}
    assert await service.chat("s1", "g1", "qq", "alice", "这步为什么这么走", "chat1")
    assert service.store.get("s1") == before
    assert not await service.chat("s1", "g1", "qq", "alice", "再说一点", "chat2")
    assert service.ctx.llm.generate.await_count == 1  # 冷却防止频繁调用。


@pytest.mark.parametrize(
    "stream,group,platform,user,text",
    [
        ("s1", "g1", "qq", "bob", "这步为什么这么走"),
        ("s1", "g2", "qq", "alice", "这步为什么这么走"),
        ("s1", "g1", "other", "alice", "这步为什么这么走"),
        ("s2", "g1", "qq", "alice", "这步为什么这么走"),
        ("s1", "g1", "qq", "alice", "下棋 炮八平五"),
        ("s1", "g1", "qq", "alice", "/其他命令"),
    ],
)
async def test_chat_scope_does_not_intercept_other_users_or_commands(
    service, stream, group, platform, user, text
):
    await start(service)
    assert not await service.chat(stream, group, platform, user, text)
    service.ctx.llm.generate.assert_not_called()


@pytest.mark.parametrize("answer", ['{"reply":""}', '{"reply":123}', "没有 JSON"])
async def test_unrelated_or_invalid_chat_continues_normal_host_processing(service, answer):
    await start(service)
    service.ctx.llm.generate.return_value = {"success": True, "response": answer}
    service.ctx.send.text.reset_mock()
    assert not await service.chat("s1", "g1", "qq", "alice", "今晚吃什么")
    service.ctx.send.text.assert_not_called()


async def test_late_chat_is_discarded_after_game_ends(service, monkeypatch):
    await start(service)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        started.set()
        await release.wait()
        return "过期的棋评"

    monkeypatch.setattr(Player, "chat", delayed)
    task = asyncio.create_task(service.chat("s1", "g1", "qq", "alice", "这步如何"))
    await started.wait()
    await service.handle("s1", "g1", "qq", "alice", "认输")
    release.set()
    assert not await task
    assert all("过期的棋评" not in c.args[0] for c in service.ctx.send.text.call_args_list)
