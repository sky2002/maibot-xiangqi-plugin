from pathlib import Path
from unittest.mock import Mock

import hashlib
import os
import sys
import tarfile

import pytest

from xiangqi import bundled_engine, engine_worker
from xiangqi.bundled_engine import ASSET, SHA256, PreparationError, default_engine, prepare_engine


@pytest.fixture
def linux_x64(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("xiangqi.bundled_engine.platform.machine", lambda: "x86_64")


def test_shipped_engine_and_corresponding_source_are_complete():
    binary = ASSET.read_bytes()
    assert binary.startswith(b"\x7fELF")
    assert hashlib.sha256(binary).hexdigest() == SHA256
    source = ASSET.with_name("fairy-stockfish-14-source.tar.gz")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "db5e96cf47faf4bfd4a500f58ae86e46fee92c2f5544e78750fc01ad098cbad2"
    )
    with tarfile.open(source) as archive:
        root = "Fairy-Stockfish-fairy_sf_14/"
        assert root + "src/Makefile" in archive.getnames()
        assert root + "src/search.cpp" in archive.getnames()
        assert (
            archive.extractfile(root + "Copying.txt").read().decode("utf-8").splitlines()
            == ASSET.with_name("Fairy-Stockfish-Copying.txt").read_text(encoding="utf-8").splitlines()
        )


def test_fresh_webui_install_materializes_offline_and_reuses_cache(linux_x64, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    path = prepare_engine(tmp_path)
    assert path == default_engine(tmp_path).resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA256
    before = path.stat().st_mtime_ns
    # 命中有效缓存时不读取分发资源，也不依赖下载器或系统安装程序。
    monkeypatch.setattr(bundled_engine, "ASSET", tmp_path / "missing-asset")
    assert prepare_engine(tmp_path) == path
    assert path.stat().st_mtime_ns == before
    assert list(path.parent.glob(".fairy-*")) == []


def test_corrupt_cached_engine_is_repaired_from_bundle(linux_x64, tmp_path):
    path = default_engine(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"incomplete old file")
    prepare_engine(tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA256


def test_corrupt_bundle_never_replaces_existing_engine(linux_x64, tmp_path, monkeypatch):
    asset = tmp_path / "bad-asset"
    asset.write_bytes(b"bad package")
    monkeypatch.setattr(bundled_engine, "ASSET", asset)
    path = default_engine(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"keep until valid replacement")
    with pytest.raises(PreparationError, match="SHA256"):
        prepare_engine(tmp_path)
    assert path.read_bytes() == b"keep until valid replacement"
    assert list(path.parent.glob(".fairy-*")) == []


def test_failed_atomic_replace_cleans_temporary_file(linux_x64, tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError("read only")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PreparationError, match="权限"):
        prepare_engine(tmp_path)
    assert not default_engine(tmp_path).exists()
    assert list(default_engine(tmp_path).parent.iterdir()) == []


def test_unsupported_architecture_never_writes_files(linux_x64, tmp_path, monkeypatch):
    monkeypatch.setattr("xiangqi.bundled_engine.platform.machine", lambda: "aarch64")
    with pytest.raises(PreparationError, match="Linux x86_64"):
        prepare_engine(tmp_path)
    assert not default_engine(tmp_path).exists()


def test_worker_sets_affinity_before_replacing_itself(linux_x64, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["worker", "5", "/engine", "argument with spaces"])
    monkeypatch.setattr(os, "sched_setaffinity", lambda *args: calls.append(("pin", args)), raising=False)
    monkeypatch.setattr(os, "execv", lambda *args: calls.append(("exec", args)))
    engine_worker.main()
    assert calls == [("pin", (0, {5})), ("exec", ("/engine", ["/engine", "argument with spaces"]))]


def test_failed_affinity_never_executes_engine(linux_x64, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["worker", "5", "/engine"])
    monkeypatch.setattr(os, "sched_setaffinity", Mock(side_effect=OSError("denied")), raising=False)
    execute = Mock()
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(OSError):
        engine_worker.main()
    execute.assert_not_called()
