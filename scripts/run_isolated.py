"""在 MaiBot 启动前隔离整个物理核心，不需要 root 或修改宿主源码。"""

from pathlib import Path

import argparse
import os
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiangqi.isolation import IsolationError, cpu_text, plan, topology  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-cpu", type=int, help="可选：指定引擎逻辑 CPU，自动排除其所有超线程")
    parser.add_argument("--dry-run", action="store_true", help="只显示分配，不启动")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- 后填写原来的 Python / uv 启动命令")
    args = parser.parse_args()
    if sys.platform != "linux" or not shutil.which("taskset"):
        parser.error("需要 Linux 和 taskset（util-linux）")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    python_hint = "在 MaiBot 根目录可直接运行：.venv/bin/python plugins/maibot-xiangqi-plugin/scripts/run_isolated.py -- .venv/bin/python bot.py"
    if not args.dry_run:
        if not command:
            parser.error("请在 -- 后填写原启动命令，例如 .venv/bin/python bot.py")
        executable = shutil.which(command[0])
        if executable is None:
            parser.error(f"找不到可执行的启动命令 {command[0]!r}，请检查安装、PATH 或文件权限。{python_hint}")
        # 保留虚拟环境 Python 的符号链接路径，不能 resolve 到系统 Python。
        command[0] = executable
    try:
        allowed = os.sched_getaffinity(0)
        selected = plan(allowed, topology(allowed), args.engine_cpu)
    except (IsolationError, OSError) as exc:
        parser.error(str(exc))
    print(f"MaiBot CPU：{cpu_text(selected.host)}", flush=True)
    print(f"引擎 CPU：{selected.engine_cpu}；预留整个物理核心：{cpu_text(selected.reserved)}", flush=True)
    if args.dry_run:
        return
    os.environ["MAIBOT_XIANGQI_ENGINE_CPU"] = str(selected.engine_cpu)
    os.environ["MAIBOT_XIANGQI_HOST_PID"] = str(os.getpid())
    os.sched_setaffinity(0, selected.host)
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as exc:
        # PATH 预检后文件仍可能消失，或脚本解释器/动态加载器不存在。
        parser.error(f"无法启动 {command[0]!r}：{exc.strerror}。请检查文件及其解释器。{python_hint}")


if __name__ == "__main__":
    main()
