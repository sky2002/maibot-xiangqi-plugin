"""按聊天流隔离棋局；耗时模型请求不占用宿主命令 RPC。"""

from pathlib import Path
from typing import Any, Coroutine, Dict, List, Optional, Set, Tuple

import asyncio
import base64
import time

from .config import ChessSection, EngineSection
from .difficulty import LEVELS, label, opening, parse_level
from .engine import Engine, EngineFailure
from .llm import ModelFailure, Player
from .render import render_board
from .rules import Choice, outcome
from .store import Game, Store


HELP = """中国象棋 · 每群一盘，只有发起者可以落子
下棋 开始 [红|黑] [难度]：例如「下棋 开始 黑 简单」
下棋 难度：查看本局难度；「下棋 难度 2」由棋手调整
难度：1入门、2简单、3标准、4困难、5挑战；不是等级分
入门和简单包含适度失误，让初学者有机会抓住弱点。
下棋 炮八平五：中文棋谱（支持前车、后马等）
下棋 b2 e2：固定坐标，红方在下，a0 左下、i9 右上
下棋 把b2的炮移到e2：自然语言，歧义时让你选择
下棋 选择 1：从刚才的候选中确认一步
下棋 棋盘：所有群友均可查看，不延长占桌时间
下棋 悔棋：撤回最近自己的走法及 bot 回应，思考期间不可用
下棋 重试：bot 选招失败后继续
下棋 认输：发起者结束棋局
下棋 结束：已配置的本群管理员强制结束，不计胜负
默认引擎提供候选、LLM 拍板，程序检查合法性。失败保留棋局，不自动代选。
bot 落子后自动解说；棋手直接聊本局即可，无需聊天指令。聊天不会落子。
采用简化和棋规则：重复局面或连续无吃子达到阈值即和棋，长将长捉不单独判责。
对局支持重启续玩；空闲超时结束，不计胜负。结束后可看最后棋盘，不能悔棋。"""


