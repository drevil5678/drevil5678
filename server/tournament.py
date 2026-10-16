"""Five-round Swiss, Buchholz/registration ties and an eight-team bracket."""

SWISS_ROUNDS = 5
OFFICIAL_STAGES = {"swiss", "quarter", "semi", "final"}


def standings(state):
    rows = {t["id"]: {**t, "score": 0, "buchholz": 0, "opponents": [],
                       "black": 0, "white": 0} for t in state["teams"] if t["checked_in"]}
    for g in state["games"]:
        if g["stage"] != "swiss":
            continue
        for color, other in (("B", "W"), ("W", "B")):
            tid, oid = g["seats"][color]["team"], g["seats"][other]["team"]
            if tid in rows:
                rows[tid]["opponents"].append(oid)
                rows[tid]["black" if color == "B" else "white"] += 1
        if g.get("result") and g["result"].get("winner") in rows:
            rows[g["result"]["winner"]]["score"] += g["result"]["points"]
    for row in rows.values():
        row["buchholz"] = sum(rows.get(oid, {}).get("score", 0) for oid in row["opponents"])
    ordered = sorted(rows.values(), key=lambda r: (-r["score"], -r["buchholz"], r["order"]))
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return ordered


def pair_swiss(state):
    rows = standings(state)
    if len(rows) < 2 or len(rows) % 2:
        raise ValueError("需有偶數支已檢錄隊伍；章程未定義輪空積分，請先處理缺席隊伍")
    budget = 20000

    def match(rest):
        nonlocal budget
        budget -= 1
        if budget < 0:
            return None
        if not rest:
            return []
        a = rest[0]
        options = [b for b in rest[1:] if b["id"] not in a["opponents"]]
        # `rest` keeps standings order. Try the nearest lower-ranked team,
        # skipping previous opponents. Backtrack only when needed to complete
        # the remaining tables without a repeat opponent.
        for b in options:
            pairs = match([r for r in rest[1:] if r["id"] != b["id"]])
            if pairs is not None:
                delta_a = a["black"]-a["white"]
                delta_b = b["black"]-b["white"]
                pair = (a["id"], b["id"]) if delta_a <= delta_b else (b["id"], a["id"])
                return [pair] + pairs
        return None
    result = match(rows)
    if result is None:
        raise ValueError("無法在搜尋上限內產生無重複對手配對；請由裁判調整參賽名單或另行確認賽制")
    return result


def playoff_pairs(state):
    games = [g for g in state["games"] if g["stage"] in OFFICIAL_STAGES]
    swiss = [g for g in games if g["stage"] == "swiss"]
    # A legacy event with a sixth played round remains usable after upgrading.
    if not swiss or max(g["round"] for g in swiss) < SWISS_ROUNDS or any(g["status"] != "finished" for g in swiss):
        raise ValueError("需先完成五輪瑞士賽")
    last = next(r for r in reversed(state["rounds"]) if r["stage"] in OFFICIAL_STAGES)
    previous = [g for g in games if g["round_id"] == last["id"]]
    if any(g["status"] != "finished" for g in previous):
        raise ValueError("上一輪尚未完成")
    if last["stage"] == "swiss":
        ids = [t["id"] for t in standings(state)[:8]]
        if len(ids) != 8:
            raise ValueError("需至少八支已檢錄隊伍")
        return "quarter", 1, [(ids[0], ids[7]), (ids[3], ids[4]), (ids[1], ids[6]), (ids[2], ids[5])]
    if last["stage"] in ("quarter", "semi"):
        winners = [g["result"]["winner"] for g in previous]
        return ("semi" if last["stage"] == "quarter" else "final"), 1, list(zip(winners[::2], winners[1::2]))
    finals = [g for g in games if g["stage"] == "final"]
    wins = {}
    for g in finals:
        winner = g["result"]["winner"]
        wins[winner] = wins.get(winner, 0) + 1
    if max(wins.values()) >= 2:
        raise ValueError("冠軍戰已結束")
    g = finals[-1]
    return "final", last["number"]+1, [(g["seats"]["W"]["team"], g["seats"]["B"]["team"])]


