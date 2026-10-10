"""Legal Go moves. Coordinates follow GTP, skipping I; row 1 is the bottom."""
LETTERS = "ABCDEFGHJKLMNOPQRST"


class IllegalMove(ValueError):
    pass


def decode(move, size):
    if move.lower() == "pass":
        return None
    try:
        x, y = LETTERS.index(move[0].upper()), size - int(move[1:])
    except (ValueError, IndexError):
        raise IllegalMove("座標格式錯誤") from None
    if not (0 <= x < size and 0 <= y < size):
        raise IllegalMove("落點超出棋盤")
    return y * size + x


def encode(index, size):
    return LETTERS[index % size] + str(size - index // size)


def neighbors(index, size):
    x, y = index % size, index // size
    for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
        if 0 <= nx < size and 0 <= ny < size:
            yield ny * size + nx


def group(board, start, size):
    stones, liberties, todo = set(), set(), [start]
    while todo:
        p = todo.pop()
        if p in stones:
            continue
        stones.add(p)
        for q in neighbors(p, size):
            if board[q] == ".":
                liberties.add(q)
            elif board[q] == board[start] and q not in stones:
                todo.append(q)
    return stones, liberties


def play(board, color, move, size, history, rules="chinese", allow_pass=True):
    pos = decode(move, size)
    if pos is None:
        if not allow_pass:
            raise IllegalMove("本場不允許停一手")
        return board, 0
    if board[pos] != ".":
        raise IllegalMove("此處已有棋子")
    result = list(board)
    result[pos] = color
    opponent = "W" if color == "B" else "B"
    captured = 0
    for q in neighbors(pos, size):
        if result[q] == opponent:
            stones, liberties = group(result, q, size)
            if not liberties:
                captured += len(stones)
                for stone in stones:
                    result[stone] = "."
    if not group(result, pos, size)[1]:
        raise IllegalMove("禁止自殺手")
    result = "".join(result)
    if rules == "japanese":
        if len(history) >= 2 and result == history[-2]:
            raise IllegalMove("禁止立即回劫")
    elif result in history:
        raise IllegalMove("禁止全局同形再現（位置超劫）")
    return result, captured


def candidates(game, settings, infos):
    valid = {}
    for item in infos:
        move = item["move"].upper()
        if move == "PASS":
            move = "pass"
        try:
            play(game["board"], game["turn"], move, settings["board_size"],
                 game["history"], settings["rules"], settings["allow_pass"])
        except IllegalMove:
            continue
        rate = float(item["winrate"])
        if not 0 <= rate <= 1:
            raise ValueError("AI 勝率超出有效範圍")
        valid[move] = {"move": move, "winrate": rate, "visits": item["visits"]}
    # The charter says highest WINRATE, not KataGo's utility/visit-based order.
    return sorted(valid.values(), key=lambda x: (-x["winrate"], -x["visits"], x["move"]))


def sgf(game, settings):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("]", "\\]")
    text = f"(;GM[1]FF[4]CA[UTF-8]SZ[{settings['board_size']}]KM[{settings['komi']}]RU[{settings['rules']}]"
    for color, move in game["moves"]:
        p = decode(move, settings["board_size"])
        coord = "" if p is None else chr(97+p % settings["board_size"]) + chr(97+p // settings["board_size"])
        text += f";{color}[{coord}]"
    if game.get("result"):
        text += f"C[{escape(game['result'])}]"
    return text + ")"
