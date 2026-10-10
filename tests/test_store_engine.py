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
