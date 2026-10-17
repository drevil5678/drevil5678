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
from .credentials import imported_team_code, matches, matches_team_code, new_team_code
from .go import IllegalMove, candidates, decode, encode, play, sgf
from .lan import network_info
from .store import Store
from .tournament import OFFICIAL_STAGES, SWISS_ROUNDS, awards, bracket, pair_swiss, playoff_pairs, standings
from .records import team_records
from .scoring import score_position
from .local_practice import install_local_practice

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


class TeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    players: list[str] = Field(min_length=2, max_length=2)
    mars_points: list[float] = Field(default_factory=lambda: [0, 0], min_length=2, max_length=2)
    checked_in: bool = True
    eligibility_checked: bool = True


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


class CodeReset(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class DiceInput(BaseModel):
    attempt: int = Field(ge=1)


class ColorInput(DiceInput):
    color: str


class ReadyInput(DiceInput):
    ready: bool = True


class SpecialInput(BaseModel):
    team_a: str = Field(max_length=60)
    team_b: str = Field(max_length=60)


class PracticeControl(BaseModel):
    enabled: bool


class DuelInput(SettingsInput):
    team_a: str = Field(default="甲隊", min_length=1, max_length=60)
    team_b: str = Field(default="乙隊", min_length=1, max_length=60)
    championship: bool = False
    device_a: bool = False
    device_b: bool = False


def create_app(runtime=None, engine=None, network=None, bootstrap=False, duel=False):
    store = Store(runtime or os.getenv("TOURNAMENT_DATA", str(ROOT / ".runtime")))
    initial_roster_loaded = store.prepare_initial_roster() if bootstrap and not duel else False
    state = store.state
    ai = engine or KataGo(store.runtime)
    lock = asyncio.Lock()
    tasks = set()
    app = None

    def time_limit(game, own_device):
        return (600. if own_device else 900.) if game.get("stage") == "final" or game.get("championship_clock") else (300. if own_device else 600.)

    def init_pregame(game):
        if "pregame" not in game:
            ids = [s["team"] for s in game["seats"].values()]
            game["pregame"] = {"armed": False, "attempt": 1, "rolls": {}, "history": [],
                               "winner": None, "color_choice": None, "ready": {tid: False for tid in ids}}
        if "id" not in game["pregame"]:
            game["pregame"]["id"] = uuid.uuid4().hex[:12]
        return game["pregame"]

    # Add new fields without replaying or discarding an existing game.
    for game in state["games"]:
        if "captures" not in game:
            captured = {"B": 0, "W": 0}
            for index, (color, _) in enumerate(game.get("moves", [])):
                history = game.get("history", [])
                if len(history) > index + 1:
                    other = "W" if color == "B" else "B"
                    captured[color] += max(0, history[index].count(other) - history[index + 1].count(other))
            game["captures"] = captured
        if game.get("time_control_version") != "v0.5.0":
            if game.get("stage") == "final" and game["status"] != "finished":
                for color in ("B", "W"):
                    game["remaining"][color] += 300.
            game["time_control_version"] = "v0.5.0"
        if game["status"] == "ready":
            init_pregame(game)

    def event(kind, detail):
        store.record_event({"time": time.time(), "kind": kind, "detail": detail})

    def game_by_id(ident):
        game = next((g for g in state["games"] if g["id"] == ident), None)
        if game is None:
            raise HTTPException(404, "找不到棋局")
        return game

    def require_admin(authorization):
        supplied = (authorization or "").removeprefix("Bearer ")
        if not matches(store.admin_token, supplied):
            raise HTTPException(401, "請輸入主辦方管理金鑰")

    def seat(game, authorization):
        supplied = (authorization or "").removeprefix("Bearer ")
        for color, info in game["seats"].items():
            team = next(t for t in state["teams"] if t["id"] == info["team"])
            if matches(info["token"], supplied) or matches_team_code(team["play_code"], supplied):
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
        result["move_number"] = len(game["moves"]) + 1
        result["moves_played"] = len(game["moves"])
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
                if not options or (len(options) < 2 and options[0]["move"] != "pass"):
                    raise ValueError("AI 未提供至少兩個合法選點，請裁判處理")
                g["candidates"] = options
                g["forbidden"] = options[0]["move"] if options[0]["move"] != "pass" else None
                best_legal = options[0] if options[0]["move"] == "pass" else options[1]
                g["best_legal_winrate"] = best_legal["winrate"]
                g["analysis_seconds"] = round(duration, 3)
                color, ply = g["turn"], len(g["moves"])+1
                if g["evaluated_ply"] != ply:
                    if ply >= settings["loss_start_ply"]:
                        g["streak"][color] = g["streak"][color]+1 if best_legal["winrate"] <= settings["threshold"] else 0
                    g["evaluated_ply"] = ply
                    event("analysis", {"game": ident, "ply": ply, "color": color,
                          "forbidden": g["forbidden"], "best_legal_winrate": best_legal["winrate"],
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

    def start_if_ready(game):
        p = init_pregame(game)
        if game["status"] == "ready" and p["armed"] and p["color_choice"] and all(p["ready"].values()):
            if ai.failure:
                raise HTTPException(503, "KataGo 尚未就緒")
            for color, info in game["seats"].items():
                game["remaining"][color] = time_limit(game, info["own_device"])
            queue_analysis(game)
            return True
        return False

    def require_pregame(game, body, authorization):
        color = seat(game, authorization)
        team_id = game["seats"][color]["team"]
        p = init_pregame(game)
        if game["status"] != "ready" or not p["armed"]:
            raise HTTPException(409, "請等待主辦開啟本輪猜先，或本局已開始")
        if body.attempt != p["attempt"]:
            raise HTTPException(409, "猜先已進入下一次，請查看最新骰子")
        return p, team_id

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
        await practice.start()
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
        await practice.close()
        await ai.close()
        store.db.close()

    app = FastAPI(title="楊海分站賽", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.ai = store, ai
    app.state.initial_roster_loaded = initial_roster_loaded
    practice = install_local_practice(app, ai, ROOT, teams=lambda: state["teams"],
                                      enabled=lambda: state["practice_enabled"])

    @app.post("/api/admin/practice")
    async def practice_control(body: PracticeControl, authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            async with practice.registry:
                state["practice_enabled"] = body.enabled
                practice.set_open(body.enabled)
                event("practice_control", {"enabled": body.enabled})
                store.save()
                return {"enabled": body.enabled}

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if duel:
            local_client = request.client and request.client.host in ("127.0.0.1", "::1", "testclient")
            local_host = request.url.hostname in ("127.0.0.1", "localhost", "::1") or (
                request.client and request.client.host == "testclient" and request.url.hostname == "testserver")
            if not local_client or not local_host:
                return Response("單機 PK 只允許本機連線", status_code=403)
        # Same-origin browser mutations; no CORS configuration and no cookie auth.
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return Response("跨來源操作遭拒絕", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        ancestors = "'self'" if request.url.path == "/local-practice" else "'none'"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors {ancestors}"
        if duel or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health():
        return {"ok": True, "ai_ready": not ai.failure, "ai_error": ai.failure,
                "rules_confirmed": state["settings"]["rules_confirmed"], "games": len(state["games"]),
                "mode": "duel" if duel else "tournament"}

    @app.get("/api/admin/network")
    async def network_status(request: Request, authorization: str | None = Header(None)):
        require_admin(authorization)
        binding = network or {"host": os.getenv("HOST", "127.0.0.1"),
                              "port": (request.scope.get("server") or (None, 8000))[1]}
        info = network_info(binding["host"], binding["port"])
        return {**info, "ai_ready": not ai.failure,
                "ai_engine": ai.status() if callable(getattr(ai, "status", None)) else {"label": "AI", "ready": not ai.failure},
                "data_dir": str(store.runtime.resolve()),
                "backup_dir": str((store.runtime/"backups").resolve())}

    @app.get("/api/state")
    async def public_state():
        async with lock:
            current = next((r for r in reversed(state["rounds"]) if r["stage"] in OFFICIAL_STAGES), None)
            result = {"settings": state["settings"], "practice_enabled": state["practice_enabled"], "rounds": state["rounds"],
                    "teams": [{"id": t["id"], "name": t["name"], "order": t["order"]} for t in state["teams"]],
                    "standings": [{k: t[k] for k in ("id", "name", "score", "buchholz", "rank", "order")} for t in standings(state)],
                    "games": [public_game(g) for g in state["games"] if duel or g["stage"] == "special" or (current and g["round_id"] == current["id"])],
                    "bracket": bracket(state), "awards": awards(state)}
            if duel:
                result["duel"] = {"credentials": {g["id"]: {s["team"]: s["token"] for s in g["seats"].values()}
                                                   for g in state["games"]}}
            return result

    @app.get("/api/bracket")
    async def get_bracket():
        async with lock:
            return bracket(state)

    @app.get("/api/admin/state")
    async def admin_state(authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            result = copy.deepcopy(state)
            codes = {t["id"]: t["play_code"] for t in state["teams"]}
            result["games"] = [{**public_game(g), "seat_tokens": {c: codes[s["team"]] for c, s in g["seats"].items()}} for g in state["games"]]
            return result

    @app.get("/api/teams/{ident}/history")
    async def history(ident: str):
        async with lock:
            try:
                return team_records(state, ident)
            except KeyError:
                raise HTTPException(404, "找不到隊伍") from None

    @app.get("/api/team/identify")
    async def identify(authorization: str | None = Header(None)):
        supplied = (authorization or "").removeprefix("Bearer ")
        async with lock:
            team = next((t for t in state["teams"] if matches_team_code(t["play_code"], supplied)), None)
            if team is None:
                raise HTTPException(403, "隊伍點棋碼不正確")
            return {"id": team["id"], "name": team["name"]}

    @app.post("/api/admin/teams/{ident}/code")
    async def reset_code(ident: str, body: CodeReset, authorization: str | None = Header(None)):
        require_admin(authorization)
        if not body.reason.strip():
            raise HTTPException(400, "請填寫重新制發原因")
        async with lock:
            team = next((t for t in state["teams"] if t["id"] == ident), None)
            if team is None:
                raise HTTPException(404, "找不到隊伍")
            team["play_code"] = new_team_code(t["play_code"] for t in state["teams"])
            for game in state["games"]:
                for info in game["seats"].values():
                    if info["team"] == ident:
                        info["token"] = secrets.token_urlsafe(18)
            event("team_code_reset", {"team": ident, "reason": body.reason})
            store.save()
            return {"id": ident, "play_code": team["play_code"]}

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
                    "checked_in": True, "eligibility_checked": True,
                    "play_code": new_team_code(t["play_code"] for t in state["teams"]),
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
            updates = body.model_dump()
            updates.update(checked_in=True, eligibility_checked=True)
            # The form no longer collects prior points. Keep historical values
            # when an old registration is edited without that optional field.
            if "mars_points" not in body.model_fields_set:
                updates.pop("mars_points")
            t.update(updates)
            t["name"] = t["name"].strip()
            event("team_edit", {"id": ident, "order": t["order"]})
            store.save()
            return t

    @app.post("/api/admin/teams/import")
    async def import_teams(body: CSVInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        parsed = []
        try:
            content = body.csv.lstrip("\ufeff").strip()
            delimiter = "\t" if "\t" in content.partition("\n")[0] else ","
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            aliases = {"隊名": "name", "隊長姓名": "captain", "隊員姓名": "member",
                       "隊長火星積分": "captain_points", "隊員火星積分": "member_points",
                       "點棋碼": "play_code", "認證碼": "play_code"}
            fields = [aliases.get(f.strip(), f.strip()) for f in (reader.fieldnames or [])]
            if not {"name", "captain", "member"}.issubset(fields) or len(set(fields)) != len(fields):
                raise ValueError("須包含隊名、隊長姓名、隊員姓名欄位，且欄位不可重複")
            reader.fieldnames = fields
            for row in reader:
                if None in row:
                    raise ValueError("部分資料的欄位數超過標題")
                team = TeamInput(name=row["name"], players=[row["captain"], row["member"]],
                                 mars_points=[float(row.get("captain_points") or 0), float(row.get("member_points") or 0)])
                validate_team(team)
                code = row.get("play_code") or ""
                parsed.append((team, imported_team_code(code) if code.strip() else None))
        except (ValueError, TypeError, csv.Error) as exc:
            raise HTTPException(400, "名單格式錯誤：請檢查欄位、姓名、點數及 12 碼點棋碼") from exc
        async with lock:
            if state["rounds"] or not parsed or len(state["teams"])+len(parsed) > 32:
                raise HTTPException(409, "已開賽、CSV 無隊伍或匯入後超過 32 隊")
            names = [t.name.strip() for t, _ in parsed] + [t["name"] for t in state["teams"]]
            if len(set(names)) != len(names):
                raise HTTPException(409, "CSV 或現有名單中有重複隊名；未匯入任何隊伍")
            supplied = [code for _, code in parsed if code]
            used = [t["play_code"] for t in state["teams"]]
            if len(set(supplied + used)) != len(supplied + used):
                raise HTTPException(409, "點棋碼重複；未匯入任何隊伍")
            # Reserve every preissued code before generating codes for blank cells.
            used += supplied
            order = max((t["order"] for t in state["teams"]), default=0)
            for i, (t, code) in enumerate(parsed, 1):
                if code is None:
                    code = new_team_code(used)
                    used.append(code)
                state["teams"].append({**t.model_dump(), "name": t.name.strip(),
                                      "checked_in": True, "eligibility_checked": True,
                                      "id": uuid.uuid4().hex[:12], "order": order+i,
                                      "play_code": code})
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
            game = {"id": uuid.uuid4().hex[:12], "round_id": rid,
                "stage": stage, "round": number, "table": table, "status": "ready",
                "seats": {"B": {"team": black, "token": secrets.token_urlsafe(18), "own_device": False},
                          "W": {"team": white, "token": secrets.token_urlsafe(18), "own_device": False}},
                "board": board, "history": [board], "moves": [], "turn": "B", "version": 0,
                "generation": 0, "clock_started": None, "remaining": {"B": 600., "W": 600.},
                "streak": {"B": 0, "W": 0}, "evaluated_ply": 0, "candidates": [],
                "forbidden": None, "best_legal_winrate": None, "pause_reason": "", "result": None,
                "captures": {"B": 0, "W": 0}, "score": None, "time_control_version": "v0.5.0"}
            for color in ("B", "W"):
                game["remaining"][color] = time_limit(game, False)
            init_pregame(game)
            state["games"].append(game)
        event("round", round_info)
        store.save()
        return round_info

    if duel:
        @app.post("/api/duel/new")
        async def new_duel(body: DuelInput):
            # Use the same game, dice, clock and AI implementation as the LAN
            # event. The launcher supplies a completely separate data directory.
            settings = body.model_dump(include=set(SettingsInput.model_fields))
            if (body.board_size not in (9, 13, 19) or body.rules not in ("chinese", "japanese")
                    or not math.isfinite(body.komi) or body.threshold != .40 or not body.allow_pass):
                raise HTTPException(400, "請選擇有效棋盤、貼目與規則；備用版固定 40% 門檻並允許虛手")
            names = [body.team_a.strip(), body.team_b.strip()]
            if not all(names) or names[0] == names[1]:
                raise HTTPException(400, "請填寫兩個不同的隊名")
            if not body.rules_confirmed:
                raise HTTPException(400, "請先確認本局規則")
            async with lock:
                if ai.failure:
                    raise HTTPException(503, "AI 尚未就緒，請先檢查程式視窗")
                if any(g["status"] != "finished" for g in state["games"]):
                    raise HTTPException(409, "請先完成目前棋局；暫停的棋局可恢復或由主辦裁定")
                if state["games"] and any(state["settings"][key] != value for key, value in settings.items()):
                    raise HTTPException(409, "已有棋局紀錄，棋盤與計分規則已鎖定；請沿用目前規則")
                state["settings"].update(settings)
                teams = []
                for name in names:
                    team = {"id": uuid.uuid4().hex[:12], "name": name, "players": ["隊長", "隊員"],
                            "mars_points": [0, 0], "checked_in": True, "eligibility_checked": True,
                            "order": len(state["teams"]) + 1,
                            "play_code": new_team_code(t["play_code"] for t in state["teams"])}
                    state["teams"].append(team)
                    teams.append(team["id"])
                new_round("special", len(state["rounds"]) + 1, [teams])
                game = state["games"][-1]
                game["championship_clock"] = body.championship
                for color, device in (("B", body.device_a), ("W", body.device_b)):
                    game["seats"][color]["own_device"] = device
                    game["remaining"][color] = time_limit(game, device)
                init_pregame(game)["armed"] = True
                game["version"] += 1
                event("duel_create", {"game": game["id"], "teams": teams, "championship": body.championship})
                store.save()
                return public_game(game)

        @app.post("/api/duel/games/{ident}/action")
        async def duel_action(ident: str, body: Action):
            return await referee(ident, body, "Bearer " + store.admin_token)

        @app.get("/api/duel/export")
        async def duel_export():
            response = await export("Bearer " + store.admin_token)
            response.headers["Content-Disposition"] = 'attachment; filename="duel-records.json"'
            return response

    @app.post("/api/admin/special-tables")
    async def special_table(body: SpecialInput, authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            if not state["settings"]["rules_confirmed"]:
                raise HTTPException(409, "請先在設定頁確認正式規則")
            if ai.failure:
                raise HTTPException(503, "KataGo 尚未就緒")
            ids = {t["id"] for t in state["teams"]}
            if body.team_a == body.team_b or body.team_a not in ids or body.team_b not in ids:
                raise HTTPException(400, "請選擇兩支不同且已登記的隊伍")
            number = 1 + sum(r["stage"] == "special" for r in state["rounds"])
            info = new_round("special", number, [(body.team_a, body.team_b)])
            event("special_table", {"round": info["id"], "teams": [body.team_a, body.team_b], "counts_for_standings": False})
            store.save()
            return {"round": info, "game": public_game(state["games"][-1])}

    @app.post("/api/admin/rounds/current/start")
    async def start_current_round(authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            current = next((r for r in reversed(state["rounds"]) if r["stage"] in OFFICIAL_STAGES), None)
            if current is None:
                raise HTTPException(409, "尚未建立正式輪次")
            if ai.failure:
                raise HTTPException(503, "KataGo 尚未就緒")
            armed, started = 0, 0
            for game in state["games"]:
                if game["round_id"] != current["id"] or game["status"] != "ready":
                    continue
                p = init_pregame(game)
                if not p["armed"]:
                    p["armed"] = True
                    armed += 1
                    game["version"] += 1
                started += int(start_if_ready(game))
            event("round_start", {"round": current["id"], "armed": armed, "started": started})
            store.save()
            return {"armed": armed, "started": started}

    @app.post("/api/admin/rounds/{kind}")
    async def next_round(kind: str, authorization: str | None = Header(None)):
        require_admin(authorization)
        async with lock:
            if not state["settings"]["rules_confirmed"]:
                raise HTTPException(409, "請先在設定頁確認正式規則")
            if ai.failure:
                raise HTTPException(503, "KataGo 尚未就緒")
            if any(g["status"] != "finished" for g in state["games"] if g["stage"] in OFFICIAL_STAGES):
                raise HTTPException(409, "上一輪仍有未完成棋局")
            try:
                if kind == "swiss":
                    number = 1 + sum(r["stage"] == "swiss" for r in state["rounds"])
                    if number > SWISS_ROUNDS or any(r["stage"] in OFFICIAL_STAGES and r["stage"] != "swiss" for r in state["rounds"]):
                        raise ValueError("瑞士制共五輪")
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
            team = next(t for t in state["teams"] if t["id"] == g["seats"][color]["team"])
            supplied = (authorization or "").removeprefix("Bearer ")
            return {"color": color, "team": team["id"], "credential": "team" if matches_team_code(team["play_code"], supplied) else "legacy"}

    @app.post("/api/games/{ident}/device")
    async def choose_device(ident: str, body: DeviceInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            color = seat(g, authorization)
            if g["status"] != "ready":
                raise HTTPException(409, "須在每場對弈開始前決定是否使用自備設備")
            p = init_pregame(g)
            if p["ready"].get(g["seats"][color]["team"]):
                raise HTTPException(409, "已準備完成，不能再變更自備設備")
            g["seats"][color]["own_device"] = body.own_device
            g["remaining"][color] = time_limit(g, body.own_device)
            g["version"] += 1
            event("device", {"game": ident, "color": color, "own_device": body.own_device})
            store.save()
            return public_game(g)

    @app.post("/api/games/{ident}/dice")
    async def dice(ident: str, body: DiceInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            p, team_id = require_pregame(g, body, authorization)
            if p["winner"] or team_id in p["rolls"]:
                raise HTTPException(409, "本次已擲骰，請等待對手或下一次猜先")
            p["rolls"][team_id] = [secrets.randbelow(6) + 1 for _ in range(2)]
            if len(p["rolls"]) == 2:
                ids = list(p["rolls"])
                totals = {tid: sum(p["rolls"][tid]) for tid in ids}
                tied = totals[ids[0]] == totals[ids[1]]
                p["history"].append({"attempt": p["attempt"], "rolls": copy.deepcopy(p["rolls"]), "tie": tied})
                if tied:
                    p["attempt"] += 1
                    p["rolls"] = {}
                else:
                    p["winner"] = max(totals, key=totals.get)
            g["version"] += 1
            event("dice", {"game": ident, "team": team_id, "pregame": copy.deepcopy(p)})
            store.save()
            return public_game(g)

    @app.post("/api/games/{ident}/color")
    async def color_choice(ident: str, body: ColorInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            p, team_id = require_pregame(g, body, authorization)
            if body.color not in ("B", "W") or p["winner"] != team_id:
                raise HTTPException(403, "只有本次點數較高的隊伍可以選擇黑白")
            if any(p["ready"].values()):
                raise HTTPException(409, "已有隊伍準備完成，不能再變更棋色")
            own_color = next(c for c, s in g["seats"].items() if s["team"] == team_id)
            if own_color != body.color:
                g["seats"]["B"], g["seats"]["W"] = g["seats"]["W"], g["seats"]["B"]
                g["remaining"]["B"], g["remaining"]["W"] = g["remaining"]["W"], g["remaining"]["B"]
            p["color_choice"] = body.color
            g["version"] += 1
            event("color_choice", {"game": ident, "team": team_id, "color": body.color})
            store.save()
            return public_game(g)

    @app.post("/api/games/{ident}/ready")
    async def player_ready(ident: str, body: ReadyInput, authorization: str | None = Header(None)):
        async with lock:
            g = game_by_id(ident)
            p, team_id = require_pregame(g, body, authorization)
            if not p["winner"] or not p["color_choice"]:
                raise HTTPException(409, "請先完成雙方擲骰與勝方選擇棋色")
            if ai.failure and body.ready:
                raise HTTPException(503, "KataGo 尚未就緒")
            p["ready"][team_id] = body.ready
            g["version"] += 1
            start_if_ready(g)
            event("player_ready", {"game": ident, "team": team_id, "ready": body.ready})
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
            if chosen != "pass" and chosen == g["forbidden"]:
                raise HTTPException(400, "禁止選擇 AI 勝率最高的落點")
            try:
                board, captured = play(g["board"], color, chosen, cfg["board_size"], g["history"], cfg["rules"], cfg["allow_pass"])
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            g["board"] = board
            g["history"].append(board)
            g["moves"].append([color, chosen])
            g["captures"][color] += captured
            g["turn"] = "W" if color == "B" else "B"
            g["version"] += 1
            event("move", {"game": ident, "ply": len(g["moves"]), "color": color,
                           "move": chosen, "captured": captured, "remaining": g["remaining"].copy()})
            if len(g["moves"]) >= 2 and all(m[1] == "pass" for m in g["moves"][-2:]):
                g["clock_started"] = None
                g["generation"] += 1
                g["candidates"] = []
                g["forbidden"] = None
                g["score"] = score_position(g["board"], cfg["board_size"], cfg["komi"], cfg["rules"], g["captures"])
                event("score", {"game": ident, "score": g["score"]})
                if g["score"]["winner"]:
                    finish(g, g["seats"][g["score"]["winner"]]["team"], "score", 1)
                else:
                    g["status"] = "paused"
                    g["pause_reason"] = "計分平手，請主辦裁定勝隊或重賽"
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
                init_pregame(g)["armed"] = True
                start_if_ready(g)
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
                if g.get("score") and g["score"]["winner"] is None:
                    raise HTTPException(409, "本局已雙方虛手且計分平手，請裁定勝隊或重賽")
                if g["candidates"]:
                    g["status"] = "playing"
                    g["clock_started"] = time.time()
                else:
                    queue_analysis(g)
            elif body.action == "adjudicate":
                if body.winner not in [s["team"] for s in g["seats"].values()] or body.points not in (1., 1.25):
                    raise HTTPException(400, "須指定本場勝隊及 1 或 1.25 積分")
                finish(g, body.winner, "referee: " + body.reason if body.reason.strip() else "referee", body.points)
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
                          "captures": {"B": 0, "W": 0}, "score": None,
                          "remaining": {c: time_limit(g, s["own_device"]) for c, s in g["seats"].items()}})
                g.pop("pregame", None)
                init_pregame(g)
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
            for t in result["teams"]:
                t.pop("play_code", None)
            for g in result["games"]:
                for s in g["seats"].values():
                    s.pop("token", None)
            result["standings"], result["awards"] = standings(state), awards(state)
            for t in result["standings"]:
                t.pop("play_code", None)
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


def create_event_app():
    """Source deployment entry point, matching the portable launcher's setup."""
    return create_app(bootstrap=True)
