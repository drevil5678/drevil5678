import pytest
from server.go import IllegalMove, candidates, decode, encode, play, sgf


def test_coordinates_and_edge_capture():
    n = 9
    assert decode("A1", n) == 72
    assert encode(80, n) == "J1"
    board = list("."*81)
    board[decode("A1", n)] = "W"
    board[decode("A2", n)] = "B"
    board = "".join(board)
    after, count = play(board, "B", "B1", n, [board])
    assert count == 1 and after[72] == "."


def test_suicide_occupied_and_superko():
    n = 9
    board = list("."*81)
    for p in ("A2", "B1"):
        board[decode(p, n)] = "W"
    board = "".join(board)
    with pytest.raises(IllegalMove, match="自殺"):
        play(board, "B", "A1", n, [board])
    with pytest.raises(IllegalMove, match="已有"):
        play(board, "B", "A2", n, [board])
    empty = "."*81
    after, _ = play(empty, "B", "D4", n, [empty])
    with pytest.raises(IllegalMove, match="同形"):
        play(empty, "B", "D4", n, [empty, after, empty])
    with pytest.raises(IllegalMove, match="停一手"):
        play(empty, "B", "pass", n, [empty], allow_pass=False)


def test_ai_uses_winrate_not_order_or_visits():
    board = "."*81
    g = {"board": board, "turn": "W", "history": [board]}
    cfg = {"board_size": 9, "rules": "chinese", "allow_pass": True}
    values = [{"move": "D4", "winrate": .8, "visits": 100, "order": 0},
              {"move": "E4", "winrate": .9, "visits": 2, "order": 1},
              {"move": "D5", "winrate": .85, "visits": 20, "order": 2}]
    result = candidates(g, cfg, values)
    assert [x["move"] for x in result] == ["E4", "D5", "D4"]


def test_sgf_contains_moves_and_escaped_adjudication():
    g = {"moves": [["B", "A1"], ["W", "pass"]], "result": {"reason": "裁定]"}}
    output = sgf(g, {"board_size": 9, "komi": 7.5, "rules": "chinese"})
    assert ";B[ai];W[]" in output
    assert "\\]" in output