class Service:
    def __init__(
        self,
        ctx: Any,
        data_dir: Path,
        settings: ChessSection,
        engine_settings: Optional[EngineSection] = None,
    ):
        self.ctx = ctx
        self.settings = settings
        self.engine_settings = engine_settings or EngineSection()
        self.engine = Engine()
        self.analysis: Dict[str, Tuple[str, Tuple[str, ...], Dict[str, Any]]] = {}
        self.chat_last: Dict[str, float] = {}
        self.chat_busy: Set[str] = set()
        self.store = Store(data_dir / "xiangqi.sqlite3")
        self.jobs: Dict[str, asyncio.Task] = {}
        self.tasks: Set[asyncio.Task] = set()
        self.aux: Set[asyncio.Task] = set()
        self.locks: Dict[str, asyncio.Lock] = {}
        self.pending: Dict[str, Tuple[str, Tuple[str, ...], List[Choice]]] = {}
        self.sweeper: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # 启动时验证保存的棋谱；损坏的数据库应暴露错误，不能偷偷重置。
        for game in self.store.all():
            game.position()
        await self.expire(notify=False)
        self.sweeper = asyncio.create_task(self._sweep(), name="xiangqi-idle-sweeper")

    async def close(self) -> None:
        tasks = self.tasks | self.aux
        if self.sweeper:
            tasks.add(self.sweeper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()

    def _same(self, snapshot: Game) -> bool:
        current = self.store.get(snapshot.stream_id)
        return (
            current is not None
            and current.id == snapshot.id
            and current.moves == snapshot.moves
            and current.result == snapshot.result
            and current.difficulty == snapshot.difficulty
        )

    async def _text(self, stream_id: str, text: str) -> None:
        async with asyncio.timeout(10):
            sent = await self.ctx.send.text(text, stream_id)
        if sent is False:
            raise RuntimeError("发送文本失败")

    async def _show(self, game: Game, message: str = "") -> None:
        position = game.position()
        png = await asyncio.to_thread(
            render_board, position.board, list(game.moves), game.human_red, bool(game.result)
        )
        if not self._same(game):
            return
        async with asyncio.timeout(10):
            sent = await self.ctx.send.image(base64.b64encode(png).decode("ascii"), game.stream_id)
        if sent is False:
            raise RuntimeError("发送棋盘失败")
        status = game.result if game.result else "轮到 maibot。" if game.bot_turn else "轮到你走。"
        status += f" 难度：{label(game.difficulty)}。" if self.engine_settings.enabled else " 纯 LLM 模式。"
        if position.board.in_check and not game.result:
            status += "将军！"
        await self._text(game.stream_id, "\n".join(x for x in (message, status) if x))

    def _cancel(self, stream_id: str) -> None:
        task = self.jobs.pop(stream_id, None)
        if task:
            task.cancel()
        self.pending.pop(stream_id, None)

    def _launch(self, game: Game, operation: Coroutine[Any, Any, None]) -> None:
        async def guarded() -> None:
            try:
                await operation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ctx.logger.error("象棋后台任务失败：%s", type(exc).__name__, exc_info=True)
                current = self.store.get(game.stream_id)
                if current and current.id == game.id and not current.result:
                    try:
                        await self._show(
                            current,
                            "处理失败，棋局已保留。请用「下棋 棋盘」查看；轮到 bot 时可用「下棋 重试」。",
                        )
                    except Exception:
                        self.ctx.logger.exception("发送象棋故障提示失败")

        task = asyncio.create_task(guarded(), name=f"xiangqi-{game.id}")
        self.jobs[game.stream_id] = task
        self.tasks.add(task)

        def done(completed: asyncio.Task) -> None:
            self.tasks.discard(completed)
            if self.jobs.get(game.stream_id) is completed:
                self.jobs.pop(game.stream_id, None)
            # 在任务尚未首次执行时取消，内部 coroutine 也需要关闭。
            operation.close()

        task.add_done_callback(done)

    def _apply_move(self, game: Game, choice: Choice) -> None:
        game.position().board.push(choice.move)
        game.moves.append(choice.move)
        game.updated_at = time.time()
        game.result = outcome(game.position(), game.repetition, game.no_capture_limit)
        self.pending.pop(game.stream_id, None)
        self.store.save(game)

    async def _accepted(self, game: Game, choice: Choice) -> None:
        self._apply_move(game, choice)
        if game.result:
            await self._show(game, f"你走了 {choice.notation}（{choice.move[:2]} → {choice.move[2:]}）。")
        else:
            self._launch(game, self._bot(game, f"已落子：{choice.notation}。思考中……"))

    async def _bot(self, snapshot: Game, notice: str) -> None:
        await self._text(snapshot.stream_id, notice)
        player = Player(self.ctx, self.settings.model_copy(deep=True))
        evidence = None
        try:
            board = snapshot.position().board
            if self.engine_settings.enabled:
                engine_settings = self.engine_settings.model_copy(deep=True)
                engine_settings.difficulty = snapshot.difficulty
                candidates = await self.engine.analyse(board, engine_settings)
                if not self._same(snapshot):
                    return
                choice = await player.select(board, list(snapshot.moves), candidates)
                evidence = {
                    "side": "红" if board.red_turn else "黑",
                    "fen": board.fen,
                    "selected": next(c.evidence() for c in candidates if c.choice.move == choice.move),
                }
            else:
                choice = await player.select(board, list(snapshot.moves))
        except (ModelFailure, EngineFailure) as exc:
            if self._same(snapshot):
                await self._show(snapshot, f"{exc} 已保留当前局面，请发送「下棋 重试」。")
            return
        # 模型返回时重新校验局号、完整棋谱和结束状态，丢弃过期回复。
        if not self._same(snapshot):
            return
        self._apply_move(snapshot, choice)
        if evidence:
            self.analysis[snapshot.stream_id] = (snapshot.id, tuple(snapshot.moves), evidence)
        await self._show(snapshot, f"maibot：{choice.notation}（{choice.move[:2]} → {choice.move[2:]}）。")
        if player.settings.commentary:
            task = asyncio.create_task(self._comment(snapshot, choice, player))
            self.aux.add(task)
            task.add_done_callback(self.aux.discard)

    async def _comment(self, game: Game, choice: Choice, player: Player) -> None:
        try:
            text = await player.comment(game.position().board, choice, self._evidence(game))
            if text and self._same(game):
                await self._text(game.stream_id, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ctx.logger.warning("本轮棋评生成或发送失败，落子与棋盘不受影响。")

    def _evidence(self, game: Game) -> Optional[Dict[str, Any]]:
        saved = self.analysis.get(game.stream_id)
        return saved[2] if saved and saved[:2] == (game.id, tuple(game.moves)) else None

    async def chat(
        self, stream_id: str, group_id: str, platform: str, user_id: str, text: str, message_id: str = ""
    ) -> bool:
        """只有成功发送了本局聊天回复才拦截普通回复；从不更改棋谱。"""
        text = text.strip()
        if (
            not self.settings.auto_chat
            or not text
            or len(text) > 300
            or text.startswith(("下棋", "/", "!", "！"))
        ):
            return False
        game = self.store.get(stream_id)
        if (
            not group_id
            or not user_id
            or not platform
            or not game
            or game.result
            or (game.platform, game.group_id, game.owner) != (platform, group_id, user_id)
            or time.time() - game.updated_at >= game.idle_seconds
        ):
            return False
        now = time.monotonic()
        if (
            stream_id in self.chat_busy
            or now - self.chat_last.get(stream_id, -float("inf")) < self.settings.chat_cooldown
        ):
            return False
        if not self.store.claim_message(stream_id, message_id):
            return False
        self.chat_last[stream_id] = now
        self.chat_busy.add(stream_id)
        task = asyncio.current_task()
        self.aux.add(task)
        try:
            player = Player(self.ctx, self.settings.model_copy(deep=True))
            reply = await player.chat(game.position().board, text, game.human_red, self._evidence(game))
            if reply and self._same(game):
                await self._text(stream_id, reply)
                return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ctx.logger.warning("象棋自动聊天未完成，继续普通聊天处理。")
        finally:
            self.chat_busy.discard(stream_id)
            self.aux.discard(task)
        return False

    async def _candidates(self, game: Game, choices: List[Choice]) -> None:
        if not choices:
            await self._text(
                game.stream_id,
                "没有识别到明确的合法走法。请补充起点和终点，或使用「下棋 炮八平五」「下棋 b2 e2」。",
            )
            return
        if len(choices) > 12:
            await self._text(game.stream_id, "可能的走法太多，请用棋盘坐标补充起点和终点。")
            return
        self.pending[game.stream_id] = (game.id, tuple(game.moves), choices)
        game.updated_at = time.time()
        self.store.save(game)
        lines = [f"{i}. {c.notation}（{c.move[:2]} → {c.move[2:]}）" for i, c in enumerate(choices, 1)]
        await self._text(
            game.stream_id,
            "这条指令需要你确认：\n" + "\n".join(lines) + "\n请发送「下棋 选择 编号」，或重新描述走法。",
        )

    async def _interpret(self, snapshot: Game, instruction: str) -> None:
        await self._text(snapshot.stream_id, "正在解析这条走法……")
        player = Player(self.ctx, self.settings.model_copy(deep=True))
        try:
            choices, ambiguous = await player.interpret(snapshot.position().board, instruction)
        except ModelFailure as exc:
            if self._same(snapshot):
                await self._text(snapshot.stream_id, str(exc))
            return
        if not self._same(snapshot):
            return
        if len(choices) == 1 and not ambiguous:
            await self._accepted(snapshot, choices[0])
        else:
            await self._candidates(snapshot, choices)

    async def handle(
        self,
        stream_id: str,
        group_id: str,
        platform: str,
        user_id: str,
        command: str,
        message_id: str = "",
        local_operator: bool = False,
    ) -> None:
        if not stream_id:
            return
        if not group_id or not platform or not user_id:
            await self._text(stream_id, "象棋对局需要在群聊中发起，并且消息必须包含群和用户身份。")
            return
        async with self.locks.setdefault(stream_id, asyncio.Lock()):
            if not self.store.claim_message(stream_id, message_id):
                return
            command = command.strip()
            if command in ("", "帮助", "help"):
                await self._text(stream_id, HELP)
                return
            if len(command) > 300:
                await self._text(stream_id, "指令太长，请在 300 字以内描述走法。")
                return
            game = self.store.get(stream_id)
            if game and not game.result and time.time() - game.updated_at >= game.idle_seconds:
                self._end(game, "空闲超时，对局结束，不计胜负。")
                await self._text(stream_id, game.result)
            if command.split()[0] == "开始":
                try:
                    human_red, difficulty = opening(command, self.engine_settings.difficulty)
                except ValueError as exc:
                    await self._text(stream_id, str(exc))
                    return
                if game and not game.result:
                    await self._text(stream_id, "本群已有一盘棋，结束后才能重新开始。可用「下棋 棋盘」查看。")
                    return
                game = Game(
                    stream_id,
                    platform,
                    group_id,
                    user_id,
                    human_red=human_red,
                    difficulty=difficulty,
                    repetition=self.settings.repetition,
                    no_capture_limit=self.settings.no_capture_halfmoves,
                    idle_seconds=self.settings.idle_minutes * 60,
                )
                self.store.save(game)
                self.pending.pop(stream_id, None)
                await self._show(
                    game,
                    f"对局开始，你执{'红' if game.human_red else '黑'}。仅发起者可落子。\n简化和棋：重复 {game.repetition} 次或 {game.no_capture_limit} 个半回合无吃子；空闲 {game.idle_seconds // 60} 分钟结束。",
                )
                if game.bot_turn:
                    self._launch(game, self._bot(game, "maibot 执红先走，思考中……"))
                return
            if command == "难度":
                levels = "、".join(label(level) for level in LEVELS)
                current = (
                    f"本局难度：{label(game.difficulty)}。"
                    if game
                    else f"新局默认难度：{label(self.engine_settings.difficulty)}。"
                )
                mode = (
                    ""
                    if self.engine_settings.enabled
                    else "当前是纯 LLM 模式，难度限制仅在启用引擎时生效。\n"
                )
                await self._text(
                    stream_id,
                    f"{mode}{current}\n可选：{levels}。\n发起者可用「下棋 难度 简单」调整，正在思考时需等待；从下一次搜索生效。",
                )
                return
            if not game:
                await self._text(stream_id, "本群还没有棋局，请发送「下棋 开始」。")
                return
            if command == "棋盘":
                await self._show(
                    game,
                    "bot 正在处理走法。"
                    if stream_id in self.jobs
                    else "轮到 bot，可发送「下棋 重试」继续。"
                    if game.bot_turn and not game.result
                    else "",
                )
                return
            if game.result:
                await self._text(stream_id, f"{game.result}\n可发送「下棋 开始」开新局。")
                return
            if command == "结束":
                if not local_operator and f"{platform}:{group_id}:{user_id}" not in self.settings.admins:
                    await self._text(stream_id, "只有已配置的本群管理员能强制结束。发起者可用「下棋 认输」。")
                    return
                self._end(game, "管理员结束了对局，不计胜负。")
                await self._show(game)
                return
            if user_id != game.owner:
                await self._text(stream_id, "这盘棋只有发起者可以操作，你可以用「下棋 棋盘」围观。")
                return
            if command == "认输":
                self._end(game, f"玩家认输，{'黑' if game.human_red else '红'}方 maibot 获胜。")
                await self._show(game)
                return
            if stream_id in self.jobs:
                await self._text(stream_id, "正在处理上一条走法，请稍候。思考期间不能落子、悔棋或调整难度。")
                return
            if command.split()[0] == "难度":
                if not self.engine_settings.enabled:
                    await self._text(stream_id, "当前是纯 LLM 模式，请由部署者启用引擎后再调难度。")
                    return
                parts = command.split()
                difficulty = parse_level(parts[1]) if len(parts) == 2 else None
                if difficulty is None:
                    await self._text(
                        stream_id, "难度请选择 1–5 或入门、简单、标准、困难、挑战，例如「下棋 难度 2」。"
                    )
                    return
                game.difficulty = difficulty
                self.store.save(game)
                self.analysis.pop(stream_id, None)
                await self._text(
                    stream_id, f"本局难度已设为 {label(difficulty)}，从下一次 bot 搜索生效；棋谱保持不变。"
                )
                return
            if command == "悔棋":
                human_indices = [i for i in range(len(game.moves)) if (i % 2 == 0) == game.human_red]
                if not human_indices:
                    await self._text(stream_id, "你还没有走过棋，无需悔棋。")
                    return
                game.moves = game.moves[: human_indices[-1]]
                game.updated_at = time.time()
                self.store.save(game)
                self.pending.pop(stream_id, None)
                await self._show(game, "已撤回你最近的一步及其后的 bot 回应。")
                return
            if command == "重试":
                if not game.bot_turn:
                    await self._text(stream_id, "现在轮到你走，无需重试。")
                    return
                game.updated_at = time.time()
                self.store.save(game)
                self._launch(game, self._bot(game, "重新思考中……"))
                return
            if game.bot_turn:
                await self._text(
                    stream_id, "现在轮到 bot，请发送「下棋 重试」继续，或用「下棋 悔棋」撤回自己的上一步。"
                )
                return
            if command.startswith("选择"):
                saved = self.pending.get(stream_id)
                number = command[2:].strip()
                if not saved or saved[:2] != (game.id, tuple(game.moves)):
                    await self._text(stream_id, "没有待确认的走法（重启后请重新描述）。")
                    return
                if not number.isascii() or not number.isdigit() or not 1 <= int(number) <= len(saved[2]):
                    await self._text(stream_id, "请使用候选列表中的编号，例如「下棋 选择 1」。")
                    return
                await self._accepted(game, saved[2][int(number) - 1])
                return
            self.pending.pop(stream_id, None)
            parsed = game.position().board.parse(command)
            if parsed is None:
                self._launch(game, self._interpret(game, command))
            elif not parsed:
                await self._text(
                    stream_id, "这步棋不合法。请检查棋子位置、走法和是否让己方被将军；棋盘没有改变。"
                )
            elif len(parsed) > 1:
                await self._candidates(game, parsed)
            else:
                await self._accepted(game, parsed[0])

    def _end(self, game: Game, reason: str) -> None:
        game.result = reason
        self.store.save(game)
        self._cancel(game.stream_id)

    async def expire(self, notify: bool = True) -> None:
        for game in self.store.all():
            if not game.result and time.time() - game.updated_at >= game.idle_seconds:
                self._end(game, "空闲超时，对局结束，不计胜负。")
                if notify:
                    try:
                        await self._text(game.stream_id, game.result)
                    except Exception:
                        self.ctx.logger.exception("象棋超时提示发送失败")

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.expire()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.ctx.logger.exception("象棋空闲清理失败")
