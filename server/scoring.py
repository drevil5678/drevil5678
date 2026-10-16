"""Score the board as it stands, without removing dead stones.

This deliberately implements the organizer's automatic double-pass rule. Empty
regions belong to a color only when every bordering stone has that color. An
empty region with both colors, or no bordering stones, is neutral. Life, death,
and seki are not inferred. Under Japanese rules, territory plus captures is the
current-board result; it is not a full Japanese rules adjudication.
"""

import math
from collections.abc import Mapping
from numbers import Real

from server.go import neighbors


def score_position(board, size, komi, rules="chinese", captures=None):
    """Return JSON-friendly totals, with komi credited to White.

    ``captures`` counts stones captured *by* each player, e.g. ``{"B": 2}``.
    Captures affect Japanese territory scoring only. An exact tie has
    ``winner=None`` and ``margin=0`` so that the caller can apply a draw policy.
    """
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("棋盤大小必須為正整數")
    if not isinstance(board, str) or len(board) != size * size:
        raise ValueError("棋盤內容與棋盤大小不符")
    if any(stone not in ".BW" for stone in board):
        raise ValueError("棋盤只能包含黑子 B、白子 W 與空點 .")
    if isinstance(komi, bool) or not isinstance(komi, Real):
        raise ValueError("貼目必須是有限數值")
    try:
        komi = float(komi)
    except (OverflowError, ValueError):
        raise ValueError("貼目必須是有限數值") from None
    if not math.isfinite(komi):
        raise ValueError("貼目必須是有限數值")
    if rules not in ("chinese", "japanese"):
        raise ValueError("不支援的計分規則")
    if captures is None:
        captures = {}
    if not isinstance(captures, Mapping) or set(captures) - {"B", "W"}:
        raise ValueError("提子數須使用 B、W 表示各方提取的棋子數")
    captured = {color: captures.get(color, 0) for color in ("B", "W")}
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0
           for count in captured.values()):
        raise ValueError("提子數必須為非負整數")

    territory = {"B": 0, "W": 0}
    neutral = 0
    visited = set()
    for start, stone in enumerate(board):
        if stone != "." or start in visited:
            continue
        region = {start}
        visited.add(start)
        boundary = set()
        todo = [start]
        while todo:
            point = todo.pop()
            for adjacent in neighbors(point, size):
                neighbor = board[adjacent]
                if neighbor == ".":
                    if adjacent not in visited:
                        visited.add(adjacent)
                        region.add(adjacent)
                        todo.append(adjacent)
                else:
                    boundary.add(neighbor)
        if len(boundary) == 1:
            territory[next(iter(boundary))] += len(region)
        else:
            neutral += len(region)

    black_stones = board.count("B")
    white_stones = board.count("W")
    black = territory["B"]
    white = territory["W"]
    if rules == "chinese":
        black += black_stones
        white += white_stones
    else:
        black += captured["B"]
        white += captured["W"]
    try:
        white += komi
        margin = abs(black - white)
    except OverflowError:
        raise ValueError("計分數值超出有效範圍") from None
    if not math.isfinite(white) or not math.isfinite(margin):
        raise ValueError("計分數值超出有效範圍")
    winner = "B" if black > white else "W" if white > black else None
    return {
        "black": black,
        "white": white,
        "winner": winner,
        "margin": margin,
        "black_stones": black_stones,
        "white_stones": white_stones,
        "black_territory": territory["B"],
        "white_territory": territory["W"],
        "neutral_points": neutral,
        "komi": komi,
        "captures": captured,
        "rules": rules,
        "method": "area" if rules == "chinese" else "territory",
        "position": "current-board",
        "dead_stones_removed": False,
    }