def bracket(state):
    """Public bracket, including stable empty slots before seeds are known."""
    names = {t["id"]: {"id": t["id"], "name": t["name"]} for t in state["teams"]}
    swiss = [g for g in state["games"] if g["stage"] == "swiss"]
    complete = bool(swiss) and max(g["round"] for g in swiss) >= SWISS_ROUNDS and all(
        g["status"] == "finished" for g in swiss)
    ranked = standings(state)[:8] if complete else []
    seeds = {t["id"]: t["rank"] for t in ranked}

    def team(tid):
        if tid is None:
            return None
        result = names[tid].copy()
        if tid in seeds:
            result["seed"] = seeds[tid]
        return result

    def slot(stage, number, predicted, games):
        games = sorted(games, key=lambda g: (g["round"], g.get("table", 1)))
        tids = [games[0]["seats"][c]["team"] for c in ("B", "W")] if games else predicted
        wins = {tid: 0 for tid in tids if tid is not None}
        for game in games:
            if game.get("result"):
                tid = game["result"]["winner"]
                wins[tid] = wins.get(tid, 0) + 1
        winner = next((tid for tid, count in wins.items() if count >= (2 if stage == "final" else 1)), None)
        status = "finished" if winner else games[-1]["status"] if games else "pending"
        # A completed final game does not mean the BO3 series is finished.
        if stage == "final" and status == "finished" and winner is None:
            status = "pending"
        public_games = []
        for game in games:
            result = game.get("result")
            public_games.append({"id": game["id"], "round": game["round"], "status": game["status"],
                                 "black": game["seats"]["B"]["team"], "white": game["seats"]["W"]["team"],
                                 "winner": result["winner"] if result else None,
                                 "points": result["points"] if result else None,
                                 "reason": (result["reason"] if result and result["reason"] in
                                            {"threshold", "timeout", "score"} else "referee") if result else None})
        return {"id": f"{stage}-{number}", "stage": stage, "number": number,
                "teams": [team(tid) for tid in tids], "games": public_games,
                "winner": team(winner), "status": status, "wins": wins}

    quarter_games = [g for g in state["games"] if g["stage"] == "quarter"]
    quarters = []
    for index, (a, b) in enumerate(((0, 7), (3, 4), (1, 6), (2, 5)), 1):
        predicted = [ranked[a]["id"], ranked[b]["id"]] if len(ranked) == 8 else [None, None]
        quarters.append(slot("quarter", index, predicted, [g for g in quarter_games if g.get("table") == index]))
    semis = []
    for index in range(1, 3):
        predicted = [q["winner"]["id"] if q["winner"] else None for q in quarters[(index-1)*2:index*2]]
        semis.append(slot("semi", index, predicted,
                          [g for g in state["games"] if g["stage"] == "semi" and g.get("table") == index]))
    predicted = [s["winner"]["id"] if s["winner"] else None for s in semis]
    final = slot("final", 1, predicted, [g for g in state["games"] if g["stage"] == "final"])
    return {"stages": [{"stage": "quarter", "label": "八強", "matches": quarters},
                       {"stage": "semi", "label": "四強", "matches": semis},
                       {"stage": "final", "label": "冠軍戰（三戰兩勝）", "matches": [final]}],
            "champion": final["winner"], "swiss_rounds": SWISS_ROUNDS, "swiss_complete": complete}


def awards(state):
    finals = [g for g in state["games"] if g["stage"] == "final" and g.get("result")]
    wins = {}
    for g in finals:
        winner = g["result"]["winner"]
        wins[winner] = wins.get(winner, 0) + 1
    champion = next((tid for tid, count in wins.items() if count >= 2), None)
    if not champion:
        return []
    finalists = {s["team"] for s in finals[0]["seats"].values()}
    semifinalists = {s["team"] for g in state["games"] if g["stage"] == "semi" for s in g["seats"].values()}
    quarterfinalists = {s["team"] for g in state["games"] if g["stage"] == "quarter" for s in g["seats"].values()}
    output = []
    for t in state["teams"]:
        if not t["checked_in"]:
            continue
        tier = "冠軍" if t["id"] == champion else "亞軍" if t["id"] in finalists else "四強" if t["id"] in semifinalists else "八強" if t["id"] in quarterfinalists else "參賽"
        captain, extra = {"冠軍": (20, 4), "亞軍": (10, 2), "四強": (5, 1), "八強": (2, 0), "參賽": (1, 0)}[tier]
        output.append({"team": t["name"], "placement": tier, "captain_points": captain,
                       "distributable_points": extra, "prize_twd": 20000 if tier == "冠軍" else 0,
                       "appearance_twd": 100 * sum(t["mars_points"])})
    return output
