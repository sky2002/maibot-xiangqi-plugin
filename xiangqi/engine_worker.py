"""仅在新建的引擎子进程内绑核并 exec，不依赖系统 taskset。"""

import os
import sys


def main() -> None:
    if sys.platform != "linux" or len(sys.argv) < 3:
        raise RuntimeError("引擎子进程需要 Linux、CPU 编号和可执行文件路径")
    os.sched_setaffinity(0, {int(sys.argv[1])})
    # exec 保留 PID 和管道，插件持有的进程对象直接管理引擎本身。
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
