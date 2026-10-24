"""Synthetic official-contract responses only. Never contacts productive NK."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
import httpx
import pytest

from nk_manager.nk import Engine, Client, RemoteError
from nk_manager.service import Conflict, Service
from nk_manager.store import Store


class NK:
    def __init__(self):
        self.raw={"good_id":111,"good_name":"Разъём","tnved":"8536","good_status":"draft","good_detailed_status":["draft"],"good_signed":False,
                  "categories":[{"cat_id":123,"cat_name":"Соединители"}],"brand_name":"BRAND","identified_by":[{"type":"gtin","level":"trade-unit","value":"04640000000011","multiplier":1}],
                  "good_attrs":[{"attr_id":1,"attr_name":"Страна","attr_value":"RU"},{"attr_id":2,"attr_name":"Модель / артикул","attr_value":"PN-ALPHA"},{"attr_id":13933,"attr_name":"ТН ВЭД","attr_value":"8536901000"}]}
        self.requests=[]
        self.timeout_path=None
        self.delay_values=False
        self.xml='<?xml version="1.0" encoding="UTF-8"?>\r\n<good>Точные байты &amp; тест</good>'

    def handle(self,request):
        path=request.url.path
        self.requests.append((request.method,path,dict(request.url.params),request.content))
        if path==self.timeout_path:
            raise httpx.ReadTimeout("secret URL must never leak",request=request)
        if path=="/v4/product-list":
            result={"total":1,"goods":[{"good_id":111}]}
        elif path=="/v3/feed-product":
            result=[copy.deepcopy(self.raw)]
        elif path=="/v3/attributes":
            result=[{"attr_id":a["attr_id"],"attr_name":a["attr_name"],"attr_type":"o","attr_field_type":"text"} for a in self.raw["good_attrs"]]
        elif path=="/v3/feed":
            body=json.loads(request.content)
            if not body.get("good_id"):
                self.raw["good_id"]=222
                self.raw["identified_by"][0]["value"]=body["gtin"]
            if not self.delay_values:
                for attr in body.get("good_attrs",[]):
                    found=next(a for a in self.raw["good_attrs"] if a["attr_id"]==attr["attr_id"])
                    found["attr_value"]=attr["attr_value"]
                if "good_name" in body:
                    self.raw["good_name"]=body["good_name"]
            result={"feed_id":999}
        elif path=="/v3/feed-status":
            result={"feed_id":999,"status":"Received","status_id":1}
        elif path=="/v3/feed-moderation":
            self.raw["good_detailed_status"]=["moderation"]
            result={"good_id":111}
        elif path=="/v3/feed-product-document":
            assert json.loads(request.content)=={"goodIds":[111],"publicationAgreement":False}
            result={"xmls":[{"goodId":111,"xml":self.xml}],"errors":[]}
        elif path=="/v3/feed-product-sign-pkcs":
            body=json.loads(request.content)
            assert base64.b64decode(body[0]["base64Xml"])==self.xml.encode()
            assert body[0]["goodId"]==111
            self.raw.update(good_status="published",good_detailed_status=["published"],good_signed=True)
            result={"signed":[111],"errors":[]}
        elif path=="/v3/generate-gtins":
            result={"drafts":[{"gtin":"04640000000028"}]}
        else:
            raise AssertionError("Unexpected method: "+path)
        return httpx.Response(200,json={"apiversion":3,"result":result})


def signer():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Synthetic Test Only")])
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1)).sign(key,hashes.SHA256())
    return key,cert,cert.fingerprint(hashes.SHA1()).hex().upper()


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path)
    remote=NK()
    engine=Engine(store,httpx.MockTransport(remote.handle))
    key,cert,thumb=signer()
    engine.save_config("tester",{"api_key":"test-key-secret","thumbprint":thumb,"writes_enabled":True})
    engine.sync("tester")
    engine.tick()
    return store,Service(store),engine,remote,key,cert,thumb


def tick(engine):
    with engine.store.transaction() as db:
        db.execute("UPDATE nk_jobs SET next_at=0")
    engine.tick()


def current(service):
    return service.cards()[0]


def test_sync_real_id_attributes_dates_and_no_duplicate_records(env):
    store,service,engine,remote,*_=env
    card=current(service)
    assert card["external_id"]=="111"
    assert card["pn"]=="PN-ALPHA"
    assert card["tnved"]=="8536901000"
    assert card["source"]=="nk_api"
    assert card["source_snapshot"]["nk"]["good_id"]==111
    params=remote.requests[0][2]
    assert params["from_date"]=="2000-01-01 00:00:00"
    assert "to_date" in params
    engine.sync("tester");tick(engine)
    assert len(service.cards())==1
    assert engine.status()["sync"]["state"]=="confirmed"
    assert "test-key-secret" not in json.dumps(engine.status())
    assert engine.path.stat().st_mode & 0o777==0o600


def edit(env):
    store,service,engine,remote,*_=env
    card=service.claim("tester",current(service)["id"],current(service)["revision"])
    card["attributes"]["Страна [#1]"]="FR"
    return service.save("tester",card,card["id"])


def test_save_existing_moderate_sign_and_confirm_published(env):
    store,service,engine,remote,key,cert,thumb=env
    card=edit(env)
    save=engine.prepare("tester","save",card["id"],card["revision"])
    assert engine.prepare("tester","save",card["id"],card["revision"])==save
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(save,))["state"]=="submitted"
    with pytest.raises(Conflict):service.save("tester",card,card["id"])
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(save,))["state"]=="confirmed"
    assert current(service)["dirty"]==0
    assert current(service)["external_id"]=="111"
    c=current(service);moderate=engine.prepare("tester","moderate",c["id"],c["revision"])
    tick(engine)
    remote.raw["good_detailed_status"]=["notsigned"]
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(moderate,))["state"]=="confirmed"
    c=current(service);publish=engine.prepare("tester","publish",c["id"],c["revision"])
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(publish,))["state"]=="signing"
    job=engine.signer_next(thumb)
    assert job["detached"] is True
    exact=base64.b64decode(job["payload_base64"])
    assert exact==remote.xml.encode()
    assert hashlib.sha256(exact).hexdigest()==job["sha256"]
    der=pkcs7.PKCS7SignatureBuilder().set_data(exact).add_signer(cert,key,hashes.SHA256()).sign(serialization.Encoding.DER,[pkcs7.PKCS7Options.DetachedSignature,pkcs7.PKCS7Options.Binary])
    engine.signer_result({**job,"signature":base64.b64encode(der).decode()})
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(publish,))["state"]=="submitted"
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(publish,))["state"]=="confirmed"
    assert current(service)["external_status"]=="Опубликована"
    assert remote.raw["good_signed"] is True
    assert len([r for r in remote.requests if r[1]=="/v3/feed-product-sign-pkcs"])==1


def test_timeout_after_mutating_get_never_retries(env):
    store,service,engine,remote,*_=env
    remote.timeout_path="/v3/feed-moderation"
    c=current(service);id=engine.prepare("tester","moderate",c["id"],c["revision"])
    tick(engine);tick(engine);tick(engine)
    job=store.one("SELECT * FROM nk_jobs WHERE id=?",(id,))
    assert job["state"]=="unknown"
    assert "test-key-secret" not in job["error"]
    assert len([r for r in remote.requests if r[1]=="/v3/feed-moderation"])==1
    remote.timeout_path=None
    remote.raw["good_detailed_status"]=["moderation"]
    engine.reconcile("admin",id)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="confirmed"


def test_feed_received_does_not_prove_values_changed(env):
    store,service,engine,remote,*_=env
    card=edit(env);remote.delay_values=True
    id=engine.prepare("tester","save",card["id"],card["revision"])
    tick(engine);tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="submitted"
    assert current(service)["dirty"]==1
    assert current(service)["attributes"]["Страна [#1]"]=="FR"


def test_remote_conflict_and_sync_preserve_local_base(env):
    store,service,engine,remote,*_=env
    card=edit(env)
    original=card["source_snapshot"]["nk"]
    remote.raw["good_attrs"][0]["attr_value"]="CN"
    engine.apply_remote(remote.raw)
    assert current(service)["attributes"]["Страна [#1]"]=="FR"
    assert current(service)["source_snapshot"]["nk"]==original
    with pytest.raises(Conflict):engine.prepare("tester","save",card["id"],card["revision"])


def test_gtin_allocation_is_separate_and_never_repeated(env):
    store,service,engine,remote,*_=env
    c=service.save("tester",{"pn":"OTHER-INDEPENDENT-PRODUCT"})
    id=engine.prepare("tester","gtin",c["id"],c["revision"])
    tick(engine);tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="confirmed"
    assert service.get(c["id"])["gtin"]=="04640000000028"
    with pytest.raises(ValueError):engine.prepare("tester","gtin",c["id"],service.get(c["id"])["revision"])
    assert len([r for r in remote.requests if r[1]=="/v3/generate-gtins"])==1


def test_stop_before_dispatch_cancels_job_and_signer_cannot_lease(env):
    store,service,engine,remote,*_=env
    c=current(service);id=engine.prepare("tester","moderate",c["id"],c["revision"])
    service.archive("tester",c["id"],c["revision"],"Больше не требуется")
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="cancelled"
    assert not any(r[1]=="/v3/feed-moderation" for r in remote.requests)


def test_sync_splits_time_ranges_on_413_without_losing_cards(tmp_path):
    store=Store(tmp_path);seen=[]
    def handle(request):
        seen.append(dict(request.url.params))
        a=datetime.fromisoformat(request.url.params["from_date"]);b=datetime.fromisoformat(request.url.params["to_date"])
        if (b-a).total_seconds()>2:return httpx.Response(413)
        return httpx.Response(200,json={"result":{"goods":[],"total":0}})
    engine=Engine(store,httpx.MockTransport(handle));engine.save_config("a",{"api_key":"secret"})
    id=engine.enqueue("a","sync",{"from":"2026-01-01 00:00:00","to":"2026-01-01 00:00:04"})
    for _ in range(4):tick(engine)
    assert len(seen)==3
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="confirmed"


def test_cms_and_lease_protection(env):
    store,service,engine,remote,key,cert,thumb=env
    remote.raw["good_detailed_status"]=["notsigned"]
    engine.apply_remote(remote.raw)
    c=current(service);id=engine.prepare("tester","publish",c["id"],c["revision"]);tick(engine)
    job=engine.signer_next(thumb)
    assert engine.signer_next(thumb)=={}
    with pytest.raises(ValueError):engine.signer_result({**job,"signature":base64.b64encode(b'x'*100).decode()})
    with pytest.raises(Conflict):engine.signer_result({**job,"lease":"wrong","signature":""})
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="signing"


def test_create_uses_provided_gtin_and_confirmed_remote_id(env):
    store,service,engine,remote,*_=env
    c=service.save("tester",{"pn":"INDEPENDENT-MODEL","name":"Другой товар","gtin":"04640000000028","tnved":"8536901000","card_type":"unit","attributes":{"Модель / артикул [#2]":"INDEPENDENT-MODEL"}})
    id=engine.prepare("tester","save",c["id"],c["revision"],{"cat_id":123,"brand":"BRAND"})
    tick(engine);tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(id,))["state"]=="confirmed"
    assert service.get(c["id"])["external_id"]=="222"
    assert service.get(c["id"])["gtin"]=="04640000000028"
    assert not any(r[1]=="/v3/generate-gtins" for r in remote.requests)
    sent=json.loads(next(r[3] for r in remote.requests if r[1]=="/v3/feed"))
    assert "good_id" not in sent


def test_stale_sync_and_environment_switch_are_blocked(env):
    store,service,engine,remote,*_=env
    with pytest.raises(Conflict):engine.save_config("a",{"environment":"sandbox","api_key":"other"})
    with store.transaction() as db:
        db.execute("UPDATE nk_jobs SET updated=? WHERE kind='sync'",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
    c=current(service)
    with pytest.raises(Conflict):engine.prepare("tester","moderate",c["id"],c["revision"])


def test_http_error_and_retry_after_never_expose_credential(tmp_path):
    transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={"Retry-After":"120"},text=str(req.url)))
    client=Client(lambda:{"api_key":"very-secret-key","environment":"production"},transport)
    with pytest.raises(RemoteError) as result:client.call("GET","/v4/product-list")
    assert result.value.code==429
    assert "very-secret-key" not in str(result.value)
    assert client.retry_until>0


def test_existing_category_and_type_are_never_silently_discarded(env):
    store,service,engine,*_=env
    card=current(service)
    card['category']='Другая категория'
    card=service.save('tester',card,card['id'],admin=True)
    with pytest.raises(ValueError,match='категории или типа'):
        engine.prepare('tester','save',card['id'],card['revision'],admin=True)


def test_signer_endpoint_requires_separate_bearer_without_browser_origin(tmp_path,monkeypatch):
    from main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NK_PUBLIC_URL','http://localhost:8020')
    app=create_app(tmp_path)
    app.state.nk.save_config('a',{'api_key':'test-key-secret','thumbprint':'A'*40})
    client=TestClient(app,base_url='http://localhost:8020')
    assert client.post('/api/signer/next',json={}).status_code==403
    assert client.post('/api/signer/next',json={},headers={'Authorization':'Bearer test-key-secret'}).status_code==403
    token=app.state.nk.config()['signer_token']
    assert client.post('/api/signer/next',json={},headers={'Authorization':'Bearer '+token}).status_code==200
    assert client.post('/api/admin/nk/config',json={}).status_code==403


def test_signer_heartbeat_persists_and_rejects_wrong_cert(tmp_path,monkeypatch):
    from main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NK_PUBLIC_URL','http://localhost:8020')
    app=create_app(tmp_path)
    app.state.nk.save_config('a',{'api_key':'test-key-secret','thumbprint':'A'*40})
    client=TestClient(app,base_url='http://localhost:8020')
    auth={'Authorization':'Bearer '+app.state.nk.config()['signer_token']}
    body={'thumbprint':'A'*40,'expires':'2027-02-05T11:05:00Z','status':'Ready'}
    assert client.post('/api/signer/heartbeat',json=body).status_code==403
    response=client.post('/api/signer/heartbeat',json=body,headers=auth)
    assert response.status_code==200
    heartbeat=app.state.nk.status()['signer']
    assert heartbeat['thumbprint']==body['thumbprint']
    assert heartbeat['expires']==body['expires']
    assert heartbeat['status']=='Ready' and heartbeat['time']
    assert client.post('/api/signer/heartbeat',json={**body,'thumbprint':'B'*40},headers=auth).status_code==422
    assert app.state.nk.status()['signer']==heartbeat
    assert client.post('/api/signer/next',json={},headers=auth).json()=={}
