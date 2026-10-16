import io
import json
import socket
from types import SimpleNamespace

from fastapi.testclient import TestClient

from server import lan
from server.app import create_app
from tests.test_app import TestEngine


def fake_interfaces(monkeypatch):
    entries = {
        "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="192.168.50.10")],
        "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="10.20.0.5")],
        "Disconnected": [SimpleNamespace(family=socket.AF_INET, address="192.168.80.1")],
        "Other": [SimpleNamespace(family=socket.AF_INET, address=ip) for ip in
                  ("127.0.0.1", "169.254.2.1", "8.8.8.8", "0.0.0.0")],
    }
    monkeypatch.setattr(lan.psutil, "net_if_addrs", lambda: entries)
    monkeypatch.setattr(lan.psutil, "net_if_stats", lambda: {
        name: SimpleNamespace(isup=name != "Disconnected") for name in entries})


def test_only_connected_private_ipv4_is_shared(monkeypatch):
    fake_interfaces(monkeypatch)
    info = lan.network_info("0.0.0.0", 8100)
    assert info["lan_enabled"]
    assert [x["url"] for x in info["interfaces"]] == [
        "http://192.168.50.10:8100/#arena", "http://10.20.0.5:8100/#arena"]


def test_binding_controls_which_addresses_can_be_shared(monkeypatch):
    fake_interfaces(monkeypatch)
    local = lan.network_info("127.0.0.1", 8100)
    assert not local["lan_enabled"] and not local["interfaces"]
    assert "Start-LAN.cmd" in local["warning"]
    specific = lan.network_info("10.20.0.5", 8100)
    assert [x["address"] for x in specific["interfaces"]] == ["10.20.0.5"]


def test_unavailable_network_is_not_reported_as_ready(monkeypatch):
    def fail():
        raise OSError("no interfaces")
    monkeypatch.setattr(lan.psutil, "net_if_stats", fail)
    info = lan.network_info("0.0.0.0", 8000)
    assert info["interfaces"] == [] and "ipconfig" in info["warning"]


def test_network_details_require_admin_and_ignore_untrusted_host(tmp_path, monkeypatch):
    fake_interfaces(monkeypatch)
    app = create_app(tmp_path, engine=TestEngine(), network={"host": "0.0.0.0", "port": 8100})
    with TestClient(app) as client:
        assert client.get("/api/admin/network").status_code == 401
        token = app.state.store.admin_token
        response = client.get("/api/admin/network", headers={
            "Authorization": "Bearer " + token, "Host": "untrusted.example:9999"})
        assert response.status_code == 200
        assert "untrusted.example" not in response.text and token not in response.text
        assert response.json()["interfaces"][0]["url"] == "http://192.168.50.10:8100/#arena"
        assert response.json()["backup_dir"] == str((tmp_path/"backups").resolve())


def test_browser_uses_bound_interface_and_opens_admin(monkeypatch):
    import launcher
    import urllib.request
    captured = []
    class Opener:
        def open(self, url, timeout):
            captured.append(url)
            return io.BytesIO(json.dumps({"ok": True}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: captured.append(url))
    stop = SimpleNamespace(wait=lambda _: False, is_set=lambda: False)
    launcher.browser_when_ready(8100, stop, "192.168.50.10")
    assert captured == ["http://192.168.50.10:8100/api/health", "http://192.168.50.10:8100/#admin"]


def test_lan_mode_overrides_existing_loopback_config_without_rewriting(tmp_path, monkeypatch):
    import launcher
    import uvicorn
    import server.app
    fake_interfaces(monkeypatch)
    for key in ("TOURNAMENT_DATA", "AUTO_BACKUP_SECONDS", "KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY"):
        monkeypatch.setenv(key, "placeholder")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    filename = tmp_path/"tournament.json"
    original = json.dumps({"host": "127.0.0.1", "port": port, "data_dir": "data", "open_browser": False})
    filename.write_text(original)
    captured = {}
    def factory(directory, network, bootstrap):
        captured["network"] = network
        captured["bootstrap"] = bootstrap
        return SimpleNamespace(state=SimpleNamespace(initial_roster_loaded=False))
    class Server:
        def __init__(self, config):
            pass
        def run(self, sockets):
            captured["bound"] = sockets[0].getsockname()
    monkeypatch.setattr(server.app, "create_app", factory)
    monkeypatch.setattr(uvicorn, "Server", Server)
    assert launcher.main(["--config", str(filename), "--lan", "--no-browser", "--no-pause"]) == 0
    assert captured["bound"] == ("0.0.0.0", port)
    assert captured["network"] == {"host": "0.0.0.0", "port": port}
    assert captured["bootstrap"] is True
    assert filename.read_text() == original
