"""Public team history. Future Swiss opponents are never guessed."""
from .tournament import standings


def team_records(state, ident):
    team = next((t for t in state["teams"] if t["id"] == ident), None)
    if team is None:
        raise KeyError(ident)
    own = [g for g in state["games"] if any(s["team"] == ident for s in g["seats"].values())]
    names = {t["id"]: t["name"] for t in state["teams"]}
    rows = standings(state)
    rank = next((r for r in rows if r["id"] == ident), None)

    def summary(g):
        color = next(c for c, s in g["seats"].items() if s["team"] == ident)
        other = "W" if color == "B" else "B"
        result = g.get("result")
        return {"id": g["id"], "stage": g["stage"], "round": g["round"], "table": g["table"],
                "color": color, "opponent": names[g["seats"][other]["team"]],
                "opponent_id": g["seats"][other]["team"], "status": g["status"],
                "moves": len(g["moves"]), "outcome": ("win" if result["winner"] == ident else "loss") if result else None,
                "points": result["points"] if result and result["winner"] == ident else 0,
                "reason": ("threshold" if result["reason"] == "threshold" else "timeout" if result["reason"] == "timeout" else "referee") if result else None}

    scheduled = [summary(g) for g in own if g["status"] == "ready"]
    active = [summary(g) for g in own if g["status"] not in ("ready", "finished")]
    history = [summary(g) for g in reversed(own) if g["status"] == "finished"]
    following = {"status": "waiting_pairing", "message": "尚未建立賽程；請等待主辦方配對。"}
    if scheduled:
        following = {"status": "scheduled", "message": "下一場已排定，請查看下方桌次與對手。"}
    elif state["rounds"]:
        last = state["rounds"][-1]
        if last["stage"] == "swiss":
            if last["number"] < 6:
                following = {"status": "waiting_pairing", "stage": "swiss", "round": last["number"]+1,
                             "message": f"瑞士制下一輪對手須待本輪全部成績確定後，由主辦方配對；目前尚未排定。下一輪為第 {last['number']+1} 輪。"}
            else:
                all_done = all(g["status"] == "finished" for g in state["games"])
                if all_done and (rank is None or rank["rank"] > 8):
                    following = {"status": "finished", "message": "六輪瑞士賽已完成，本隊未晉級八強。"}
                else:
                    following = {"status": "waiting_seed", "message": "六輪成績確定後，依前八名建立淘汰賽；尚未排定。"}
        elif own:
            latest = own[-1]
            if latest["stage"] == "swiss" or (latest["stage"] in ("quarter", "semi") and latest.get("result") and latest["result"]["winner"] != ident):
                following = {"status": "finished", "message": "本隊賽程已結束。"}
            elif last["stage"] == "final":
                wins = {}
                for game in state["games"]:
                    if game["stage"] == "final" and game.get("result"):
                        winner = game["result"]["winner"]
                        wins[winner] = wins.get(winner, 0)+1
                following = {"status": "finished", "message": "冠軍戰已結束。"} if any(n >= 2 for n in wins.values()) else {
                    "status": "waiting_pairing", "message": "冠軍戰三戰兩勝；是否有下一局依系列賽結果決定，桌次待主辦方建立。"}
            else:
                following = {"status": "waiting_pairing", "message": "下一階段待本輪成績確定後，由主辦方建立。"}
    return {"team": {"id": ident, "name": team["name"]},
            "standing": {k: rank[k] for k in ("rank", "score", "buchholz")} if rank else None,
            "current": active, "history": history, "upcoming": scheduled, "next": following}
