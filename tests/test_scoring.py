import json

import pytest

from server.scoring import score_position


def test_enclosed_regions_and_mixed_outside_are_counted_once():
    board = "".join([
        "BBB.WWW",
        "B.B.W.W",
        "BBB.WWW",
        ".......",
        ".......",
        ".......",
        ".......",
    ])
    result = score_position(board, 7, 7.5)
    assert result["black_stones"] == result["white_stones"] == 8
    assert result["black_territory"] == result["white_territory"] == 1
    assert result["neutral_points"] == 31
    assert result["black"] == 9
    assert result["white"] == 16.5
    assert result["winner"] == "W" and result["margin"] == 7.5
    assert result["method"] == "area"
    assert result["dead_stones_removed"] is False
    assert result["position"] == "current-board"
    json.dumps(result, allow_nan=False)


def test_empty_board_is_neutral_and_komi_goes_to_white():
    result = score_position("." * 81, 9, 7.5)
    assert result["neutral_points"] == 81
    assert result["black_territory"] == result["white_territory"] == 0
    assert result["black"] == 0 and result["white"] == 7.5
    assert result["winner"] == "W" and result["margin"] == 7.5


def test_board_edge_closes_a_single_color_region():
    # A board edge needs no extra bordering stone; diagonal contact is ignored.
    result = score_position(".BB" "BWW" "WWW", 3, 0)
    assert result["black_territory"] == 1
    assert result["neutral_points"] == 0
    assert result["black"] == 4 and result["white"] == 5


def test_japanese_current_board_uses_territory_and_captures_not_stones():
    board = "BBB" "B.B" "BBB"
    captures = {"B": 3, "W": 2}
    japanese = score_position(board, 3, 0.5, "japanese", captures)
    assert japanese["black"] == 4
    assert japanese["white"] == 2.5
    assert japanese["winner"] == "B" and japanese["margin"] == 1.5
    assert japanese["method"] == "territory"
    assert japanese["captures"] == captures
    assert captures == {"B": 3, "W": 2}
    chinese = score_position(board, 3, 0.5, "chinese", captures)
    assert chinese["black"] == 9 and chinese["white"] == 0.5


def test_all_stones_are_counted_without_dead_stone_removal():
    result = score_position("BBB" "BWB" "BBB", 3, 7.5)
    assert result["black_stones"] == 8 and result["white_stones"] == 1
    assert result["neutral_points"] == 0
    assert result["black_territory"] == result["white_territory"] == 0
    assert result["black"] == 8 and result["white"] == 8.5
    assert result["winner"] == "W"


def test_exact_tie_has_no_invented_winner():
    result = score_position("BW" "BW", 2, 0)
    assert result["black"] == result["white"] == 2
    assert result["winner"] is None and result["margin"] == 0


@pytest.mark.parametrize("changes", [
    {"size": True}, {"size": 0}, {"size": 2.0},
    {"board": "..."}, {"board": ["."] * 4}, {"board": ".BWx"},
    {"komi": True}, {"komi": "7.5"}, {"komi": float("nan")},
    {"komi": float("inf")}, {"komi": -float("inf")},
    {"rules": "unknown"}, {"captures": []},
    {"captures": {"B": -1}}, {"captures": {"W": 1.5}},
    {"captures": {"B": True}}, {"captures": {"black": 1}},
])
def test_invalid_scoring_inputs_are_rejected(changes):
    arguments = {"board": "....", "size": 2, "komi": 7.5}
    arguments.update(changes)
    with pytest.raises(ValueError):
        score_position(**arguments)
