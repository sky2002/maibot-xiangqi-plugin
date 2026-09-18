"""按原生 Skill Level 分档；数值是初始映射，不映射真人等级分。"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Difficulty:
    name: str
    skill: int


LEVELS = {
    1: Difficulty("入门", -20),
    2: Difficulty("简单", -12),
    3: Difficulty("标准", -4),
    4: Difficulty("困难", 4),
    5: Difficulty("挑战", 12),
    6: Difficulty("全力", 20),
}


def parse_level(text: str) -> Optional[int]:
    if text == "超人类":  # 兼容旧指令，存档仍使用数字 6。
        return 6
    return next((level for level, spec in LEVELS.items() if text in (str(level), spec.name)), None)


def label(level: int) -> str:
    return f"{level}（{LEVELS[level].name}）"


def opening(command: str, default: int) -> Tuple[bool, int]:
    """接受「开始 [红|黑] [难度]」，颜色与难度也可交换顺序。"""
    tokens = command.split()
    if not tokens or tokens[0] != "开始" or len(tokens) > 3:
        raise ValueError("请使用「下棋 开始 [红|黑] [1–6或难度名]」，例如「下棋 开始 黑 简单」。")
    color, level = None, None
    for token in tokens[1:]:
        if token in ("红", "黑", "红方", "黑方") and color is None:
            color = "黑" not in token
        elif parse_level(token) is not None and level is None:
            level = parse_level(token)
        else:
            raise ValueError("开局参数有误：颜色与难度各选一个，例如「下棋 开始 黑 简单」。")
    return color if color is not None else True, level if level is not None else default
