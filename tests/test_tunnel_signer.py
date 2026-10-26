"""SSH tunnel Host allowance must never open the LAN UI to loopback hosts."""
from fastapi.testclient import TestClient
from main import create_app


def test_ssh_tunnel_host_allowed_only_for_signer(monkeypatch, tmp_path):
    monkeypatch.setenv("NK_PUBLIC_URL", "http://10.0.0.171:8020")
    monkeypatch.setenv("NK_SIGNER_ALLOW_LOOPBACK_HOST", "1")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/health", headers={"host": "10.0.0.171:8020"}).status_code == 200
        assert client.get("/health", headers={"host": "127.0.0.1:18020"}).status_code == 400
        assert client.post("/api/signer/heartbeat",
                           headers={"host": "localhost:18020"}, json={}).status_code == 403
        assert client.post("/api/signer/heartbeat",
                           headers={"host": "127.0.0.1:18020"}, json={}).status_code == 403
        assert client.post("/api/signer/heartbeat",
                           headers={"host": "untrusted.invalid:18020"}, json={}).status_code == 400


def test_ssh_tunnel_host_rejected_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("NK_PUBLIC_URL", "http://10.0.0.171:8020")
    monkeypatch.delenv("NK_SIGNER_ALLOW_LOOPBACK_HOST", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/signer/heartbeat",
                           headers={"host": "localhost:18020"}, json={}).status_code == 400
