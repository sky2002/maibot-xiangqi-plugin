"""低档结合浅搜索和受控失误分档，不映射真人等级分。"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Difficulty:
    name: str
    depth: int  # 0 表示仅受部署者设置的搜索时间限制。
    mistake_rate: float = 0
    min_loss: int = 0
    max_loss: int = 0


LEVELS = {
    1: Difficulty("入门", 1, 0.85, 100, 600),
    2: Difficulty("简单", 2, 0.60, 50, 300),
    3: Difficulty("标准", 6),
    4: Difficulty("困难", 10),
    5: Difficulty("挑战", 0),
}


def parse_level(text: str) -> Optional[int]:
    return next((level for level, spec in LEVELS.items() if text in (str(level), spec.name)), None)


def label(level: int) -> str:
    return f"{level}（{LEVELS[level].name}）"


def opening(command: str, default: int) -> Tuple[bool, int]:
    """接受「开始 [红|黑] [难度]」，颜色与难度也可交换顺序。"""
    tokens = command.split()
    if not tokens or tokens[0] != "开始" or len(tokens) > 3:
        raise ValueError("请使用「下棋 开始 [红|黑] [1–5或难度名]」，例如「下棋 开始 黑 简单」。")
    color, level = None, None
    for token in tokens[1:]:
        if token in ("红", "黑", "红方", "黑方") and color is None:
            color = "黑" not in token
        elif parse_level(token) is not None and level is None:
            level = parse_level(token)
        else:
            raise ValueError("开局参数有误：颜色与难度各选一个，例如「下棋 开始 黑 简单」。")
    return color if color is not None else True, level if level is not None else default
