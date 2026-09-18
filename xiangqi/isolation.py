"""只约束引擎子进程的 CPU；不修改 MaiBot 或共享 runner 的亲和性。"""

from pathlib import Path
from typing import List, Optional

import os
import sys

from .bundled_engine import PreparationError, prepare_engine


class IsolationError(RuntimeError):
    pass


def engine_command(executable: str, cpu: int = -1, data_dir: Optional[Path] = None) -> List[str]:
    if sys.platform != "linux":
        raise IsolationError("引擎绑核需要 Linux；其他系统可设置 engine.enabled = false 暂停自动落子")
    try:
        allowed = os.sched_getaffinity(0)
    except OSError as exc:
        raise IsolationError("无法读取允许使用的 CPU，请检查运行环境") from exc
    if not allowed:
        raise IsolationError("没有可用 CPU")
    # 自动选择在本 runner 的允许集合内，不依赖旧启动器环境变量或超线程编号。
    selected = max(allowed) if cpu == -1 else cpu
    if selected not in allowed:
        raise IsolationError("engine.cpu 不在当前允许的 CPU 集合内")
    try:
        path = Path(executable).expanduser() if executable else prepare_engine(data_dir)
    except PreparationError as exc:
        raise IsolationError(str(exc)) from exc
    if not path.is_file() or not os.access(path, os.X_OK):
        raise IsolationError("未找到可执行引擎，请检查 engine.executable；留空使用插件内置引擎")
    # 用同一 Python 在子进程内设置亲和性，exec 后引擎线程继承同一逻辑 CPU。
    worker = Path(__file__).with_name("engine_worker.py")
    return [sys.executable, str(worker), str(selected), str(path.resolve())]
