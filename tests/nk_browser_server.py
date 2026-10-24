"""Isolated browser harness: synthetic NK transport, no external requests."""
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from fastapi.responses import JSONResponse
import httpx
import uvicorn

from main import create_app
from tests.test_nk import NK, signer


class BrowserNK(NK):
    def handle(self, request):
        if request.url.path=="/v3/categories":
            return httpx.Response(200,json={"result":[{"cat_id":123,"cat_name":"Соединители"}]})
        response=super().handle(request)
        if request.url.path=="/v3/feed-moderation":
            self.raw["good_detailed_status"]=["notsigned"]
        return response


if __name__=="__main__":
    directory=Path(os.environ["NK_DATA"])
    app=create_app(directory)
    app.state.store.setup("tester","isolated-test-admin-password")
    remote=BrowserNK()
    app.state.nk.client.transport=httpx.MockTransport(remote.handle)
    key,cert,thumb=signer()
    for name,data in (("private.pem",key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())),("cert.pem",cert.public_bytes(serialization.Encoding.PEM)),("certificate.json",json.dumps({"thumbprint":thumb}).encode())):
        file=directory/name
        file.write_bytes(data)
        file.chmod(0o600)
    uvicorn.run(app,host="127.0.0.1",port=int(os.environ["NK_TEST_PORT"]),access_log=False)
