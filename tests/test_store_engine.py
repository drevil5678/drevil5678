import asyncio
import sqlite3
from server.engine import KataGo
from server.store import Store


def test_complete_audit_survives_checkpoints_backup_and_restart(tmp_path):
    store = Store(tmp_path)
    for i in range(250):
        store.record_event({"kind": "test", "sequence": i})
        if i%10 == 0:
            store.save()
    store.save()
    assert len(store.state["events"]) == 100
    assert [e["sequence"] for e in store.all_events()] == list(range(250))
    filename = store.backup()
    backup = sqlite3.connect(tmp_path/"backups"/filename)
    assert backup.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 250
    backup.close()
    store.db.close()
    reopened = Store(tmp_path)
    assert len(reopened.all_events()) == 250
    assert len(reopened.state["events"]) == 100
    reopened.db.close()


def test_engine_refuses_reversed_winrate_perspective(tmp_path, monkeypatch):
    binary, model, config = (tmp_path/p for p in ("binary","model","config"))
    binary.write_text("unused")
    model.write_text("unused")
    config.write_text("reportAnalysisWinratesAs = WHITE\n")
    for key, value in (("KATAGO_BIN",binary),("KATAGO_MODEL",model),("KATAGO_CONFIG",config)):
        monkeypatch.setenv(key,str(value))
    engine = KataGo(tmp_path)
    asyncio.run(engine.start())
    assert engine.process is None
    assert "SIDETOMOVE" in engine.failure


def test_analysis_wire_includes_actual_komi_rules_and_position(tmp_path):
    import json
    from types import SimpleNamespace
    async def check():
        engine = KataGo(tmp_path)
        captured = []
        class Sink:
            def write(self, data):
                query = json.loads(data)
                captured.append(query)
                engine.pending[query["id"]].set_result({"moveInfos": [{"move": "D4", "winrate": .5, "visits": 10}]})
            async def drain(self):
                pass
        engine.process = SimpleNamespace(returncode=None, stdin=Sink())
        moves = [["B", "D4"], ["W", "Q16"]]
        for komi in (7.5, 6.5):
            await engine.analyze(moves, {"board_size": 19, "komi": komi, "rules": "chinese", "visits": 128})
        assert [q["komi"] for q in captured] == [7.5, 6.5]
        assert all(q["moves"] == moves and q["rules"] == "chinese-ogs" and q["boardXSize"] == 19 for q in captured)
    asyncio.run(check())
