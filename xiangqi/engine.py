"""短时单线程 UCI 搜索；跨群串行，退出/取消时回收子进程。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import asyncio
import random
import re

from .config import EngineSection
from .difficulty import LEVELS
from .isolation import IsolationError, engine_command
from .rules import Board, Choice, public_move


class EngineFailure(RuntimeError):
    pass


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


async def search(command: List[str], board: Board, settings: EngineSection) -> List[Candidate]:
    """传输层独立可测；生产调用者必须先通过 CPU 隔离核验。"""
    process = None
    try:
        async with asyncio.timeout(settings.movetime_ms / 1000 + 8):
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=65536,
            )

            async def send(line: str) -> None:
                process.stdin.write((line + "\n").encode("ascii"))
                await process.stdin.drain()

            async def read_until(prefix: str) -> List[str]:
                lines = []
                while True:
                    raw = await process.stdout.readline()
                    if not raw:
                        raise EngineFailure("引擎提前退出，请检查可执行文件及 CPU 隔离设置")
                    line = raw.decode("utf-8", errors="replace").strip()
                    lines.append(line)
                    if len(lines) > 10000:
                        raise EngineFailure("引擎协议输出异常")
                    if line == prefix or line.startswith(prefix + " "):
                        return lines

            await send("uci")
            handshake = await read_until("uciok")
            for required in ("Threads", "Hash", "MultiPV", "UCI_Variant", "Use NNUE"):
                if not any(line.startswith(f"option name {required} type ") for line in handshake):
                    raise EngineFailure("引擎不兼容，请安装官方 Fairy-Stockfish 14 largeboard")
            if not any(" var xiangqi" in line for line in handshake):
                raise EngineFailure("引擎未包含象棋规则，请使用 largeboard 版本")
            spec = LEVELS[settings.difficulty]
            legal_count = len(board.legal_moves())
            # 有失误机制的档位必须评估最佳三招之外的着法，否则 LLM 总能挑回强招。
            count = legal_count if spec.mistake_rate else min(settings.candidates, legal_count)
            if not count:
                raise EngineFailure("当前局面没有合法着法")
            for option, value in (
                ("Threads", 1),
                ("Hash", settings.hash_mb),
                ("MultiPV", count),
                ("UCI_Variant", "xiangqi"),
                ("Use NNUE", "false"),
                ("Ponder", "false"),
                # Skill Level 主要改写最终 bestmove；此处从 MultiPV 交给 LLM 选招，
                # 所以通过真正限制搜索深度调级，不依赖 bestmove 的随机降强。
                ("Skill Level", 20),
                ("UCI_LimitStrength", "false"),
            ):
                await send(f"setoption name {option} value {value}")
            await send("ucinewgame")
            await send("isready")
            await read_until("readyok")
            # 插件有自己的和棋裁判；不把历史计数交给引擎提前判和。
            fields = board.fen.split()
            fields[4] = "0"
            await send("position fen " + " ".join(fields))
            depth = spec.depth
            await send(f"go movetime {settings.movetime_ms}" + (f" depth {depth}" if depth else ""))
            lines = await read_until("bestmove")
            ranked = candidates_from_info(board, lines, lines[-1].split()[1], count)
            return select_candidates(ranked, settings)
    except TimeoutError:
        raise EngineFailure("引擎响应超时，棋局已保留，请检查引擎安装后重试") from None
    except (OSError, ValueError, IndexError) as exc:
        raise EngineFailure("引擎启动或协议解析失败，请检查安装和隔离启动方式") from exc
    finally:
        if process is not None:
            # 不依赖引擎响应 quit，取消时也保证搜索不残留占用 CPU。
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()


class Engine:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()

    async def analyse(self, board: Board, settings: EngineSection) -> List[Candidate]:
        async with self.lock:
            try:
                command = engine_command(settings.executable)
            except IsolationError as exc:
                raise EngineFailure(str(exc)) from None
            return await search(command, board, settings)
