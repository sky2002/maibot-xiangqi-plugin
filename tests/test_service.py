from unittest.mock import AsyncMock

import asyncio
import time

from xiangqi.llm import Player
from xiangqi.rules import Board
from xiangqi.store import Store


async def command(service, text, user="alice", stream="s1", group="g1", message_id=""):
    await service.handle(stream, group, "qq", user, text, message_id)


async def drain(service):
    async with asyncio.timeout(3):
        while service.jobs:
            await asyncio.gather(*list(service.jobs.values()))
            await asyncio.sleep(0)


def said(service):
    return "\n".join(call.args[0] for call in service.ctx.send.text.call_args_list)


async def test_red_game_turn_and_undo(service):
    await command(service, "开始")
    await command(service, "炮八平五")
    # 命令返回即落盘；无需等待模型完成。
    assert service.store.get("s1").moves == ["b2e2"]
    await drain(service)
    assert len(service.store.get("s1").moves) == 2
    await command(service, "悔棋")
    assert service.store.get("s1").moves == []
    assert service.ctx.send.image.await_count == 3


async def test_black_game_bot_opens_and_undo_preserves_opening(service):
    await command(service, "开始 黑")
    await drain(service)
    game = service.store.get("s1")
    opening = list(game.moves)
    assert len(opening) == 1 and not game.bot_turn
    await command(service, game.position().board.choices()[0].move)
    await drain(service)
    assert len(service.store.get("s1").moves) == 3
    await command(service, "悔棋")
    assert service.store.get("s1").moves == opening


async def test_spectators_cannot_move_retry_or_undo(service):
    await command(service, "开始")
    for action in ("炮八平五", "悔棋", "重试", "认输", "结束"):
        await command(service, action, user="bob")
    assert service.store.get("s1").moves == []
    assert not service.store.get("s1").result
    service.ctx.llm.generate.assert_not_called()
    await command(service, "棋盘", user="bob")
    assert service.ctx.send.image.await_count == 2


async def test_duplicate_messages_are_ignored_and_persisted(service, tmp_path):
    await command(service, "开始", message_id="start")
    await command(service, "炮八平五", message_id="move")
    await drain(service)
    await command(service, "炮八平五", message_id="move")
    assert len(service.store.get("s1").moves) == 2
    other = Store(tmp_path / "xiangqi.sqlite3")
    try:
        assert other.get("s1").moves == service.store.get("s1").moves
        assert not other.claim_message("s1", "move")
    finally:
        other.close()


async def test_failure_keeps_player_move_and_can_retry(service):
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id":9999}'}
    await command(service, "开始")
    await command(service, "炮八平五")
    await drain(service)
    assert service.store.get("s1").moves == ["b2e2"]
    assert "下棋 重试" in said(service)
    service.ctx.llm.generate.return_value = {"success": True, "response": '{"id":1}'}
    await command(service, "重试")
    await drain(service)
    assert len(service.store.get("s1").moves) == 2


async def test_can_undo_unanswered_move(service):
    service.ctx.llm.generate.return_value = {"success": False}
    await command(service, "开始")
    await command(service, "炮八平五")
    await drain(service)
    await command(service, "悔棋")
    assert service.store.get("s1").moves == []


async def test_busy_rejects_second_move_and_undo(service):
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return {"success": True, "response": '{"id":1}'}

    service.ctx.llm.generate.side_effect = delayed
    await command(service, "开始")
    await command(service, "炮八平五")
    await started.wait()
    await command(service, "悔棋")
    await command(service, "马二进三")
    assert service.store.get("s1").moves == ["b2e2"]
    release.set()
    await drain(service)
    assert len(service.store.get("s1").moves) == 2


async def test_late_reply_cannot_modify_replacement_game(service, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(self, board, moves):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # 模拟不响应取消的外部提供方。
        return board.choices()[0]

    monkeypatch.setattr(Player, "select", delayed)
    await command(service, "开始")
    await command(service, "炮八平五")
    old_task = service.jobs["s1"]
    await started.wait()
    await command(service, "认输")
    await command(service, "开始")
    new_id = service.store.get("s1").id
    release.set()
    await old_task
    assert service.store.get("s1").id == new_id
    assert service.store.get("s1").moves == []


async def test_ambiguity_selection_is_owner_bound(service, monkeypatch):
    monkeypatch.setattr(Player, "interpret", AsyncMock(return_value=(Board().choices()[:2], True)))
    await command(service, "开始")
    await command(service, "左边的车走一下")
    await drain(service)
    assert service.store.get("s1").moves == []
    await command(service, "选择 1", user="bob")
    assert service.store.get("s1").moves == []
    await command(service, "选择 2")
    await drain(service)
    assert service.store.get("s1").moves[0] == Board().choices()[1].move


async def test_clear_natural_language_plays_only_resolved_move(service, monkeypatch):
    selected = Board().parse("炮八平五")[0]
    monkeypatch.setattr(Player, "interpret", AsyncMock(return_value=([selected], False)))
    await command(service, "开始")
    await command(service, "把b2的炮移到e2")
    await drain(service)
    assert service.store.get("s1").moves[0] == "b2e2"


async def test_groups_are_isolated(service):
    await command(service, "开始")
    await command(service, "开始", stream="s2", group="g2", user="bob")
    await command(service, "炮八平五")
    await drain(service)
    assert service.store.get("s2").moves == []
    assert service.store.get("s2").owner == "bob"


async def test_spectator_views_do_not_extend_idle_timer(service):
    await command(service, "开始")
    original = service.store.get("s1").updated_at
    await command(service, "棋盘", user="bob")
    assert service.store.get("s1").updated_at == original
    game = service.store.get("s1")
    game.updated_at = time.time() - 1900
    service.store.save(game)
    await service.expire()
    assert "不计胜负" in service.store.get("s1").result
    await command(service, "开始", user="bob")
    assert service.store.get("s1").owner == "bob"


async def test_admin_is_scoped_to_group(service):
    service.settings.admins = ["qq:g1:bob"]
    await command(service, "开始")
    await command(service, "开始", stream="s2", group="g2")
    await command(service, "结束", stream="s2", group="g2", user="bob")
    assert not service.store.get("s2").result
    await command(service, "结束", user="bob")
    assert "管理员" in service.store.get("s1").result


async def test_illegal_notation_does_not_call_model(service):
    await command(service, "开始")
    await command(service, "炮八平八")
    assert service.store.get("s1").moves == []
    service.ctx.llm.generate.assert_not_called()


async def test_no_capture_rule_finishes_game(service):
    service.settings.no_capture_halfmoves = 2
    await command(service, "开始")
    await command(service, "炮八平五")
    await drain(service)
    assert "和棋" in service.store.get("s1").result
    await command(service, "悔棋")
    assert len(service.store.get("s1").moves) == 2
