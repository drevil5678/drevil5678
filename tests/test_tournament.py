import pytest
from server.tournament import standings, pair_swiss, playoff_pairs, awards, bracket


def fixture_state(count=32):
    return {"teams": [{"id": str(i), "name": f"隊{i}", "players": ["甲", "乙"],
                       "mars_points": [i, 0], "checked_in": True, "order": i} for i in range(count)],
            "rounds": [], "games": []}


def append_round(state, stage, number, pairs):
    rid = f"{stage}{number}"
    state["rounds"].append({"id": rid, "stage": stage, "number": number})
    for table, (a, b) in enumerate(pairs, 1):
        state["games"].append({"id": f"{rid}-{table}", "round_id": rid, "round": number, "stage": stage, "table": table, "moves": [],
            "seats": {"B": {"team": a}, "W": {"team": b}}, "status": "finished",
            "result": {"winner": a, "points": 1.25 if int(a)%2 else 1, "reason": "referee"}})


def test_five_swiss_rounds_32_teams_no_rematches_and_buchholz():
    state = fixture_state()
    encountered = set()
    for n in range(1, 6):
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
    assert all(len(r["opponents"]) == 5 for r in rows)
    assert rows == sorted(rows, key=lambda r: (-r["score"], -r["buchholz"], r["order"]))


def test_full_bracket_bo3_and_awards():
    state = fixture_state()
    for n in range(1, 6):
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


def test_nearest_rank_pairing_skips_previous_opponents_and_backtracks():
    state = fixture_state(4)
    assert pair_swiss(state) == [("0", "1"), ("2", "3")]
    # The nearest choice 0-1 leaves the forbidden 2-3 pair. Completing every
    # table requires backing up and choosing 0-2, then 1-3 instead.
    append_round(state, "swiss", 1, [("0", "3"), ("1", "2"), ("2", "3")])
    for game in state["games"]:
        game["result"]["points"] = 0
    assert [r["id"] for r in standings(state)] == ["0", "1", "2", "3"]
    assert {frozenset(p) for p in pair_swiss(state)} == {frozenset(("0", "2")), frozenset(("1", "3"))}


def test_public_bracket_has_empty_slots_then_advances_winners_and_ignores_special():
    state = fixture_state(18)
    empty = bracket(state)
    assert not empty["swiss_complete"] and empty["champion"] is None
    assert [len(s["matches"]) for s in empty["stages"]] == [4, 2, 1]
    assert all(m["teams"] == [None, None] and not m["games"]
               for stage in empty["stages"] for m in stage["matches"])
    for number in range(1, 6):
        append_round(state, "swiss", number, pair_swiss(state))
    before = standings(state)
    append_round(state, "special", 1, [("0", "1")])
    state["games"][-1]["status"] = "playing"
    assert standings(state) == before
    stage, number, pairs = playoff_pairs(state)
    seeded = bracket(state)
    assert seeded["swiss_complete"]
    assert [[t["seed"] for t in m["teams"]] for m in seeded["stages"][0]["matches"]] == [[1, 8], [4, 5], [2, 7], [3, 6]]
    append_round(state, stage, number, pairs)
    quarter = bracket(state)
    assert all(m["winner"] is not None for m in quarter["stages"][0]["matches"])
    assert all(all(t is not None for t in m["teams"]) for m in quarter["stages"][1]["matches"])
    for _ in range(4):
        stage, number, pairs = playoff_pairs(state)
        append_round(state, stage, number, pairs)
    finished = bracket(state)
    final = finished["stages"][-1]["matches"][0]
    assert final["status"] == "finished" and len(final["games"]) == 3
    assert finished["champion"] == final["winner"]
    assert sorted(final["wins"].values()) == [1, 2]
    assert all(g["id"] != "special1-1" for s in finished["stages"] for m in s["matches"] for g in m["games"])


def test_legacy_completed_sixth_swiss_round_can_still_enter_playoffs():
    state = fixture_state(18)
    for number in range(1, 7):
        append_round(state, "swiss", number, pair_swiss(state))
    assert playoff_pairs(state)[0] == "quarter"
    assert bracket(state)["swiss_complete"]


def test_team_records_keep_special_results_independent_of_official_progression():
    from server.records import team_records
    state = fixture_state(18)
    for number in range(1, 6):
        append_round(state, "swiss", number, pair_swiss(state))
    eliminated = standings(state)[-1]["id"]
    opponent = next(t["id"] for t in state["teams"] if t["id"] != eliminated)
    append_round(state, "special", 1, [(eliminated, opponent)])
    state["games"][-1]["status"] = "playing"
    record = team_records(state, eliminated)
    assert record["next"]["status"] == "finished"
    assert record["current"][0]["stage"] == "special"
    stage, number, pairs = playoff_pairs(state)
    append_round(state, stage, number, pairs)
    loser = pairs[0][1]
    opponent = next(t["id"] for t in state["teams"] if t["id"] != loser)
    append_round(state, "special", 2, [(loser, opponent)])
    state["games"][-1]["result"]["reason"] = "score"
    record = team_records(state, loser)
    assert record["next"]["status"] == "finished"
    assert record["history"][0]["stage"] == "special" and record["history"][0]["reason"] == "score"
