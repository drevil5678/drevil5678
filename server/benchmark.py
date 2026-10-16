"""Real, queued multi-position analysis without creating tournament records."""
import asyncio
import hashlib
import json
import random
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path

from .go import IllegalMove, encode, play
from .store import DEFAULT_SETTINGS


def current_settings(directory):
    settings = DEFAULT_SETTINGS.copy()
    database = Path(directory) / "tournament.sqlite3"
    if database.exists():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT data FROM state WHERE id=1").fetchone()
            if row:
                settings.update(json.loads(row[0])["settings"])
    return {key: settings[key] for key in ("board_size", "rules", "komi", "visits")}


def position(size, seed, rules="chinese"):
    rng = random.Random(seed)
    board = "." * (size * size)
    history, moves = [board], []
    for number in range(min(60 + seed % 35, size * size // 2)):
        color = "B" if number % 2 == 0 else "W"
        choices = [index for index, stone in enumerate(board) if stone == "."]
        rng.shuffle(choices)
        for index in choices:
            move = encode(index, size)
            try:
                board, _ = play(board, color, move, size, history, rules)
                break
            except IllegalMove:
                continue
        else:
            break
        moves.append([color, move])
        history.append(board)
    return moves


async def run(ai, settings, tables=12, rounds=3):
    if not 1 <= tables <= 32 or not 1 <= rounds <= 10:
        raise ValueError("測速桌數須為 1–32、測試輪數須為 1–10")
    if ai.failure:
        raise RuntimeError(ai.failure)
    durations, samples = [], []
    started = time.perf_counter()
    for round_number in range(rounds):
        positions = [position(settings["board_size"], round_number * 100 + i, settings["rules"]) for i in range(tables)]

        async def query(index, moves):
            stamp = time.perf_counter()
            response = await ai.analyze(moves, settings)
            if not response.get("moveInfos"):
                raise RuntimeError("測速未收到有效 AI 分析")
            duration = time.perf_counter() - stamp
            durations.append(duration)
            samples.append({"round": round_number + 1, "table": index + 1,
                            "moves": len(moves), "position_sha256": hashlib.sha256(json.dumps(moves).encode()).hexdigest(),
                            "seconds_including_queue": round(duration, 3)})

        await asyncio.gather(*(query(index, moves) for index, moves in enumerate(positions)))
        print(f"測速第 {round_number + 1}/{rounds} 輪完成（{tables} 桌）。", flush=True)
    model = Path(ai.model)
    with model.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    ordered = sorted(durations)
    return {"passed": True, "real_ai": True, "engine": ai.status(), "settings": settings,
            "model_sha256": digest, "tables": tables, "rounds": rounds, "analyses": len(durations),
            "positions": "distinct legal midgame positions; not only repeated empty boards",
            "latency_includes_queue": True, "median_seconds": round(statistics.median(ordered), 3),
            "p95_seconds": round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * .95 + .999999))], 3),
            "max_seconds": round(max(ordered), 3), "total_seconds": round(time.perf_counter() - started, 3),
            "samples": samples, "tournament_records_modified": False}
