from typing import Dict

import pytest

from xiangqi.rules import Board, Position, START_FEN, native_move, outcome, public_move, replay, squares


def fen(cells: Dict[str, str], turn: str = "w") -> str:
    rows = []
    for y in reversed(range(10)):
        row, empty = "", 0
        for x in range(9):
            piece = cells.get(f"{chr(97 + x)}{y}")
            if piece:
                row += (str(empty) if empty else "") + piece
                empty = 0
            else:
                empty += 1
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows) + f" {turn} - - 0 1"


def test_initial_board_and_native_coordinates():
    board = Board()
    assert len(board.legal_moves()) == 44
    assert len(squares(board.fen)) == 32
    assert native_move("b2b9") == "b3b10"
    assert public_move("b3b10") == "b2b9"
    assert board.push("b2b9") is True
    assert squares(board.fen)["b9"] == "C"


@pytest.mark.parametrize(
    "text", ["炮八平五", "炮8平5", "砲八平五", "炮 八 平 五", "b2e2", "b2 e2", "B2 → E2", "b2到e2"]
)
def test_central_cannon_inputs(text):
    assert [c.move for c in Board().parse(text)] == ["b2e2"]


def test_black_notation_uses_black_perspective():
    board = Board()
    board.push("b2e2")
    assert board.parse("马8进7")[0].move == "h9g7"
    assert board.parse("炮二平五")[0].move == "b7e7"


def test_illegal_move_cannot_change_board():
    board = Board()
    for move in ("a0a9", "b0b2", "b2b7", "e0f1", "a3b3", "bad"):
        with pytest.raises(ValueError):
            board.push(move)
        assert board.fen == START_FEN
    assert board.parse("炮八平八") == []
    assert board.parse("帮我走一步好的") is None


def test_horse_leg_elephant_eye_and_flying_generals():
    cells = {"e0": "K", "e9": "k", "e5": "P", "b0": "N", "b1": "P", "c0": "B"}
    board = Board(fen(cells))
    moves = board.legal_moves()
    assert "b0a2" not in moves and "b0c2" not in moves
    assert "c0a2" not in moves and "c0e2" in moves
    assert "e5f5" not in moves  # 唯一遮挡将帅的兵不能移开。


def test_front_and_rear_rooks_and_ambiguous_file():
    board = Board(fen({"e0": "K", "e9": "k", "e5": "P", "a0": "R", "a2": "R"}))
    assert board.parse("前车平八")[0].move == "a2b2"
    assert board.parse("後車平八")[0].move == "a0b0"
    assert {c.move for c in board.parse("车九平八")} == {"a0b0", "a2b2"}


@pytest.mark.parametrize("extra,check,label", [({}, False, "困毙"), ({"e1": "r"}, True, "将死")])
def test_stalemate_and_checkmate_are_losses(extra, check, label):
    cells = {"e0": "K", "e9": "k", "e5": "p", "d1": "r", "f1": "r", **extra}
    board = Board(fen(cells))
    assert board.in_check is check
    assert board.legal_moves() == []
    assert outcome(Position(board, [board.key] * 3, 120), 3, 120) == f"黑方获胜（{label}）"


def test_repetition_and_no_capture_house_rules():
    cycle = ["b0c2", "b9c7", "c2b0", "c7b9"]
    assert outcome(replay(cycle), 3, 120) == ""
    assert "相同局面" in outcome(replay(cycle * 2), 3, 120)
    # 不使用引擎 FEN 的半回合计数：兵移动也应计入插件的无吃子计数。
    position = replay(["a3a4", "a6a5"])
    assert position.no_capture == 2
    assert "没有吃子" in outcome(position, 3, 2)
    assert replay(["b2b9"]).no_capture == 0


def test_every_initial_notation_round_trips():
    for choice in Board().choices():
        assert choice.move in {c.move for c in Board().parse(choice.notation)}
