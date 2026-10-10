import pytest
from server.tournament import standings, pair_swiss, playoff_pairs, awards


def fixture_state(count=32):
    return {"teams": [{"id": str(i), "name": f"隊{i}", "players": ["甲", "乙"],
                       "mars_points": [i, 0], "checked_in": True, "order": i} for i in range(count)],
            "rounds": [], "games": []}


def append_round(state, stage, number, pairs):
    rid = f"{stage}{number}"
    state["rounds"].append({"id": rid, "stage": stage, "number": number})
    for a, b in pairs:
        state["games"].append({"round_id": rid, "round": number, "stage": stage,
            "seats": {"B": {"team": a}, "W": {"team": b}}, "status": "finished",
            "result": {"winner": a, "points": 1.25 if int(a)%2 else 1}})


def test_six_swiss_rounds_32_teams_no_rematches_and_buchholz():
    state = fixture_state()
    encountered = set()
    for n in range(1, 7):
        pairs = pair_swiss(state)
        assert len(pairs) == 16
        assert len({x for pair in pairs for x in pair}) == 32
        for pair in pairs:
            frozen = frozenset(pair)
            assert frozen not in encountered
            encountered.add(frozen)
        append_round(state, "swiss", n, pairs)
    rows = standings(state)
    scores = {r["id"]: r["score"] for r in rows}
    assert all(r["buchholz"] == sum(scores[o] for o in r["opponents"]) for r in rows)
    assert all(len(r["opponents"]) == 6 for r in rows)
    assert rows == sorted(rows, key=lambda r: (-r["score"], -r["buchholz"], r["order"]))


def test_full_bracket_bo3_and_awards():
    state = fixture_state()
    for n in range(1, 7):
        append_round(state, "swiss", n, pair_swiss(state))
    stage, n, pairs = playoff_pairs(state)
    ranking = standings(state)
    assert pairs[0] == (ranking[0]["id"], ranking[7]["id"])
    append_round(state, stage, n, pairs)
    stage, n, pairs = playoff_pairs(state)
    assert stage == "semi" and len(pairs) == 2
    append_round(state, stage, n, pairs)
    stage, n, pairs = playoff_pairs(state)
    assert stage == "final" and n == 1
    append_round(state, stage, n, pairs)
    stage, n, pairs = playoff_pairs(state)
    assert n == 2
    append_round(state, stage, n, pairs)
    assert awards(state) == []  # Alternating black winners make series 1-1.
    stage, n, pairs = playoff_pairs(state)
    assert n == 3
    append_round(state, stage, n, pairs)
    with pytest.raises(ValueError, match="結束"):
        playoff_pairs(state)
    prizes = awards(state)
    assert sum(p["prize_twd"] for p in prizes) == 20000
    assert sorted(p["placement"] for p in prizes).count("四強") == 2
    assert sum(p["placement"] == "八強" for p in prizes) == 4


def test_undefined_bye_is_rejected():
    with pytest.raises(ValueError, match="輪空"):
        pair_swiss(fixture_state(31))
