import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from server.benchmark import current_settings, position, run
from server.go import play
from server.store import Store


def test_midgame_samples_are_distinct_legal_and_reproducible():
    sequences = [position(19, seed) for seed in range(12)]
    assert len({json.dumps(moves) for moves in sequences}) == 12
    assert all(len(moves) >= 60 for moves in sequences)
    assert sequences[0] == position(19, 0)
    for moves in sequences:
        board, history = "." * 361, ["." * 361]
        for color, move in moves:
            board, _ = play(board, color, move, 19, history)
            history.append(board)


def test_readonly_benchmark_uses_actual_rules_and_does_not_modify_event_or_key(tmp_path):
    store = Store(tmp_path)
    store.state["settings"].update(board_size=13, komi=6.5, rules="japanese", visits=384)
    store.save()
    store.db.close()
    files = [tmp_path / "tournament.sqlite3", tmp_path / "admin-token"]
    before = [path.read_bytes() for path in files]
    assert current_settings(tmp_path) == {"board_size": 13, "komi": 6.5, "rules": "japanese", "visits": 384}
    assert [path.read_bytes() for path in files] == before
    missing = tmp_path / "new-event"
    assert current_settings(missing)["visits"] == 128
    assert not missing.exists()


def test_benchmark_records_real_queue_latency_and_different_samples(tmp_path):
    model = tmp_path / "model"
    model.write_bytes(b"explicit unit-test model marker")

    class SerialTestAI:
        failure = ""

        def __init__(self):
            self.model = model
            self.limit = asyncio.Semaphore(1)
            self.calls = []

        async def analyze(self, moves, settings):
            self.calls.append((moves, settings.copy()))
            async with self.limit:
                await asyncio.sleep(.01)
                return {"moveInfos": [{"move": "pass"}]}

        def status(self):
            return {"backend": "test-double", "ready": True}

    async def exercise():
        ai = SerialTestAI()
        settings = {"board_size": 19, "rules": "chinese", "komi": 7.5, "visits": 128}
        report = await run(ai, settings, tables=3, rounds=2)
        assert len(ai.calls) == report["analyses"] == 6
        assert report["latency_includes_queue"] and report["max_seconds"] >= .02
        assert all(actual == settings for _, actual in ai.calls)
        assert len({sample["position_sha256"] for sample in report["samples"]}) == 6
        assert report["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
        with pytest.raises(ValueError):
            await run(ai, settings, tables=0)

    asyncio.run(exercise())
