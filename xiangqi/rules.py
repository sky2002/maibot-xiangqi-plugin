"""只用 pyffish 的规则接口；所有模型和用户输入先校验再传入原生库。"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import re
import unicodedata

import pyffish


VARIANT = "xiangqi"
START_FEN = pyffish.start_fen(VARIANT)
DIGITS = "零一二三四五六七八九"
PIECES = dict(zip("RNBAKCP rnbakcp".replace(" ", ""), "车马相仕帅炮兵车马象士将炮卒", strict=True))
MOVE_RE = re.compile(r"([a-i])([0-9])([a-i])([0-9])")
NATIVE_RE = re.compile(r"([a-i])(10|[1-9])([a-i])(10|[1-9])")
TRANSLATION = str.maketrans(
    "零一二三四五六七八九車馬砲進後俥傌帥將象士卒", "0123456789车马炮进后车马帅帅相仕兵"
)


def normalize(text: str) -> str:
    """统一棋谱数字、繁简字和两方同类棋子名称。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).translate(TRANSLATION).replace("将", "帅")


def native_move(move: str) -> str:
    match = MOVE_RE.fullmatch(move)
    if match is None:
        raise ValueError("坐标走法格式错误")
    a, b, c, d = match.groups()
    return f"{a}{int(b) + 1}{c}{int(d) + 1}"


def public_move(move: str) -> str:
    match = NATIVE_RE.fullmatch(move)
    if match is None:
        raise ValueError("规则库返回了无法识别的坐标")
    a, b, c, d = match.groups()
    return f"{a}{int(b) - 1}{c}{int(d) - 1}"


def squares(fen: str) -> Dict[str, str]:
    result = {}
    for row, cells in enumerate(fen.split()[0].split("/")):
        x = 0
        for cell in cells:
            if cell.isdigit():
                x += int(cell)
            else:
                result[f"{chr(97 + x)}{9 - row}"] = cell
                x += 1
    return result


@dataclass(frozen=True)
class Choice:
    move: str
    notation: str
    aliases: Tuple[str, ...]


class Board:
    """插件坐标固定为 a0–i9，红方在下；隔离 pyffish 的 1–10 行号。"""

    def __init__(self, fen: str = START_FEN):
        self.fen = fen

    @property
    def red_turn(self) -> bool:
        return self.fen.split()[1] == "w"

    @property
    def key(self) -> str:
        return " ".join(self.fen.split()[:2])

    @property
    def in_check(self) -> bool:
        return bool(pyffish.gives_check(VARIANT, self.fen, []))

    def legal_moves(self) -> List[str]:
        return sorted(public_move(move) for move in pyffish.legal_moves(VARIANT, self.fen, []))

    def push(self, move: str) -> bool:
        if move not in self.legal_moves():
            raise ValueError("这步棋不合法：请检查走法、蹩马腿、塞象眼或是否让己方被将军。")
        capture = move[2:] in squares(self.fen)
        self.fen = pyffish.get_fen(VARIANT, self.fen, [native_move(move)])
        return capture

    def choices(self) -> List[Choice]:
        cells = squares(self.fen)
        result = []
        for move in self.legal_moves():
            src, dst = move[:2], move[2:]
            piece = cells[src]
            red = piece.isupper()
            x, y = ord(src[0]) - 97, int(src[1])
            dx, dy = ord(dst[0]) - 97, int(dst[1])
            file_no = (9 - x) if red else (x + 1)
            target_file = (9 - dx) if red else (dx + 1)
            action = "平" if y == dy else "进" if (dy > y) == red else "退"
            target = target_file if action == "平" or piece.upper() in "NBA" else abs(dy - y)

            def num(n: int, is_red: bool = red) -> str:
                return DIGITS[n] if is_red else str(n)

            name = PIECES[piece]
            base = f"{name}{num(file_no)}{action}{num(target)}"
            aliases = [base]
            peers = sorted(
                [sq for sq, p in cells.items() if p == piece and sq[0] == src[0]],
                key=lambda sq: int(sq[1]),
                reverse=red,
            )
            label = base
            if len(peers) > 1:
                index = peers.index(src)
                prefix = (
                    "前"
                    if index == 0
                    else "后"
                    if index == len(peers) - 1
                    else "中"
                    if len(peers) == 3
                    else DIGITS[index + 1]
                )
                label = f"{prefix}{name}{action}{num(target)}"
                aliases.append(label)
            result.append(Choice(move, label, tuple(normalize(a) for a in aliases)))
        return result

    def parse(self, text: str) -> Optional[List[Choice]]:
        """None 表示需要自然语言解析；空列表表示明确棋谱/坐标但走法不合法。"""
        text = unicodedata.normalize("NFKC", text.strip()).lower()
        coordinate = re.fullmatch(r"([a-i][0-9])\s*(?:到|至|->|→|[-,，])?\s*([a-i][0-9])", text)
        choices = self.choices()
        if coordinate:
            move = "".join(coordinate.groups())
            return [c for c in choices if c.move == move]
        compact = normalize(text)
        if re.fullmatch(r"(?:[车马相仕帅炮兵][1-9]|[前中后1-5][车马相仕帅炮兵])[进退平][1-9]", compact):
            return [c for c in choices if compact in c.aliases]
        return None


@dataclass
class Position:
    board: Board
    keys: List[str]
    no_capture: int


def replay(moves: List[str]) -> Position:
    board = Board()
    keys = [board.key]
    no_capture = 0
    for move in moves:
        no_capture = 0 if board.push(move) else no_capture + 1
        keys.append(board.key)
    return Position(board, keys, no_capture)


def outcome(position: Position, repetition: int, no_capture_limit: int) -> str:
    board = position.board
    # 无合法着法优先于简化和棋：象棋的困毙同样判负。
    if not board.legal_moves():
        return f"{'黑' if board.red_turn else '红'}方获胜（{'将死' if board.in_check else '困毙'}）"
    if position.keys.count(board.key) >= repetition:
        return f"和棋：相同局面且同一方行棋出现 {repetition} 次。"
    if position.no_capture >= no_capture_limit:
        return f"和棋：连续 {no_capture_limit} 个半回合没有吃子。"
    return ""
