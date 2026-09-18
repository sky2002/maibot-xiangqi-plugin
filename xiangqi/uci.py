"""UCI 子进程的协议和所有权；调用者负责串行访问。"""

from typing import List, Optional

import asyncio


class EngineFailure(RuntimeError):
    pass


class UciProcess:
    def __init__(self, command: List[str]) -> None:
        self.command = command
        self.process: Optional[asyncio.subprocess.Process] = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        try:
            async with asyncio.timeout(8):
                self.process = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=65536,
                )
                await self.send("uci")
                handshake = await self.read_until("uciok")
                for required in (
                    "Threads",
                    "Hash",
                    "MultiPV",
                    "UCI_Variant",
                    "Use NNUE",
                    "Skill Level",
                    "UCI_LimitStrength",
                ):
                    if not any(line.startswith(f"option name {required} type ") for line in handshake):
                        raise EngineFailure("引擎不兼容，请安装官方 Fairy-Stockfish 14 largeboard")
                if not any(" var xiangqi" in line for line in handshake):
                    raise EngineFailure("引擎未包含象棋规则，请使用 largeboard 版本")
                for option, value in (
                    ("Threads", 1),
                    ("UCI_Variant", "xiangqi"),
                    ("Use NNUE", "false"),
                    ("Ponder", "false"),
                    ("Skill Level", 20),
                    ("UCI_LimitStrength", "false"),
                ):
                    await self.send(f"setoption name {option} value {value}")
                await self.send("isready")
                await self.read_until("readyok")
        except BaseException as exc:
            # 宿主不会为 on_load 失败调用 on_unload，初始化必须自己回收。
            await self.close()
            if isinstance(exc, TimeoutError):
                raise EngineFailure("引擎初始化超时，请检查引擎安装") from exc
            if isinstance(exc, (OSError, ValueError)):
                raise EngineFailure("引擎启动或协议解析失败，请检查安装和 engine.cpu") from exc
            raise

    async def send(self, line: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise EngineFailure("引擎未启动")
        self.process.stdin.write((line + "\n").encode("ascii"))
        await self.process.stdin.drain()

    async def read_until(self, prefix: str) -> List[str]:
        if self.process is None or self.process.stdout is None:
            raise EngineFailure("引擎未启动")
        lines = []
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise EngineFailure("引擎提前退出，请检查可执行文件及 CPU 设置")
            line = raw.decode("utf-8", errors="replace").strip()
            lines.append(line)
            if len(lines) > 10000:
                raise EngineFailure("引擎协议输出异常")
            if line == prefix or line.startswith(prefix + " "):
                return lines

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            # 不依赖引擎响应 quit，取消时也保证搜索不残留占用 CPU。
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
