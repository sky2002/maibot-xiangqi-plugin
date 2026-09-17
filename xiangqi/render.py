"""Pillow 棋盘，无浏览器、系统字体或网络依赖。"""

from io import BytesIO
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .rules import DIGITS, PIECES, Board, squares


FONT = Path(__file__).parent / "assets" / "MaiBotXiangqi.otf"
INK = "#40362d"
RED = "#b93432"
BLUE = "#287a86"


def point(square: str) -> Tuple[int, int]:
    return 100 + (ord(square[0]) - 97) * 80, 180 + (9 - int(square[1])) * 80


def render_board(board: Board, moves: List[str], human_red: bool, finished: bool = False) -> bytes:
    image = Image.new("RGB", (840, 1100), "#f8f3e9")
    draw = ImageDraw.Draw(image)
    fonts = {size: ImageFont.truetype(str(FONT), size) for size in (18, 22, 26, 34, 40)}

    def text(x: float, y: float, value: str, size: int = 22, fill: str = INK, anchor: str = "mm") -> None:
        draw.text((x, y), value, font=fonts[size], fill=fill, anchor=anchor)

    text(52, 48, "与麦麦下象棋", 34, anchor="lm")
    turn = "对局已结束" if finished else ("红方行棋" if board.red_turn else "黑方行棋")
    draw.rounded_rectangle((584, 27, 790, 71), radius=18, fill="#eee4d4")
    text(687, 49, turn, 22)
    text(52, 91, f"玩家执{'红' if human_red else '黑'} · 第 {len(moves) // 2 + 1} 回合", 18, anchor="lm")
    draw.rounded_rectangle((72, 152, 768, 928), radius=10, fill="#f0dfbd", outline="#d7bf95", width=2)
    for x in range(9):
        cx = 100 + 80 * x
        if x in (0, 8):
            draw.line((cx, 180, cx, 900), fill=INK, width=2)
        else:
            draw.line((cx, 180, cx, 500), fill=INK, width=2)
            draw.line((cx, 580, cx, 900), fill=INK, width=2)
        text(cx, 132, chr(97 + x), 18)
        text(cx, 954, DIGITS[9 - x], 22, RED)
    for y in range(10):
        cy = 180 + y * 80
        draw.line((100, cy, 740, cy), fill=INK, width=2)
        text(44, cy, str(9 - y), 18)
        text(796, cy, str(9 - y), 18)
    for top in (180, 740):
        draw.line((340, top, 500, top + 160), fill=INK, width=2)
        draw.line((500, top, 340, top + 160), fill=INK, width=2)
    text(260, 540, "楚 河", 26)
    text(580, 540, "汉 界", 26)
    # 最近双方着法使用不同颜色；起点虚环，终点实环，颜色由棋子阵营决定。
    for index in range(max(0, len(moves) - 2), len(moves)):
        move = moves[index]
        color = RED if index % 2 == 0 else BLUE
        x1, y1 = point(move[:2])
        x2, y2 = point(move[2:])
        draw.line((x1, y1, x2, y2), fill=color, width=4)
        draw.ellipse((x1 - 12, y1 - 12, x1 + 12, y1 + 12), outline=color, width=3)
        draw.ellipse((x2 - 35, y2 - 35, x2 + 35, y2 + 35), outline=color, width=4)
    for square, piece in squares(board.fen).items():
        x, y = point(square)
        color = RED if piece.isupper() else INK
        draw.ellipse((x - 30, y - 27, x + 32, y + 34), fill="#bda579")
        draw.ellipse((x - 31, y - 31, x + 31, y + 31), fill="#fff5dc", outline=color, width=2)
        draw.ellipse((x - 26, y - 26, x + 26, y + 26), outline=color, width=1)
        text(x, y - 2, PIECES[piece], 40, color)
    if board.in_check and not finished:
        text(730, 93, "将军", 22, RED)
    latest = " / ".join(
        f"{'红' if i % 2 == 0 else '黑'} {moves[i][:2]} → {moves[i][2:]}"
        for i in range(max(0, len(moves) - 2), len(moves))
    )
    text(420, 1000, latest if latest else "红方在下 · 坐标固定，不随执棋方翻转", 22)
    text(420, 1043, "下棋 炮八平五  /  下棋 b2 e2  /  下棋 帮助", 18)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
