"""Independent eight-team bracket: quarterfinal BO1, semifinal/final BO3."""


def bracket(state):
    names = {team["id"]: {"id": team["id"], "name": team["name"]} for team in state["teams"]}
    entrants = state.get("knockout", {}).get("entrants", [])

    def slot(stage, number, tids):
        ident = f"{stage}-{number}"
        games = sorted((g for g in state["games"] if g.get("knockout_match") == ident), key=lambda g: g["round"])
        wins = {tid: 0 for tid in tids if tid}
        required = 1 if stage == "quarter" else 2
        for game in games:
            if game.get("result"):
                winner = game["result"]["winner"]
                if winner in wins:
                    wins[winner] += 1  # A threshold victory is ONE game win, even when its points are 1.25.
        winner = next((tid for tid, count in wins.items() if count >= required), None)
        active = next((game for game in games if game["status"] != "finished"), None)
        return {"id": ident, "stage": stage, "number": number,
                "teams": [names.get(tid) for tid in tids], "wins": wins,
                "winner": names.get(winner), "best_of": 1 if required == 1 else 3, "wins_required": required,
                "status": "finished" if winner else active["status"] if active else "pending",
                "games": [{"id": game["id"], "round": game["round"], "status": game["status"],
                           "winner": game["result"]["winner"] if game.get("result") else None,
                           "points": game["result"]["points"] if game.get("result") else None,
                           "reason": game["result"]["reason"] if game.get("result") else None} for game in games]}

    quarters = [slot("quarter", index + 1, entrants[index*2:index*2+2] if entrants else [None, None]) for index in range(4)]
    semis = [slot("semi", index + 1, [match["winner"]["id"] if match["winner"] else None
                                      for match in quarters[index*2:index*2+2]]) for index in range(2)]
    final = slot("final", 1, [match["winner"]["id"] if match["winner"] else None for match in semis])
    return {"stages": [{"stage": "quarter", "label": "八強（一局定勝負）", "matches": quarters},
                       {"stage": "semi", "label": "四強（三戰兩勝）", "matches": semis},
                       {"stage": "final", "label": "冠軍戰（三戰兩勝）", "matches": [final]}],
            "champion": final["winner"], "knockout": True, "initialized": len(entrants) == 8,
            "swiss_rounds": 0, "swiss_complete": False}


def next_game(state, ident):
    tree = bracket(state)
    match = next((match for stage in tree["stages"] for match in stage["matches"] if match["id"] == ident), None)
    if match is None:
        raise ValueError("找不到這組淘汰賽")
    if not all(match["teams"]):
        raise ValueError("請先完成前一階段對戰，等待兩隊晉級")
    if match["winner"]:
        raise ValueError("這組對戰已分出勝負，不能再開下一局")
    if any(game["status"] != "finished" for game in state["games"]):
        raise ValueError("單機版一次只進行一局，請先完成目前棋局")
    if len(match["games"]) >= match["best_of"]:
        raise ValueError("已達本組對戰局數上限，請核對紀錄")
    return match, len(match["games"]) + 1
