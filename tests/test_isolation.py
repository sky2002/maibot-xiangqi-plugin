from pathlib import Path
from unittest.mock import Mock

import os
import sys

import pytest

from xiangqi.isolation import IsolationError, engine_command


@pytest.fixture
def cpu_environment(monkeypatch, tmp_path):
    path = tmp_path / "engine"
    path.touch()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {2, 5}, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", Mock(), raising=False)
    monkeypatch.setattr(os, "access", lambda *args: True)
    monkeypatch.delenv("MAIBOT_XIANGQI_ENGINE_CPU", raising=False)
    monkeypatch.delenv("MAIBOT_XIANGQI_HOST_PID", raising=False)
    return path


def test_normal_launch_needs_no_launcher_and_never_changes_host(cpu_environment):
    assert engine_command(str(cpu_environment)) == [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "xiangqi/engine_worker.py"),
        "5",
        str(cpu_environment.resolve()),
    ]
    assert engine_command(str(cpu_environment), 2)[2] == "2"
    os.sched_setaffinity.assert_not_called()


def test_single_allowed_cpu_is_supported(cpu_environment, monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {3})
    assert engine_command(str(cpu_environment))[2] == "3"


def test_invalid_cpu_fails_before_launch(cpu_environment):
    with pytest.raises(IsolationError, match="engine.cpu"):
        engine_command(str(cpu_environment), 0)


def test_no_system_tool_required(cpu_environment, monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert engine_command(str(cpu_environment))[0] == sys.executable


def test_missing_engine_is_actionable(cpu_environment):
    with pytest.raises(IsolationError, match="engine.executable"):
        engine_command(str(cpu_environment) + "missing")


def test_unsupported_platform_is_explicit(cpu_environment, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(IsolationError, match="Linux"):
        engine_command(str(cpu_environment))
