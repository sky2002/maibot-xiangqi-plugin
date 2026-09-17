"""SQLite 原子保存棋谱；模型输出和群聊内容不写入棋局。"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import json
import sqlite3
import time

from .rules import Position, replay


@dataclass
class Game:
    stream_id: str
    platform: str
    group_id: str
    owner: str
    human_red: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    moves: List[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    result: str = ""
    # 规则在开局时快照，配置热更新不会改变正在进行的棋局。
    repetition: int = 3
    no_capture_limit: int = 120
    idle_seconds: int = 1800
    # 旧存档缺省为挑战，保持升级前只有时间限制的搜索方式。
    difficulty: int = 5

    def position(self) -> Position:
        return replay(self.moves)

    @property
    def bot_turn(self) -> bool:
        return (len(self.moves) % 2 == 0) != self.human_red


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS games (stream_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS receipts (seq INTEGER PRIMARY KEY, stream_id TEXT, message_id TEXT, UNIQUE(stream_id, message_id))"
        )
        self.db.commit()

    def get(self, stream_id: str) -> Optional[Game]:
        row = self.db.execute("SELECT data FROM games WHERE stream_id = ?", (stream_id,)).fetchone()
        return Game(**json.loads(row[0])) if row else None

    def save(self, game: Game) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO games VALUES (?, ?)",
                (game.stream_id, json.dumps(asdict(game), ensure_ascii=False)),
            )

    def all(self) -> List[Game]:
        return [Game(**json.loads(row[0])) for row in self.db.execute("SELECT data FROM games")]

    def claim_message(self, stream_id: str, message_id: str) -> bool:
        if not message_id:
            return True
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO receipts(stream_id, message_id) VALUES (?, ?)", (stream_id, message_id)
            )
            self.db.execute(
                "DELETE FROM receipts WHERE stream_id = ? AND seq NOT IN (SELECT seq FROM receipts WHERE stream_id = ? ORDER BY seq DESC LIMIT 100)",
                (stream_id, stream_id),
            )
            return cursor.rowcount == 1

    def close(self) -> None:
        self.db.close()
