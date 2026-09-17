from unittest.mock import AsyncMock

import asyncio
import json

import pytest

from xiangqi.difficulty import opening
from xiangqi.engine import Candidate
from xiangqi.store import Store


async def command(service, text, user="alice", stream="s1", group="g1"):
    await service.handle(stream, group, "qq", user, text)


def said(service):
    return service.ctx.send.text.call_args.args[0]


@pytest.mark.parametrize(
    "text,result",
    [
        ("开始", (True, 3)),
        ("开始 黑 简单", (False, 2)),
        ("开始 困难 红方", (True, 4)),
        ("开始 1", (True, 1)),
        ("开始 黑方 5", (False, 5)),
    ],
)
def test_opening_color_and_difficulty(text, result):
    assert opening(text, 3) == result


@pytest.mark.parametrize("text", ["开始 黑 红", "开始 6", "开始 简单 困难", "开始 新手", "开始 红 1 多余"])
def test_bad_opening_never_silently_chooses_a_level(text):
    with pytest.raises(ValueError):
        opening(text, 3)


async def test_default_snapshot_group_isolation_and_restart(service, tmp_path):
    service.engine_settings.enabled = True
    service.engine_settings.difficulty = 4
    await command(service, "开始")
    service.engine_settings.difficulty = 3
    await command(service, "开始 红 1", stream="s2", group="g2")
    assert service.store.get("s1").difficulty == 4
    assert service.store.get("s2").difficulty == 1
    await command(service, "难度 简单")
    assert service.store.get("s1").difficulty == 2
    reopened = Store(tmp_path / "xiangqi.sqlite3")
    try:
        assert reopened.get("s1").difficulty == 2
        assert reopened.get("s2").difficulty == 1
    finally:
        reopened.close()


async def test_old_save_preserves_pre_upgrade_search_mode(service):
    await command(service, "开始")
    data = json.loads(service.store.db.execute("SELECT data FROM games WHERE stream_id='s1'").fetchone()[0])
    data.pop("difficulty")
    with service.store.db:
        service.store.db.execute("UPDATE games SET data=? WHERE stream_id='s1'", (json.dumps(data),))
    assert service.store.get("s1").difficulty == 5


async def test_difficulty_view_and_owner_only_changes_do_not_move_or_extend_timer(service):
    service.engine_settings.enabled = True
    await command(service, "难度", user="bob")
    assert "默认难度：3" in said(service)
    await command(service, "开始 简单")
    before = service.store.get("s1")
    await command(service, "难度", user="bob")
    assert "本局难度：2" in said(service)
    await command(service, "难度 5", user="bob")
    assert "发起者" in said(service)
    assert service.store.get("s1") == before
    await command(service, "难度 4")
    game = service.store.get("s1")
    assert game.difficulty == 4 and game.moves == before.moves and game.updated_at == before.updated_at
    service.ctx.llm.generate.assert_not_called()


@pytest.mark.parametrize("text", ["难度 0", "难度 6", "难度 简单 多余", "难度 2.5"])
async def test_bad_level_does_not_change_game_or_call_llm(service, text):
    service.engine_settings.enabled = True
    await command(service, "开始")
    before = service.store.get("s1")
    await command(service, text)
    assert service.store.get("s1") == before
    assert "请选择" in said(service)
    service.ctx.llm.generate.assert_not_called()


async def test_busy_cannot_change_level_and_engine_receives_game_setting(service):
    service.engine_settings.enabled = True
    started, release = asyncio.Event(), asyncio.Event()

    async def analyse(board, settings):
        assert settings.difficulty == 2
        started.set()
        await release.wait()
        c = board.choices()[0]
        return [Candidate(c, "cp", 30, 3, [c.move])]

    service.engine.analyse = AsyncMock(side_effect=analyse)
    await command(service, "开始 黑 简单")
    await started.wait()
    await command(service, "难度 5")
    assert "思考期间" in said(service)
    assert service.store.get("s1").difficulty == 2
    release.set()
    await asyncio.gather(*list(service.jobs.values()))
    assert len(service.store.get("s1").moves) == 1


async def test_pure_llm_reports_no_engine_difficulty(service):
    await command(service, "开始")
    before = service.store.get("s1")
    await command(service, "难度 1")
    assert "纯 LLM" in said(service)
    assert service.store.get("s1") == before
