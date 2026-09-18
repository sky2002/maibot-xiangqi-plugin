"""由插件生命周期管理的引擎；跨群串行，失败/取消时回收子进程。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio
import re

from .config import EngineSection
from .difficulty import LEVELS
from .isolation import IsolationError, engine_command
from .rules import Board, Choice, public_move
from .uci import EngineFailure, UciProcess


@dataclass(frozen=True)
class EngineMove:
    choice: Choice
    score_kind: str = ""
    score: Optional[int] = None
    depth: int = 0
    pv: List[str] = field(default_factory=list)

    def evidence(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"move": self.choice.move, "notation": self.choice.notation}
        if self.score is not None:
            result.update(score_type=self.score_kind, score=self.score, depth=self.depth, pv=self.pv)
        return result


def move_from_info(board: Board, lines: List[str], best: str) -> EngineMove:
    """严格采用原生降强后的 bestmove；分析只附在对应走法上，缺失时不编造评分。"""
    legal = {choice.move: choice for choice in board.choices()}
    try:
        best_move = public_move(best)
    except ValueError:
        raise EngineFailure("引擎没有返回有效的 bestmove，棋局已保留") from None
    if best_move not in legal:
        raise EngineFailure("引擎走法未通过规则库校验，棋局已保留")
    result = EngineMove(legal[best_move])
    for line in lines:
        match = re.search(r"\bdepth (\d+).*?\bscore (cp|mate) (-?\d+) (.*?)\bpv (.+)$", line)
        if not match or "bound" in match[4]:
            continue
        try:
            pv = [public_move(move) for move in match[5].split()[:6]]
            depth = int(match[1])
            if not pv or pv[0] != best_move or depth < result.depth:
                continue
            future = Board(board.fen)
            valid_pv = []
            for move in pv:
                if move not in future.legal_moves():
                    break
                valid_pv.append(move)
                future.push(move)
            result = EngineMove(legal[best_move], match[2], int(match[3]), depth, valid_pv)
        except ValueError:
            continue
    return result


async def _analyse(process: UciProcess, board: Board, settings: EngineSection) -> EngineMove:
    try:
        async with asyncio.timeout(settings.movetime_ms / 1000 + 8):
            spec = LEVELS[settings.difficulty]
            if not board.legal_moves():
                raise EngineFailure("当前局面没有合法着法")
            # 每次搜索设置本局档位，避免共用进程的不同群互相影响。
            # 原生 Skill 内部自动扩展候选；只采用最终 bestmove，不从 MultiPV 重选。
            for option, value in (
                ("Hash", settings.hash_mb),
                ("MultiPV", 1),
                ("UCI_LimitStrength", "false"),
                ("Skill Level", spec.skill),
            ):
                await process.send(f"setoption name {option} value {value}")
            # 每局每步重新初始化，复用进程但不让其他群的搜索状态泄漏进本次分析。
            await process.send("ucinewgame")
            await process.send("isready")
            await process.read_until("readyok")
            # 插件有自己的和棋裁判；不把历史计数交给引擎提前判和。
            fields = board.fen.split()
            fields[4] = "0"
            await process.send("position fen " + " ".join(fields))
            await process.send(f"go movetime {settings.movetime_ms}")
            lines = await process.read_until("bestmove")
            return move_from_info(board, lines, lines[-1].split()[1])
    except TimeoutError:
        raise EngineFailure("引擎响应超时，棋局已保留，请检查引擎安装后重试") from None
    except (OSError, ValueError, IndexError) as exc:
        raise EngineFailure("引擎通信或协议解析失败，请检查安装和 CPU 设置") from exc


class Engine:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.lock = asyncio.Lock()
        self._data_dir = data_dir
        self._session: Optional[UciProcess] = None
        self._settings: Optional[EngineSection] = None
        self._closed = False

    async def _prepare(self, settings: EngineSection) -> UciProcess:
        if self._closed:
            raise EngineFailure("引擎已随插件卸载")
        try:
            command = await asyncio.to_thread(
                engine_command, settings.executable, settings.cpu, self._data_dir
            )
        except IsolationError as exc:
            raise EngineFailure(str(exc)) from None
        if self._session is not None and self._session.command == command and self._session.alive:
            return self._session
        # 新配置先成功握手再替换；启动失败仍可继续使用旧配置。
        replacement = UciProcess(command)
        await replacement.start()
        old, self._session = self._session, replacement
        if old is not None:
            await old.close()
        return replacement

    async def start(self, settings: EngineSection) -> None:
        async with self.lock:
            if self._closed:
                raise EngineFailure("引擎已随插件卸载")
            if settings.enabled:
                await self._prepare(settings)
            elif self._session is not None:
                old, self._session = self._session, None
                await old.close()
            self._settings = settings.model_copy(deep=True)

    async def analyse(self, board: Board, settings: EngineSection) -> EngineMove:
        async with self.lock:
            # 排队请求保留棋局难度快照，但不能撤销之后生效的引擎启停、路径或 CPU 设置。
            runtime_settings = self._settings if self._settings is not None else settings
            if not runtime_settings.enabled:
                raise EngineFailure("引擎未启用")
            session = await self._prepare(runtime_settings)
            try:
                return await _analyse(session, board, settings)
            except BaseException:
                # 包括取消：丢弃未读完的协议流，下次重试重新握手，不自动代走。
                self._session = None
                await session.close()
                raise

    async def close(self) -> None:
        async with self.lock:
            self._closed = True
            if self._session is not None:
                old, self._session = self._session, None
                await old.close()
