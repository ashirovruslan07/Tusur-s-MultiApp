import time


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


def test_admin_schedule_is_forbidden_for_student(registered_client):
    response = registered_client.get("/api/admin/schedule")

    assert response.status_code == 403


def test_admin_schedule_payload_contains_summary_and_logs(admin_client):
    response = admin_client.get("/api/admin/schedule")

    assert response.status_code == 200
    payload = response.json()
    assert "summary" in payload
    assert "groups" in payload
    assert "syncLogs" in payload
    assert payload["summary"]["total_groups"] >= 1


def test_schedule_progress_job_reports_real_percent(admin_client, app_module, monkeypatch):
    def fake_sync_schedule_groups(*, progress_callback=None, **kwargs):
        assert kwargs["trigger_type"] == "manual"
        if progress_callback:
            progress_callback(total=4, processed=0, message="Старт")
            progress_callback(total=4, processed=1, message="Первая группа")
            progress_callback(total=4, processed=4, message="Готово")
        return {
            "ok": True,
            "total_groups": 4,
            "synced_groups": 4,
            "lesson_count": 12,
            "empty_groups": 0,
            "error_count": 0,
            "message": "Синхронизация завершена",
            "admin": app_module.admin_schedule_payload(),
        }

    monkeypatch.setattr(app_module, "sync_schedule_groups", fake_sync_schedule_groups)

    response = admin_client.post(
        "/api/admin/schedule/sync-all",
        json={"_progress": "1", "refresh_groups": "0", "sync_mode": "current"},
    )

    assert response.status_code == 200
    started = response.json()
    assert started["job_id"]

    progress = wait_for_job(admin_client, started["job_id"])
    assert progress["done"] is True
    assert progress["percent"] == 100
    assert progress["processed"] == 4
    assert progress["total"] == 4
    assert progress["result"]["synced_groups"] == 4


def test_progress_endpoint_requires_admin(registered_client, app_module):
    job = app_module.start_sync_progress_job(
        "Тестовая задача",
        lambda progress: {"ok": True, "total_groups": 1, "message": "Готово"},
    )

    response = registered_client.get(f"/api/admin/schedule/progress/{job['job_id']}")

    assert response.status_code == 403
