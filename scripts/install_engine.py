"""下载固定版本的官方 Linux x86_64 引擎，校验 SHA256 后原子安装。"""

from pathlib import Path
from urllib.request import urlopen

import argparse
import hashlib
import platform
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiangqi.isolation import default_engine  # noqa: E402


URL = "https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14/fairy-stockfish-largeboard_x86-64"
SHA256 = "41b8b4d539adfd9924929ee4a948d1a37dd1e9beaa535a811cb5e7fee9e4cb99"


def install(destination: Path) -> None:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == SHA256:
        destination.chmod(0o755)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            with urlopen(URL, timeout=60) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != SHA256:
                raise RuntimeError("引擎 SHA256 不符，未安装；请检查下载网络和官方发布")
            output.write(data)
        temporary.chmod(0o755)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=default_engine())
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        parser.error("此安装器只支持 Linux x86_64；其他架构需自行构建 largeboard 引擎")
    install(args.destination.expanduser().resolve())
    print(f"已安装 Fairy-Stockfish 14 largeboard：{args.destination}")
    print("来源与 GPL 源码：https://github.com/fairy-stockfish/Fairy-Stockfish/tree/fairy_sf_14")


if __name__ == "__main__":
    main()
