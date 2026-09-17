from pathlib import Path
from unittest.mock import Mock

import importlib.util
import os
import subprocess
import sys

import pytest


@pytest.fixture
def launcher(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/run_isolated.py"
    spec = importlib.util.spec_from_file_location("xiangqi_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2, 3}, raising=False)
    monkeypatch.setattr(module, "topology", lambda cpus: {0: {0, 2}, 2: {0, 2}, 1: {1, 3}, 3: {1, 3}})
    monkeypatch.setattr(os, "sched_setaffinity", Mock(), raising=False)
    monkeypatch.setenv("MAIBOT_XIANGQI_HOST_PID", "test-original")
    monkeypatch.setenv("MAIBOT_XIANGQI_ENGINE_CPU", "test-original")
    return module


def test_missing_uv_has_actionable_error_before_affinity_changes(launcher, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_isolated.py", "--", "uv", "run", "--no-sync", "bot.py"])
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/usr/bin/taskset" if name == "taskset" else None
    )
    execute = Mock(side_effect=FileNotFoundError(2, "No such file or directory", b"/snap/bin/uv"))
    monkeypatch.setattr(os, "execvpe", execute)
    with pytest.raises(SystemExit) as caught:
        launcher.main()
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "uv" in error and ".venv/bin/python" in error
    execute.assert_not_called()
    os.sched_setaffinity.assert_not_called()
    assert os.environ["MAIBOT_XIANGQI_HOST_PID"] == "test-original"


def test_exec_error_is_reported_without_traceback(launcher, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_isolated.py", "--", "uv", "run", "bot.py"])
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/usr/bin/taskset" if name == "taskset" else "/snap/bin/uv"
    )
    monkeypatch.setattr(
        os, "execvpe", Mock(side_effect=FileNotFoundError(2, "No such file or directory", b"/snap/bin/uv"))
    )
    with pytest.raises(SystemExit) as caught:
        launcher.main()
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "无法启动" in error and "/snap/bin/uv" in error


def test_explicit_python_preserves_environment_path_and_arguments(launcher, monkeypatch):
    python = "/home/user/桌面/MaiBot/.venv/bin/python"
    monkeypatch.setattr(sys, "argv", ["run_isolated.py", "--", python, "bot.py", "an argument with spaces"])
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/usr/bin/taskset" if name == "taskset" else name
    )
    execute = Mock()
    monkeypatch.setattr(os, "execvpe", execute)
    launcher.main()
    assert execute.call_args.args[:2] == (python, [python, "bot.py", "an argument with spaces"])
    os.sched_setaffinity.assert_called_once_with(0, {0, 2})
    assert os.environ["MAIBOT_XIANGQI_ENGINE_CPU"] == "1"
    assert os.environ["MAIBOT_XIANGQI_HOST_PID"] == str(os.getpid())


@pytest.mark.skipif(sys.platform != "linux", reason="真实 Linux 启动器子进程")
def test_missing_executable_in_real_linux_launcher(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_isolated.py"
    result = subprocess.run(
        [sys.executable, str(script), "--", str(tmp_path / "missing-uv"), "run", "bot.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 2
    assert "找不到可执行的启动命令" in result.stderr
    assert ".venv/bin/python" in result.stderr
    assert "Traceback" not in result.stderr
