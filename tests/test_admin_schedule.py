import time
from types import SimpleNamespace


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
    assert "weeks" in payload
    assert "syncLogs" in payload
    assert payload["summary"]["total_groups"] >= 1


def test_schedule_sync_targets_current_and_next_weeks(app_module):
    assert app_module.target_offsets("current") == [0]
    assert app_module.target_offsets("next") == [1]
    assert app_module.target_offsets("current_next") == [0, 1]


def test_schedule_week_id_and_type_are_completed_for_future_weeks(app_module):
    current_week = SimpleNamespace(week_id=900, week_number=12, week_type="четная")

    week = app_module.complete_week_info(
        {"week_id": None, "week_number": None, "week_type": None, "starts_at": None},
        current_week,
        3,
    )

    assert week["week_id"] == 903
    assert week["week_number"] == 15
    assert week["week_type"] == "нечетная"
    assert week["starts_at"] == app_module.fallback_week_start(3)


def test_future_week_ids_are_stored_without_parsing_lessons(app_module):
    current_week = SimpleNamespace(week_id=900, week_number=12, week_type="четная")

    with app_module.db.transaction() as conn:
        rows = app_module.ensure_future_week_ids(conn, current_week)

    assert [row["source_week_id"] for row in rows] == [900, 901, 902, 903]
    assert [row["week_type"] for row in rows] == ["четная", "нечетная", "четная", "нечетная"]


def test_schedule_without_parser_date_gets_current_week(registered_client, app_module):
    with app_module.db.transaction() as conn:
        save_result = app_module.save_parsed_schedule(
            conn,
            "fvs",
            "515-1",
            {"week_type": "обычная", "week_id": None, "week_number": None, "starts_at": None},
            [
                {
                    "day_number": 1,
                    "lesson_number": 1,
                    "discipline": "Тестовая дисциплина",
                    "lesson_type": "Практика",
                    "auditorium": "425",
                    "teacher_name": "Преподаватель",
                    "start_time": "08:50",
                    "end_time": "10:25",
                }
            ],
        )

    profile_response = registered_client.patch("/api/profile", json={"group_id": save_result["group_id"]})
    assert profile_response.status_code == 200

    schedule_response = registered_client.get("/api/schedule")
    assert schedule_response.status_code == 200
    schedule = schedule_response.json()
    assert schedule["week"]["starts_at"] == app_module.fallback_week_start().isoformat()
    assert schedule["week"]["ends_at"] == (app_module.fallback_week_start() + app_module.timedelta(days=6)).isoformat()
    assert schedule["lessons"][0]["discipline"] == "Тестовая дисциплина"

    admin_response = registered_client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert admin_response.status_code == 200
    admin_payload = registered_client.get("/api/admin/schedule").json()
    assert any(week["starts_at"] == schedule["week"]["starts_at"] for week in admin_payload["weeks"])


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
