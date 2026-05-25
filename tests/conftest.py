import importlib
import sys
import time

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, monkeypatch):
    db_path = tmp_path / "multiapp_test.sqlite"
    monkeypatch.setenv("MULTIAPP_DB_PATH", str(db_path))
    monkeypatch.setenv("MULTIAPP_TIMEZONE", "Asia/Tomsk")

    for module_name in ("server.main", "server.database"):
        sys.modules.pop(module_name, None)
    package = sys.modules.get("server")
    if package is not None:
        for attribute in ("main", "database"):
            if hasattr(package, attribute):
                delattr(package, attribute)

    app_module = importlib.import_module("server.main")
    app_module.run_scheduled_sync_if_due = lambda *args, **kwargs: {"ok": True, "skipped": True}
    return app_module


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    return _fresh_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(app_module):
    test_client = TestClient(app_module.app)
    yield test_client
    test_client.close()


@pytest.fixture()
def registered_client(client):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Иван Иванов",
            "email": "ivan@example.com",
            "password": "student123",
            "password_confirm": "student123",
        },
    )
    assert response.status_code == 201
    return client


@pytest.fixture()
def admin_client(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200
    return client


def wait_for_job(client, job_id, timeout=5):
    deadline = time.time() + timeout
    payload = None
    while time.time() < deadline:
        response = client.get(f"/api/admin/schedule/progress/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload.get("done"):
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Задача {job_id} не завершилась вовремя: {payload}")
