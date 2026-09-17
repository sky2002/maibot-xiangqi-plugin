"""Linux 物理核心隔离；仅启动器改亲和性，插件负责核验。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import os
import shutil
import sys


class IsolationError(RuntimeError):
    pass


def cpu_set(text: str) -> Set[int]:
    result = set()
    for part in text.strip().split(","):
        ends = part.split("-")
        if len(ends) not in (1, 2) or any(not v.isdigit() for v in ends):
            raise IsolationError("CPU 列表格式错误")
        start, end = int(ends[0]), int(ends[-1])
        if start > end or end > 65535:
            raise IsolationError("CPU 列表范围错误")
        result.update(range(start, end + 1))
    return result


def cpu_text(cpus: Set[int]) -> str:
    return ",".join(map(str, sorted(cpus)))


def topology(cpus: Set[int], root: Path = Path("/sys/devices/system/cpu")) -> Dict[int, Set[int]]:
    result = {}
    for cpu in cpus:
        siblings = cpu_set((root / f"cpu{cpu}/topology/thread_siblings_list").read_text())
        if cpu not in siblings:
            raise IsolationError("系统返回了不一致的物理核心信息")
        result[cpu] = siblings
    return result


@dataclass(frozen=True)
class Plan:
    engine_cpu: int
    reserved: Set[int]
    host: Set[int]


def plan(allowed: Set[int], siblings: Dict[int, Set[int]], engine_cpu: Optional[int] = None) -> Plan:
    if not allowed:
        raise IsolationError("没有可用 CPU")
    # 选择最后一个物理核心的首个可用线程，不能假定超线程编号连续。
    cpu = engine_cpu if engine_cpu is not None else min(siblings[max(allowed)] & allowed)
    if cpu not in allowed:
        raise IsolationError("指定的引擎 CPU 不在当前允许的 CPU 中")
    reserved = siblings[cpu]
    host = allowed - reserved
    if not host:
        raise IsolationError("至少需要两个可用物理核心，无法将引擎与 MaiBot 分开")
    return Plan(cpu, reserved, host)


def default_engine() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "maibot-xiangqi/fairy-sf-14-largeboard"


def _check_threads(pid: int, reserved: Set[int]) -> None:
    threads = list((Path("/proc") / str(pid) / "task").iterdir())
    if not threads:
        raise IsolationError("无法核验 MaiBot 的线程亲和性")
    for thread in threads:
        try:
            overlap = os.sched_getaffinity(int(thread.name)) & reserved
        except ProcessLookupError:
            continue  # 枚举后正常退出的短命线程。
        if overlap:
            raise IsolationError("MaiBot 或插件线程仍可使用引擎物理核心，请通过 run_isolated.py 重启")


def engine_command(executable: str) -> List[str]:
    if sys.platform != "linux":
        raise IsolationError("引擎隔离模式需要 Linux；请在 Linux 上用 run_isolated.py 启动")
    try:
        cpu = int(os.environ["MAIBOT_XIANGQI_ENGINE_CPU"])
        host_pid = int(os.environ["MAIBOT_XIANGQI_HOST_PID"])
        reserved = topology({cpu})[cpu]
        # 从 runner 沿父链核验到启动器 exec 后的进程，覆盖 uv、宿主和 runner。
        pid = os.getpid()
        while True:
            _check_threads(pid, reserved)
            if pid == host_pid:
                break
            fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
            if pid <= 1:
                raise IsolationError("找不到隔离启动器的父进程，请重新启动 MaiBot")
    except (KeyError, ValueError, OSError) as exc:
        raise IsolationError("无法验证 CPU 隔离，请使用 scripts/run_isolated.py 启动 MaiBot") from exc
    taskset = shutil.which("taskset")
    if not taskset:
        raise IsolationError("缺少 taskset，请安装 util-linux")
    path = Path(executable).expanduser() if executable else default_engine()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise IsolationError("未找到可执行引擎，请先运行 scripts/install_engine.py")
    # taskset 在 exec 引擎前设置亲和性，搜索线程继承同一逻辑 CPU。
    return [taskset, "--cpu-list", str(cpu), str(path.resolve())]
