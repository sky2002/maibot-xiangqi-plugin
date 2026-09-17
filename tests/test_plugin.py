from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import importlib.util
import json
import logging
import re
import sys
import tomllib

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
