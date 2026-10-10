import asyncio
import copy
import csv
import io
import json
import math
import os
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import KataGo
from .go import IllegalMove, candidates, decode, encode, play, sgf
from .lan import network_info
from .store import Store
from .tournament import awards, pair_swiss, playoff_pairs, standings

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


class TeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    players: list[str] = Field(min_length=2, max_length=2)
    mars_points: list[float] = Field(default_factory=lambda: [0, 0], min_length=2, max_length=2)
    checked_in: bool = False
    eligibility_checked: bool = False


class SettingsInput(BaseModel):
    board_size: int = Field(default=19, ge=9, le=19)
    komi: float = Field(default=7.5, ge=-50, le=50)
    rules: str = "chinese"
    threshold: float = Field(default=.40, ge=0, le=1)
    visits: int = Field(default=128, ge=16, le=100000)
    allow_pass: bool = True
    rules_confirmed: bool = False


class Action(BaseModel):
    action: str
    reason: str = Field(default="", max_length=500)
    winner: str | None = None
    points: float = 1.0


class MoveInput(BaseModel):
    move: str = Field(max_length=5)
    version: int


class DeviceInput(BaseModel):
    own_device: bool


class CSVInput(BaseModel):
    csv: str = Field(max_length=50000)


def create_app(runtime=None, engine=None, network=None):
    store = Store(runtime or os.getenv("TOURNAMENT_DATA", str(ROOT / ".runtime")))
    state = store.state
    ai = engine or KataGo(store.runtime)
    lock = asyncio.Lock()
    tasks = set()
    app = None

    def event(kind, detail):
        store.record_event({"time": time.time(), "kind": kind, "detail": detail})

    def game_by_id(ident):
        game = next((g for g in state["games"] if g["id"] == ident), None)
        if game is None:
            raise HTTPException(404, "找不到棋局")
        return game

    def require_admin(authorization):
        supplied = (authorization or "").removeprefix("Bearer ")
        if not secrets.compare_digest(supplied, store.admin_token):
            raise HTTPException(401, "請輸入主辦方管理金鑰")

    def seat(game, authorization):
        supplied = (authorization or "").removeprefix("Bearer ")
        for color, info in game["seats"].items():
            if secrets.compare_digest(supplied, info["token"]):
                return color
        raise HTTPException(403, "操作碼不屬於本場比賽")

    def finish(game, winner, reason, points):
        game["status"] = "finished"
        game["clock_started"] = None
        game["generation"] += 1
        game["version"] += 1
        game["result"] = {"winner": winner, "reason": reason, "points": points}
        event("result", {"game": game["id"], **game["result"]})
        # Persist even when a request discovers timeout and then returns 409.
        store.save()

    def settle(game):
        if game["status"] == "playing" and game["clock_started"] is not None:
            now = time.time()
            color = game["turn"]
            game["remaining"][color] = max(0., game["remaining"][color] - max(0, now-game["clock_started"]))
            game["clock_started"] = now
            if game["remaining"][color] <= 0:
                other = "W" if color == "B" else "B"
                finish(game, game["seats"][other]["team"], "timeout", 1)

    def public_game(game):
        result = copy.deepcopy(game)
        result.pop("history", None)
        result.pop("generation", None)
        result.pop("clock_started", None)
        for s in result["seats"].values():
            s.pop("token", None)
            team = next(t for t in state["teams"] if t["id"] == s["team"])
            s["name"] = team["name"]
        result["candidates"] = result["candidates"][:5]
        if game["status"] == "playing" and game["clock_started"] is not None:
            color = game["turn"]
            result["remaining"][color] = max(0, result["remaining"][color] - max(0, time.time()-game["clock_started"]))
        return result

    async def analyze_game(ident, generation):
        async with lock:
            g = game_by_id(ident)
            moves, settings = copy.deepcopy(g["moves"]), state["settings"].copy()
        try:
            started = time.perf_counter()
            data = await ai.analyze(moves, settings)
            duration = time.perf_counter() - started
            async with lock:
                g = game_by_id(ident)
                if g["generation"] != generation or g["status"] != "analyzing":
                    return
                options = candidates(g, settings, data["moveInfos"])
                if len(options) < 2:
                    raise ValueError("AI 未提供至少兩個合法選點，請裁判處理")
                g["candidates"] = options
                g["forbidden"] = options[0]["move"]
                g["best_legal_winrate"] = options[1]["winrate"]
                g["analysis_seconds"] = round(duration, 3)
                color, ply = g["turn"], len(g["moves"])+1
                if g["evaluated_ply"] != ply:
                    if ply >= settings["loss_start_ply"]:
                        g["streak"][color] = g["streak"][color]+1 if options[1]["winrate"] <= settings["threshold"] else 0
                    g["evaluated_ply"] = ply
                    event("analysis", {"game": ident, "ply": ply, "color": color,
                          "forbidden": g["forbidden"], "best_legal_winrate": options[1]["winrate"],
                          "streak": g["streak"][color], "seconds": duration,
                          "candidates": options, "root": data.get("rootInfo", {})})
                if g["streak"][color] >= settings["loss_turns"]:
                    other = "W" if color == "B" else "B"
                    finish(g, g["seats"][other]["team"], "threshold", 1.25)
                else:
                    g["status"] = "playing"
                    g["clock_started"] = time.time()
                g["version"] += 1
                store.save()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with lock:
                g = game_by_id(ident)
                if g["generation"] == generation and g["status"] == "analyzing":
                    g["status"] = "paused"
                    g["pause_reason"] = "AI 分析失敗；裁判須排除原因後恢復"
                    g["clock_started"] = None
                    g["version"] += 1
                    event("ai_error", {"game": ident, "error": str(exc)[:500]})
                    store.save()

    def queue_analysis(game):
        game["status"] = "analyzing"
        game["clock_started"] = None
        game["generation"] += 1
        game["candidates"] = []
        game["forbidden"] = None
        task = asyncio.create_task(analyze_game(game["id"], game["generation"]))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def ticker():
        last_saved = time.time()
        last_backup = time.time()
        backup_seconds = int(os.getenv("AUTO_BACKUP_SECONDS", "0"))
        while True:
            await asyncio.sleep(.25)
            async with lock:
                running = [g for g in state["games"] if g["status"] == "playing"]
                for g in running:
                    settle(g)
                    if ai.failure and g["status"] == "playing":
                        g["status"] = "paused"
                        g["clock_started"] = None
                        g["generation"] += 1
                        g["version"] += 1
                        g["pause_reason"] = "KataGo 程序中斷；需由裁判排除原因後恢復"
                        event("ai_interruption", {"game": g["id"]})
                now = time.time()
                if running and (now-last_saved >= 1 or any(g["status"] == "finished" for g in running)):
                    store.save()
                    last_saved = now
                if backup_seconds > 0 and now-last_backup >= backup_seconds and state["teams"]:
                    store.save()
                    filename = store.backup()
                    event("auto_backup", {"filename": filename})
                    store.save()
                    last_backup = now

    @asynccontextmanager
    async def lifespan(_):
        for g in state["games"]:
            if g["status"] in ("playing", "analyzing"):
                g["status"] = "paused"
                g["clock_started"] = None
                g["generation"] += 1
                g["version"] += 1
                g["pause_reason"] = "伺服器重新啟動；需由裁判恢復"
                event("recovery", {"game": g["id"]})
        store.save()
        await ai.start()
        tick = asyncio.create_task(ticker())
        yield
        tick.cancel()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(tick, *tasks, return_exceptions=True)
        async with lock:
            for g in state["games"]:
                settle(g)
                if g["status"] in ("playing", "analyzing"):
                    g["status"] = "paused"
                    g["clock_started"] = None
                    g["generation"] += 1
                    g["pause_reason"] = "伺服器停止；需由裁判恢復"
            store.save()
        await ai.close()
        store.db.close()

    app = FastAPI(title="楊海分站賽", lifespan=lifespan)
    app.state.store, app.state.ai = store, ai

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        # Same-origin browser mutations; no CORS configuration and no cookie auth.
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return Response("跨來源操作遭拒絕", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health():
        return {"ok": True, "ai_ready": not ai.failure, "ai_error": ai.failure,
                "rules_confirmed": state["settings"]["rules_confirmed"], "games": len(state["games"])}

    @app.get("/api/admin/network")
    async def network_status(request: Request, authorization: str | None = Header(None)):
        require_admin(authorization)
        binding = network or {"host": os.getenv("HOST", "127.0.0.1"),
                              "port": (request.scope.get("server") or (None, 8000))[1]}
        info = network_info(binding["host"], binding["port"])
        return {**info, "ai_ready": not ai.failure,
                "data_dir": str(store.runtime.resolve()),
                "backup_dir": str((store.runtime/"backups").resolve())}

    @app.get("/api/state")
    async def public_state():
        async with lock:
            return {"settings": state["settings"], "rounds": state["rounds"],
                    "standings": [{k: t[k] for k in ("id", "name", "score", "buchholz", "rank", "order")} for t in standings(state)],
                    "games": [public_game(g) for g in state["games"] if state["rounds"] and g["round_id"] == state["rounds"][-1]["id"]], "awards": awards(state)}

    @app.get("/api/admin/state")
    async def admin_state(authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            result = copy.deepcopy(state)
            result["games"] = [{**public_game(g), "seat_tokens": {c: s["token"] for c, s in g["seats"].items()}} for g in state["games"]]
            return result

    @app.post("/api/admin/settings")
    async def settings(body: SettingsInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        if body.board_size not in (9, 13, 19) or body.rules not in ("chinese", "japanese"):
            raise HTTPException(400, "僅支援 9、13、19 路及中國／日本規則")
        if not math.isfinite(body.komi) or not math.isfinite(body.threshold):
            raise HTTPException(400, "設定須為有限數值")
        async with lock:
            if state["games"]:
                raise HTTPException(409, "首輪建立後不可變更賽事規則")
            state["settings"].update(body.model_dump())
            event("settings", body.model_dump())
            store.save()
            return state["settings"]

    def validate_team(body):
        if not body.name.strip() or any(not p.strip() or len(p) > 80 for p in body.players):
            raise HTTPException(400, "隊名及兩位隊員姓名不可空白，姓名最多 80 字")
        if any(not math.isfinite(p) or p < 0 for p in body.mars_points):
            raise HTTPException(400, "火星積分須為非負有限數值")
        if body.checked_in and not body.eligibility_checked:
            raise HTTPException(400, "檢錄前請先確認參賽資格證明")

    @app.post("/api/admin/teams")
    async def add_team(body: TeamInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        validate_team(body)
        async with lock:
            if state["rounds"] or len(state["teams"]) >= 32:
                raise HTTPException(409, "已開賽或報名已滿 32 隊")
            if any(t["name"] == body.name.strip() for t in state["teams"]):
                raise HTTPException(409, "隊名重複")
            team = {**body.model_dump(), "name": body.name.strip(), "id": uuid.uuid4().hex[:12],
                    "order": max((t["order"] for t in state["teams"]), default=0)+1}
            state["teams"].append(team)
            event("team_add", {"id": team["id"], "order": team["order"]})
            store.save()
            return team

    @app.put("/api/admin/teams/{ident}")
    async def edit_team(ident: str, body: TeamInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        validate_team(body)
        async with lock:
            if state["rounds"]:
                raise HTTPException(409, "開賽後名單鎖定")
            t = next((t for t in state["teams"] if t["id"] == ident), None)
            if not t:
                raise HTTPException(404, "隊伍不存在")
            if any(o["id"] != ident and o["name"] == body.name.strip() for o in state["teams"]):
                raise HTTPException(409, "隊名重複")
            if t["players"] != body.players:
                t["order"] = max(x["order"] for x in state["teams"])+1
            t.update(body.model_dump())
            t["name"] = t["name"].strip()
            event("team_edit", {"id": ident, "order": t["order"]})
            store.save()
            return t

    @app.post("/api/admin/teams/import")
    async def import_teams(body: CSVInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        parsed = []
        try:
            reader = csv.DictReader(io.StringIO(body.csv.lstrip("\ufeff")))
            if not {"name", "captain", "member"}.issubset(reader.fieldnames or []):
                raise ValueError("CSV 必須有 name,captain,member 欄位")
            for row in reader:
                team = TeamInput(name=row["name"], players=[row["captain"], row["member"]],
                                 mars_points=[float(row.get("captain_points") or 0), float(row.get("member_points") or 0)])
                validate_team(team)
                parsed.append(team)
        except (ValueError, TypeError, csv.Error) as exc:
            raise HTTPException(400, "CSV 格式錯誤：" + str(exc)[:300]) from exc
        async with lock:
            if state["rounds"] or not parsed or len(state["teams"])+len(parsed) > 32:
                raise HTTPException(409, "已開賽、CSV 無隊伍或匯入後超過 32 隊")
            names = [t.name.strip() for t in parsed] + [t["name"] for t in state["teams"]]
            if len(set(names)) != len(names):
                raise HTTPException(409, "CSV 或現有名單中有重複隊名；未匯入任何隊伍")
            order = max((t["order"] for t in state["teams"]), default=0)
            for i, t in enumerate(parsed, 1):
                state["teams"].append({**t.model_dump(), "name": t.name.strip(),
                                      "id": uuid.uuid4().hex[:12], "order": order+i})
            event("team_import", {"count": len(parsed)})
            store.save()
            return {"imported": len(parsed)}

    def new_round(stage, number, pairs):
        rid = uuid.uuid4().hex[:12]
        round_info = {"id": rid, "stage": stage, "number": number}
        state["rounds"].append(round_info)
        size = state["settings"]["board_size"]
        for table, (black, white) in enumerate(pairs, 1):
            board = "." * (size * size)
            state["games"].append({"id": uuid.uuid4().hex[:12], "round_id": rid,
                "stage": stage, "round": number, "table": table, "status": "ready",
                "seats": {"B": {"team": black, "token": secrets.token_urlsafe(18), "own_device": False},
                          "W": {"team": white, "token": secrets.token_urlsafe(18), "own_device": False}},
                "board": board, "history": [board], "moves": [], "turn": "B", "version": 0,
                "generation": 0, "clock_started": None, "remaining": {"B": 600., "W": 600.},
                "streak": {"B": 0, "W": 0}, "evaluated_ply": 0, "candidates": [],
                "forbidden": None, "best_legal_winrate": None, "pause_reason": "", "result": None})
        event("round", round_info)
        store.save()
        return round_info

    @app.post("/api/admin/rounds/{kind}")
    async def next_round(kind: str, authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            if not state["settings"]["rules_confirmed"]:
                raise HTTPException(409, "請先在設定頁確認正式規則")
            if ai.failure:
                raise HTTPException(503, "KataGo 尚未就緒")
            if any(g["status"] != "finished" for g in state["games"]):
                raise HTTPException(409, "上一輪仍有未完成棋局")
            try:
                if kind == "swiss":
                    number = 1 + sum(r["stage"] == "swiss" for r in state["rounds"])
                    if number > 6 or any(r["stage"] != "swiss" for r in state["rounds"]):
                        raise ValueError("瑞士制共六輪")
                    return new_round("swiss", number, pair_swiss(state))
                if kind == "playoff":
                    stage, number, pairs = playoff_pairs(state)
                    return new_round(stage, number, pairs)
                raise HTTPException(404, "未知賽制")
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get("/api/games/{ident}")
    async def get_game(ident: str):
        async with lock:
            return public_game(game_by_id(ident))

    @app.get("/api/games/{ident}/seat")
    async def get_seat(ident: str, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            color = seat(g, authorization)
            return {"color": color, "team": g["seats"][color]["team"]}

    @app.post("/api/games/{ident}/device")
    async def choose_device(ident: str, body: DeviceInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            color = seat(g, authorization)
            if g["status"] != "ready":
                raise HTTPException(409, "須在每場對弈開始前決定是否使用自備設備")
            g["seats"][color]["own_device"] = body.own_device
            g["remaining"][color] = 300. if body.own_device else 600.
            event("device", {"game": ident, "color": color, "own_device": body.own_device})
            store.save()
            return public_game(g)

    @app.post("/api/games/{ident}/move")
    async def move(ident: str, body: MoveInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            color = seat(g, authorization)
            settle(g)
            if g["status"] != "playing":
                store.save()
                raise HTTPException(409, "棋局尚未開始、暫停、分析中或已結束")
            if color != g["turn"]:
                raise HTTPException(403, "尚未輪到你行棋")
            if body.version != g["version"]:
                raise HTTPException(409, "棋局已更新，請重新確認落點")
            cfg = state["settings"]
            try:
                pos = decode(body.move, cfg["board_size"])
                chosen = "pass" if pos is None else encode(pos, cfg["board_size"])
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            if chosen == g["forbidden"]:
                raise HTTPException(400, "禁止選擇 AI 勝率最高的落點")
            try:
                board, captured = play(g["board"], color, chosen, cfg["board_size"], g["history"], cfg["rules"], cfg["allow_pass"])
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            g["board"] = board
            g["history"].append(board)
            g["moves"].append([color, chosen])
            g["turn"] = "W" if color == "B" else "B"
            g["version"] += 1
            event("move", {"game": ident, "ply": len(g["moves"]), "color": color,
                           "move": chosen, "captured": captured, "remaining": g["remaining"].copy()})
            if len(g["moves"]) >= 2 and all(m[1] == "pass" for m in g["moves"][-2:]):
                g["status"] = "paused"
                g["clock_started"] = None
                g["generation"] += 1
                g["candidates"] = []
                g["pause_reason"] = "雙方停一手；請裁判依公告規則裁定"
            else:
                queue_analysis(g)
            store.save()
            return public_game(g)

    @app.post("/api/admin/games/{ident}/action")
    async def referee(ident: str, body: Action, authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            g = game_by_id(ident)
            settle(g)
            if g["status"] == "finished":
                raise HTTPException(409, "已完成棋局不可覆寫；請保留紀錄另行處理申訴")
            if body.action == "start":
                if g["status"] != "ready":
                    raise HTTPException(409, "棋局已開始")
                if ai.failure:
                    raise HTTPException(503, "KataGo 未就緒")
                queue_analysis(g)
            elif body.action == "pause":
                if not body.reason.strip() or g["status"] not in ("playing", "analyzing"):
                    raise HTTPException(400, "須提供暫停原因且棋局正在進行")
                g["status"] = "paused"
                g["clock_started"] = None
                g["generation"] += 1
                g["pause_reason"] = body.reason
            elif body.action == "resume":
                if g["status"] != "paused" or ai.failure:
                    raise HTTPException(409, "僅能在 AI 就緒後恢復暫停棋局")
                if g["candidates"]:
                    g["status"] = "playing"
                    g["clock_started"] = time.time()
                else:
                    queue_analysis(g)
            elif body.action == "adjudicate":
                if not body.reason.strip() or body.winner not in [s["team"] for s in g["seats"].values()] or body.points not in (1., 1.25):
                    raise HTTPException(400, "須指定本場勝隊、1 或 1.25 積分及裁定理由")
                finish(g, body.winner, "referee: " + body.reason, body.points)
            elif body.action == "rematch":
                if not body.reason.strip():
                    raise HTTPException(400, "重賽須填寫原因")
                # Archive the interrupted board in the audit log before resetting.
                event("rematch_archive", {"game": ident, "reason": body.reason,
                      "sgf": sgf(g, state["settings"]), "remaining": g["remaining"].copy()})
                empty = "." * len(g["board"])
                g.update({"board": empty, "history": [empty], "moves": [], "turn": "B", "status": "ready",
                          "streak": {"B": 0, "W": 0}, "candidates": [], "forbidden": None,
                          "evaluated_ply": 0, "clock_started": None, "best_legal_winrate": None,
                          "remaining": {c: 300. if s["own_device"] else 600. for c, s in g["seats"].items()}})
                g["generation"] += 1
            else:
                raise HTTPException(400, "未知裁判操作")
            g["version"] += 1
            event("referee", {"game": ident, **body.model_dump()})
            store.save()
            return public_game(g)

    @app.get("/api/games/{ident}/sgf")
    async def export_sgf(ident: str):
        async with lock:
            return Response(sgf(game_by_id(ident), state["settings"]), media_type="application/x-go-sgf",
                            headers={"Content-Disposition": f'attachment; filename="{ident}.sgf"'})

    @app.get("/api/admin/export")
    async def export(authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            result = copy.deepcopy(state)
            result["events"] = store.all_events()
            for g in result["games"]:
                for s in g["seats"].values():
                    s.pop("token", None)
            result["standings"], result["awards"] = standings(state), awards(state)
            return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="tournament-export.json"'})

    @app.post("/api/admin/backup")
    async def backup(authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            store.save()
            filename = store.backup()
            event("backup", {"filename": filename})
            store.save()
            return {"filename": filename}

    @app.post("/api/admin/pause-all")
    async def pause_all(body: Action, authorization: str | None = Header(None)):
        require_admin(authorization)
        if not body.reason.strip():
            raise HTTPException(400, "請填写全場暫停原因")
        async with lock:
            count = 0
            for g in state["games"]:
                settle(g)
                if g["status"] in ("playing", "analyzing"):
                    g["status"] = "paused"
                    g["clock_started"] = None
                    g["generation"] += 1
                    g["version"] += 1
                    g["pause_reason"] = body.reason
                    count += 1
            event("pause_all", {"count": count, "reason": body.reason})
            store.save()
            return {"paused": count}

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
