"""A tunnel must never weaken Host checking outside the signer API."""
import json

import pytest
from fastapi.testclient import TestClient

from main import create_app

CORPORATE = "http://10.0.0.171:8020"
THUMBPRINT = "A" * 40


@pytest.mark.parametrize("host", ["localhost:18020", "127.0.0.1:18020"])
def test_enabled_ssh_tunnel_reaches_only_bearer_authenticated_signer(monkeypatch, tmp_path, host):
    monkeypatch.setenv("NK_PUBLIC_URL", CORPORATE)
    monkeypatch.setenv("NK_SIGNER_ALLOW_LOOPBACK_HOST", "1")
    app = create_app(tmp_path)
    # Synthetic credentials; no real NK API or signing takes place.
    app.state.nk.path.write_text(json.dumps({"signer_token": "synthetic-signer-token", "thumbprint": THUMBPRINT}))
    with TestClient(app) as client:
        assert client.get("/health", headers={"Host": "10.0.0.171:8020"}).status_code == 200
        for path in ("/health", "/", "/api/me"):
            assert client.get(path, headers={"Host": host}).status_code == 400
        assert client.post("/api/login", headers={"Host": host}, json={}).status_code == 400
        assert client.post("/api/signer/heartbeat", headers={"Host": host}, json={}).status_code == 403
        assert client.post("/api/signer/heartbeat", headers={"Host": host,
                           "Authorization": "Bearer incorrect"}, json={}).status_code == 403
        assert client.post("/api/signer/heartbeat", headers={"Host": "untrusted.invalid:18020",
                           "Authorization": "Bearer synthetic-signer-token"},
                           json={"thumbprint": THUMBPRINT}).status_code == 400
        response = client.post("/api/signer/heartbeat",
                               headers={"Host": host, "Authorization": "Bearer synthetic-signer-token"},
                               json={"thumbprint": THUMBPRINT, "status": "connected"})
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}
        assert client.post("/api/signer/next",
                           headers={"Host": host, "Authorization": "Bearer synthetic-signer-token"},
                           json={}).status_code == 200


def test_ssh_tunnel_host_rejected_without_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("NK_PUBLIC_URL", CORPORATE)
    monkeypatch.delenv("NK_SIGNER_ALLOW_LOOPBACK_HOST", raising=False)
    app = create_app(tmp_path)
    app.state.nk.path.write_text(json.dumps({"signer_token": "synthetic-signer-token", "thumbprint": THUMBPRINT}))
    with TestClient(app) as client:
        assert client.post("/api/signer/heartbeat",
                           headers={"Host": "127.0.0.1:18020",
                                    "Authorization": "Bearer synthetic-signer-token"},
                           json={"thumbprint": THUMBPRINT}).status_code == 400
