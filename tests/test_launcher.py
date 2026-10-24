"""Use real loopback sockets to distinguish TIME_WAIT from an active service."""
import socket
import sys

import pytest
import uvicorn

from nk_manager.store import Store
from scripts.run_wsl import main


def configure(tmp_path,monkeypatch,port):
    Store(tmp_path).setup('tester','isolated-launcher-password')
    monkeypatch.setenv('NK_DATA',str(tmp_path))
    monkeypatch.setenv('NK_PUBLIC_URL','http://localhost:8020')
    monkeypatch.setattr(sys,'argv',['run_wsl.py','--port',str(port),'--data',str(tmp_path)])
    monkeypatch.chdir(tmp_path)


def test_launcher_restarts_on_port_in_time_wait(tmp_path,monkeypatch):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        listener.listen()
        with socket.create_connection(('127.0.0.1',port),timeout=2) as peer:
            accepted,_=listener.accept()
            accepted.close()  # Server actively closes, retaining TIME_WAIT.
            assert peer.recv(1)==b''
    configure(tmp_path,monkeypatch,port)
    calls=[]
    monkeypatch.setattr(uvicorn,'run',lambda *args,**kwargs:calls.append(kwargs))
    main()
    assert len(calls)==1 and calls[0]['port']==port
    assert calls[0]['host']=='127.0.0.1' and calls[0]['workers']==1


def test_launcher_does_not_take_port_from_active_listener(tmp_path,monkeypatch):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        listener.listen()
        configure(tmp_path,monkeypatch,port)
        monkeypatch.setattr(uvicorn,'run',lambda *a,**kw:pytest.fail('Must not launch on occupied port'))
        with pytest.raises(SystemExit,match='занят'):
            main()
        with socket.create_connection(('127.0.0.1',port),timeout=2):
            accepted,_=listener.accept()
            accepted.close()
