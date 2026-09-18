from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import importlib.util
import json
import logging
import platform
import re
import sys
import tomllib

import pytest

from PIL import Image, ImageFont

from xiangqi.render import FONT, render_board
from xiangqi.rules import Board


ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    # 与 MaiBot Runner 相同的包加载方式，验证入口相对导入能工作。
    name = "_xiangqi_plugin_integration"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "plugin.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.create_plugin()


async def test_sdk_entry_command_lifecycle(tmp_path):
    plugin = load_plugin()
    config = plugin.get_default_config()
    config["chess"]["commentary"] = False
    config["engine"]["enabled"] = False
    plugin.set_plugin_config(config)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(data_dir=tmp_path),
        logger=logging.getLogger("entry-test"),
        send=SimpleNamespace(text=AsyncMock(return_value=True), image=AsyncMock(return_value=True)),
        config=SimpleNamespace(get=AsyncMock(return_value="")),
        llm=SimpleNamespace(
            generate=AsyncMock(return_value={"success": True, "response": '{"reply":"先看看中路。"}'})
        ),
    )
    plugin._set_context(ctx)
    components = plugin.get_components()
    assert len(components) == 2
    command = next(c for c in components if "command_pattern" in c["metadata"])
    pattern = command["metadata"]["command_pattern"]
    assert re.fullmatch(pattern, "下棋 炮八平五")
    assert not re.fullmatch(pattern, "他刚说下棋 炮八平五")
    await plugin.on_load()
    try:
        result = await plugin.handle_chess(
            stream_id="s1",
            group_id="g1",
            platform="qq",
            user_id="u1",
            matched_groups={"instruction": "开始"},
            message={"message_id": "m1"},
        )
        assert result == (True, "象棋指令已处理", True)
        assert plugin.service.store.get("s1").owner == "u1"
        ctx.send.image.assert_awaited_once()
        hook_reply = await plugin.handle_chat(
            message={
                "session_id": "s1",
                "platform": "qq",
                "message_id": "chat1",
                "message_info": {"group_info": {"group_id": "g1"}, "user_info": {"user_id": "u1"}},
                "processed_plain_text": "这步为什么这么走？",
            }
        )
        assert hook_reply == {"action": "abort"}
        assert plugin.service.store.get("s1").moves == []
    finally:
        await plugin.on_unload()


def test_real_png_and_bundled_chinese_font():
    board = Board()
    board.push("b2e2")
    data = render_board(board, ["b2e2"], True)
    image = Image.open(BytesIO(data))
    assert image.size == (840, 1100) and image.format == "PNG"
    assert len(data) > 20000
    # 若中文丢失，所有棋子会变成同一种缺字方框。
    font = ImageFont.truetype(str(FONT), 40)
    assert bytes(font.getmask("炮")) != bytes(font.getmask("马"))


def test_manifest_versions_and_requirements_stay_aligned():
    manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [
        line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert set(requirements) == set(project["dependencies"])
    assert manifest["version"] == project["version"]
    assert set(manifest["capabilities"]) == {"send.text", "send.image", "config.get", "llm.generate"}
    assert manifest["i18n"]["default_locale"] == "zh-CN"


def managed_plugin(tmp_path, monkeypatch, fake_uci, executable=""):
    plugin = load_plugin()
    config = plugin.get_default_config()
    config["engine"]["executable"] = executable
    plugin.set_plugin_config(config)
    plugin._set_context(
        SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path), logger=logging.getLogger("entry-test"))
    )
    module = sys.modules[type(plugin).__module__ + ".xiangqi.engine"]
    monkeypatch.setattr(module, "engine_command", fake_uci)
    return plugin


async def test_plugin_system_starts_stops_and_reloads_engine(
    tmp_path, monkeypatch, fake_uci, child_processes
):
    plugin = managed_plugin(tmp_path, monkeypatch, fake_uci)
    await plugin.on_load()
    first = plugin.service
    try:
        await plugin.on_load()
        assert plugin.service is first and len(child_processes) == 1
        assert child_processes[0].returncode is None
    finally:
        await plugin.on_unload()
    assert plugin.service is None and child_processes[0].returncode is not None
    assert first.sweeper.done()
    await plugin.on_unload()
    await plugin.on_load()
    assert plugin.service is not first and len(child_processes) == 2
    await plugin.on_unload()
    assert child_processes[1].returncode is not None


