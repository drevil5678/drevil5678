import json
import socket
import sqlite3
import pytest
from launcher import InstanceLock, backup_before_start, load_settings, main


def test_local_lock_rejects_duplicate_and_releases_after_exit(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError, match="已有伺服器"):
            with InstanceLock(tmp_path):
                raise AssertionError("duplicate must not acquire lock")
    with InstanceLock(tmp_path):
        pass


def test_relative_paths_follow_configuration_not_working_directory(tmp_path, monkeypatch):
    config = tmp_path/"host/tournament.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"data_dir":"event-data","katago_bin":"engine/katago"}))
    monkeypatch.chdir(tmp_path)
    settings = load_settings(config)
    assert settings["data_dir"] == str(config.parent/"event-data")
    assert settings["katago_bin"] == str(config.parent/"engine/katago")
    assert settings["auto_backup_seconds"] == 600


def test_invalid_config_is_not_overwritten(tmp_path):
    filename = tmp_path/"tournament.json"
    content = '{"port": "8000"}'
    filename.write_text(content)
    with pytest.raises(ValueError, match="port"):
        load_settings(filename)
    assert filename.read_text() == content


def test_startup_backup_preserves_committed_wal_data(tmp_path):
    original = sqlite3.connect(tmp_path/"tournament.sqlite3")
    original.execute("PRAGMA journal_mode=WAL")
    original.execute("PRAGMA wal_autocheckpoint=0")
    original.execute("CREATE TABLE marker (value TEXT)")
    original.execute("INSERT INTO marker VALUES ('preserve-me')")
    original.commit()
    assert (tmp_path/"tournament.sqlite3-wal").exists()
    filename = backup_before_start(tmp_path)
    backup = sqlite3.connect(filename)
    assert backup.execute("SELECT value FROM marker").fetchone()[0] == "preserve-me"
    assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    backup.close()
    original.close()


def test_occupied_port_fails_before_modifying_tournament(tmp_path, monkeypatch):
    for key in ("TOURNAMENT_DATA","AUTO_BACKUP_SECONDS","KATAGO_BIN","KATAGO_MODEL","KATAGO_CONFIG","AI_CONCURRENCY"):
        monkeypatch.setenv(key,"test-placeholder")
    data = tmp_path/"data"
    data.mkdir()
    db = sqlite3.connect(data/"tournament.sqlite3")
    db.execute("CREATE TABLE marker (value TEXT)")
    db.execute("INSERT INTO marker VALUES ('existing-event')")
    db.commit()
    db.close()
    listener = socket.socket()
    listener.bind(("127.0.0.1",0))
    listener.listen()
    filename=tmp_path/"tournament.json"
    filename.write_text(json.dumps({"data_dir":"data"}))
    try:
        result=main(["--config",str(filename),"--host","127.0.0.1","--port",str(listener.getsockname()[1]),"--no-pause","--no-browser"])
        assert result == 1
        assert not (data/"backups").exists() and not (data/"admin-token").exists()
        db=sqlite3.connect(data/"tournament.sqlite3")
        assert db.execute("SELECT value FROM marker").fetchone()[0] == "existing-event"
        db.close()
    finally:
        listener.close()


def test_corrupt_database_is_preserved_without_empty_backup(tmp_path):
    original=tmp_path/"tournament.sqlite3"
    original.write_bytes(b"damaged database preserved for recovery")
    with pytest.raises(sqlite3.DatabaseError):
        backup_before_start(tmp_path)
    assert original.read_bytes()==b"damaged database preserved for recovery"
    assert not list((tmp_path/"backups").glob("*.sqlite3"))
