"""离线准备随插件分发的官方引擎；校验后原子写入插件数据目录。"""

from pathlib import Path
from typing import Optional

import hashlib
import os
import platform
import sys
import tempfile


ASSET = Path(__file__).with_name("assets") / "fairy-stockfish-14-largeboard-linux-x86_64"
SHA256 = "41b8b4d539adfd9924929ee4a948d1a37dd1e9beaa535a811cb5e7fee9e4cb99"


class PreparationError(RuntimeError):
    pass


def default_engine(data_dir: Optional[Path] = None) -> Path:
    if data_dir is not None:
        return data_dir / "engine/fairy-sf-14-largeboard"
    # 直接使用 Engine 的调用者沿用旧路径；SDK 入口始终传入插件专属数据目录。
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "maibot-xiangqi/fairy-sf-14-largeboard"


def prepare_engine(data_dir: Optional[Path] = None) -> Path:
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise PreparationError("内置引擎支持 Linux x86_64；其他架构请配置 engine.executable 或关闭引擎")
    destination = default_engine(data_dir)
    temporary = None
    try:
        if destination.is_file():
            with destination.open("rb") as cached:
                if hashlib.file_digest(cached, "sha256").hexdigest() == SHA256:
                    destination.chmod(0o755)
                    return destination.resolve()
        data = ASSET.read_bytes()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise PreparationError("插件内置引擎 SHA256 不符，请在 WebUI 重新安装完整插件")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".fairy-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.chmod(0o755)
        temporary.replace(destination)
        return destination.resolve()
    except OSError as exc:
        raise PreparationError("无法准备内置引擎，请检查插件文件完整性及数据目录读写、执行权限") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