async def test_load_failure_closes_store_and_engine(tmp_path, monkeypatch, fake_uci, child_processes):
    plugin = managed_plugin(tmp_path, monkeypatch, fake_uci, "bad")
    store_type = sys.modules[type(plugin).__module__ + ".xiangqi.store"].Store
    closed = []
    close = store_type.close

    def tracked_close(store):
        close(store)
        closed.append(store)

    monkeypatch.setattr(store_type, "close", tracked_close)
    with pytest.raises(RuntimeError, match="不兼容"):
        await plugin.on_load()
    assert plugin.service is None
    assert len(closed) == 1
    assert len(child_processes) == 1 and child_processes[0].returncode is not None


async def test_plugin_config_update_releases_disabled_engine(
    tmp_path, monkeypatch, fake_uci, child_processes
):
    plugin = managed_plugin(tmp_path, monkeypatch, fake_uci)
    await plugin.on_load()
    try:
        config = plugin.get_default_config()
        config["engine"]["enabled"] = False
        plugin.set_plugin_config(config)
        await plugin.on_config_update("self", config, "0.4.0")
        assert not plugin.service.engine_settings.enabled
        assert child_processes[0].returncode is not None
        config["engine"]["enabled"] = True
        plugin.set_plugin_config(config)
        await plugin.on_config_update("self", config, "0.4.0")
        assert plugin.service.engine_settings.enabled
        assert child_processes[-1].returncode is None
    finally:
        await plugin.on_unload()


async def test_unload_cancels_running_search(tmp_path, monkeypatch, fake_uci, child_processes):
    plugin = managed_plugin(tmp_path, monkeypatch, fake_uci, "hang")
    module = sys.modules[type(plugin).__module__ + ".xiangqi.uci"]
    searching = asyncio.Event()
    send = module.UciProcess.send

    async def tracked_send(process, line):
        await send(process, line)
        if line.startswith("go "):
            searching.set()

    monkeypatch.setattr(module.UciProcess, "send", tracked_send)
    await plugin.on_load()
    service = plugin.service
    task = asyncio.create_task(service.engine.analyse(Board(), service.engine_settings))
    service.tasks.add(task)
    try:
        await asyncio.wait_for(searching.wait(), 5)
    finally:
        await asyncio.wait_for(plugin.on_unload(), 5)
    assert task.cancelled()
    assert child_processes[0].returncode is not None
    assert service.sweeper.done()


async def test_failed_config_update_keeps_working_settings(tmp_path, monkeypatch, fake_uci, child_processes):
    plugin = managed_plugin(tmp_path, monkeypatch, fake_uci)
    await plugin.on_load()
    original = plugin.service.engine_settings
    try:
        config = plugin.get_default_config()
        config["engine"]["executable"] = "bad"
        plugin.set_plugin_config(config)
        with pytest.raises(RuntimeError, match="不兼容"):
            await plugin.on_config_update("self", config, "0.4.0")
        assert plugin.service.engine_settings is original
        assert child_processes[0].returncode is None
        assert child_processes[1].returncode is not None
    finally:
        await plugin.on_unload()


@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"),
    reason="内置引擎需要 Linux x86_64",
)
async def test_sdk_fresh_install_works_without_installer_or_taskset(tmp_path, monkeypatch, child_processes):
    plugin = load_plugin()
    plugin.set_plugin_config(plugin.get_default_config())
    plugin._set_context(
        SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path), logger=logging.getLogger("bundled-test"))
    )
    # 安装后的插件拥有全部资源，首次启动不依赖 PATH 中的外部命令。
    monkeypatch.setenv("PATH", "")
    await plugin.on_load()
    try:
        assert (tmp_path / "engine/fairy-sf-14-largeboard").is_file()
        assert len(child_processes) == 1 and child_processes[0].returncode is None
        settings = plugin.service.engine_settings.model_copy(update={"difficulty": 6})
        move = await plugin.service.engine.analyse(Board(), settings)
        assert move.choice.move in Board().legal_moves()
    finally:
        await plugin.on_unload()
    assert child_processes[0].returncode is not None
