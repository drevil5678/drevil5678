"""Loopback-only, untimed two-person practice, with no tournament database."""
import asyncio
import copy
import math
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import KataGo
from .go import IllegalMove, candidates, decode, encode, play, sgf


class PracticeSettings(BaseModel):
    board_size: int = Field(default=19, ge=9, le=19)
    komi: float = Field(default=7.5, ge=-50, le=50)
    rules: str = "chinese"
    forbid_best: bool = True


class PracticeVersion(BaseModel):
    game_id: str
    version: int


class PracticeMove(PracticeVersion):
    move: str = Field(max_length=5)


def create_practice_app(runtime, engine=None):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    ai = engine or KataGo(runtime)
    lock = asyncio.Lock()
    current = None
    import sys
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent

    @asynccontextmanager
    async def lifespan(_):
        await ai.start()
        yield
        await ai.close()

    app = FastAPI(title="雙人單機練習", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ai = ai

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Reject unintended LAN hosting even if a caller constructs the factory.
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return Response("單機練習只允許本機連線", status_code=403)
        hostname = request.url.hostname
        if hostname not in ("127.0.0.1", "localhost", "::1") and not (
                request.client and request.client.host == "testclient" and hostname == "testserver"):
            return Response("請使用本機練習網址", status_code=403)
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return Response("跨來源操作遭拒絕", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    def public():
        if current is None:
            return {"game": None}
        game = copy.deepcopy(current)
        game.pop("history")
        game["candidates"] = game["candidates"][:5]
        return {"game": game}

    def require_version(body):
        if current is None or body.game_id != current["id"] or body.version != current["version"]:
            raise HTTPException(409, "練習棋局已更新，請重新確認")

    async def analyze():
        current["status"] = "analyzing"
        current["candidates"] = []
        current["forbidden"] = None
        try:
            data = await ai.analyze(current["moves"], current["settings"])
            options = candidates(current, current["settings"], data["moveInfos"])
            if not options:
                raise RuntimeError("AI 未提供合法選點")
            current["candidates"] = options
            current["forbidden"] = options[0]["move"] if current["settings"]["forbid_best"] else None
            current["status"] = "playing"
            current["error"] = ""
        except Exception:
            current["status"] = "error"
            current["error"] = "AI 分析暫時失敗。可按重新分析；若仍失敗，重新啟動練習程式。"
        current["version"] += 1

    @app.get("/api/health")
    async def health():
        return {"ok": True, "ai_ready": not ai.failure, "mode": "practice"}

    @app.get("/api/practice/state")
    async def state():
        async with lock:
            return public()

    @app.post("/api/practice/new")
    async def new_game(body: PracticeSettings):
        nonlocal current
        if body.board_size not in (9, 13, 19) or body.rules not in ("chinese", "japanese") or not math.isfinite(body.komi):
            raise HTTPException(400, "請選擇 9／13／19 路、中國／日本規則及有效貼目")
        if ai.failure:
            raise HTTPException(503, "AI 尚未就緒，請重新啟動練習程式")
        async with lock:
            board = "."*(body.board_size**2)
            current = {"id": uuid.uuid4().hex, "settings": {**body.model_dump(), "visits": 128, "allow_pass": True},
                       "board": board, "history": [board], "moves": [], "turn": "B", "version": 0,
                       "status": "analyzing", "candidates": [], "forbidden": None, "error": ""}
            await analyze()
            return public()

    @app.post("/api/practice/move")
    async def move(body: PracticeMove):
        async with lock:
            require_version(body)
            if current["status"] != "playing":
                raise HTTPException(409, "目前不能落子")
            cfg = current["settings"]
            try:
                pos = decode(body.move, cfg["board_size"])
                chosen = "pass" if pos is None else encode(pos, cfg["board_size"])
                if chosen == current["forbidden"]:
                    raise IllegalMove("此練習禁止選擇 AI 首選；可在新局設定切換")
                board, _ = play(current["board"], current["turn"], chosen, cfg["board_size"], current["history"], cfg["rules"])
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            current["board"] = board
            current["history"].append(board)
            current["moves"].append([current["turn"], chosen])
            current["turn"] = "W" if current["turn"] == "B" else "B"
            current["version"] += 1
            if len(current["moves"]) >= 2 and all(m[1] == "pass" for m in current["moves"][-2:]):
                current.update(status="finished", candidates=[], forbidden=None)
            else:
                await analyze()
            return public()

    @app.post("/api/practice/undo")
    async def undo(body: PracticeVersion):
        async with lock:
            require_version(body)
            if not current["moves"]:
                raise HTTPException(409, "沒有可悔棋的棋著")
            current["moves"].pop()
            current["history"].pop()
            current["board"] = current["history"][-1]
            current["turn"] = "B" if len(current["moves"]) % 2 == 0 else "W"
            current["version"] += 1
            await analyze()
            return public()

    @app.post("/api/practice/retry")
    async def retry(body: PracticeVersion):
        async with lock:
            require_version(body)
            if current["status"] != "error":
                raise HTTPException(409, "目前無需重新分析")
            await analyze()
            return public()

    @app.post("/api/practice/finish")
    async def finish(body: PracticeVersion):
        async with lock:
            require_version(body)
            current.update(status="finished", candidates=[], forbidden=None, version=current["version"]+1)
            return public()

    @app.get("/api/practice/sgf")
    async def export():
        async with lock:
            if current is None:
                raise HTTPException(404, "尚未開始練習")
            return Response(sgf(current, current["settings"]), media_type="application/x-go-sgf",
                            headers={"Content-Disposition": 'attachment; filename="practice.sgf"'})

    @app.get("/")
    async def index():
        return FileResponse(root/"web/practice.html")

    app.mount("/static", StaticFiles(directory=root/"web"), name="static")
    return app
