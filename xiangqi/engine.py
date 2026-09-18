"""由插件生命周期管理的引擎；跨群串行，失败/取消时回收子进程。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio
import random
import re

from .config import EngineSection
from .difficulty import LEVELS
from .isolation import IsolationError, engine_command
from .rules import Board, Choice, public_move
from .uci import EngineFailure, UciProcess


@dataclass(frozen=True)
class Candidate:
    choice: Choice
    score_kind: str
    score: int
    depth: int
    pv: List[str]

    def evidence(self) -> Dict[str, Any]:
        return {
            "move": self.choice.move,
            "notation": self.choice.notation,
            "score_type": self.score_kind,
            "score": self.score,
            "depth": self.depth,
            "pv": self.pv,
        }


def select_candidates(
    ranked: List[Candidate], settings: EngineSection, rng: Optional[random.Random] = None
) -> List[Candidate]:
    """先生成候选池再交给 LLM；失误回合不能从池中重新选回最优招。"""
    spec = LEVELS[settings.difficulty]
    normal = ranked[: settings.candidates]
    rng = rng if rng is not None else random.SystemRandom()
    if not ranked or not spec.mistake_rate:
        return normal
    safe = [c for c in ranked if not (c.score_kind == "mate" and c.score < 0)]
    normal = (safe or ranked)[: settings.candidates]
    if rng.random() >= spec.mistake_rate:
        return normal
    best = ranked[0]
    # 不把 mate 当普通数值运算：有非败招时，不主动提供已被引擎判为强制输棋的招。
    # 存在可杀时，允许错过杀棋、走另一条仍非强制输棋的路线。
    finite = [c for c in ranked if c.score_kind == "cp"]
    if best.score_kind == "mate":
        pool = [c for c in finite if finite[0].score - c.score <= spec.max_loss] if best.score > 0 else []
    else:
        pool = [c for c in finite if spec.min_loss <= best.score - c.score <= spec.max_loss]
        if not pool:
            # 分数差距不足最低目标时仍可选较小失误；不为凑数强行超过损失上限。
            pool = [c for c in finite if 0 < best.score - c.score <= spec.max_loss]
    if not pool:
        return normal
    selected = {c.choice.move for c in rng.sample(pool, min(settings.candidates, len(pool)))}
    return [c for c in ranked if c.choice.move in selected]


def candidates_from_info(board: Board, lines: List[str], best: str, count: int) -> List[Candidate]:
    legal = {choice.move: choice for choice in board.choices()}
    # 同一完成深度的 MultiPV 才可比较，舍弃被停止打断的最后一层。
    layers: Dict[int, Dict[int, Candidate]] = {}
    for line in lines:
        match = re.search(
            r"\bdepth (\d+).*?\bmultipv (\d+).*?\bscore (cp|mate) (-?\d+) (.*?)\bpv (.+)$", line
        )
        if not match or "bound" in match[5]:
            continue
        try:
            pv = [public_move(move) for move in match[6].split()[:6]]
            if not pv or pv[0] not in legal:
                continue
            future = Board(board.fen)
            valid_pv = []
            for move in pv:
                if move not in future.legal_moves():
                    break
                valid_pv.append(move)
                future.push(move)
            candidate = Candidate(legal[pv[0]], match[3], int(match[4]), int(match[1]), valid_pv)
            layers.setdefault(candidate.depth, {})[int(match[2])] = candidate
        except ValueError:
            continue
    try:
        best_move = public_move(best)
    except ValueError:
        raise EngineFailure("引擎没有返回有效的 bestmove，棋局已保留") from None
    if best_move not in legal:
        raise EngineFailure("引擎走法未通过规则库校验，棋局已保留")
    for depth in sorted(layers, reverse=True):
        layer = layers[depth]
        if all(i in layer for i in range(1, count + 1)):
            result = [layer[i] for i in range(1, count + 1)]
            if len({c.choice.move for c in result}) == count:
                return result
    raise EngineFailure("引擎未完成候选分析，请重试或增加 engine.movetime_ms")


async def _analyse(process: UciProcess, board: Board, settings: EngineSection) -> List[Candidate]:
    try:
        async with asyncio.timeout(settings.movetime_ms / 1000 + 8):
            spec = LEVELS[settings.difficulty]
            legal_count = len(board.legal_moves())
            # 有失误机制的档位必须评估最佳三招之外的着法，否则 LLM 总能挑回强招。
            count = legal_count if spec.mistake_rate else min(settings.candidates, legal_count)
            if not count:
                raise EngineFailure("当前局面没有合法着法")
            for option, value in (("Hash", settings.hash_mb), ("MultiPV", count)):
                await process.send(f"setoption name {option} value {value}")
            # 每局每步重新初始化，复用进程但不让其他群的搜索状态泄漏进本次分析。
            await process.send("ucinewgame")
            await process.send("isready")
            await process.read_until("readyok")
            # 插件有自己的和棋裁判；不把历史计数交给引擎提前判和。
            fields = board.fen.split()
            fields[4] = "0"
            await process.send("position fen " + " ".join(fields))
            # Skill Level 主要改写最终 bestmove；这里以 MultiPV 和真正的深度限制调级。
            depth = spec.depth
            await process.send(f"go movetime {settings.movetime_ms}" + (f" depth {depth}" if depth else ""))
            lines = await process.read_until("bestmove")
            ranked = candidates_from_info(board, lines, lines[-1].split()[1], count)
            return select_candidates(ranked, settings)
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

    async def analyse(self, board: Board, settings: EngineSection) -> List[Candidate]:
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
