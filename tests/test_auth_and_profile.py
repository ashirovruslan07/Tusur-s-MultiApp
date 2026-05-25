def test_health_uses_isolated_database(client, app_module):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["db"] == str(app_module.db.DB_PATH)
    assert "multiapp_test.sqlite" in payload["db"]


def test_protected_endpoints_require_authorization(client):
    response = client.get("/api/dashboard")

    assert response.status_code == 401


def test_registration_creates_empty_student_account(client, app_module):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Петр Петров",
            "email": "PETR@example.com",
            "password": "student123",
            "password_confirm": "student123",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    user_id = payload["user"]["user_id"]
    assert payload["user"]["email"] == "petr@example.com"
    assert payload["profile"]["user_id"] == user_id

    tables = [
        "Accounts",
        "Categories",
        "Transactions",
        "Transfers",
        "Workout_Plans",
        "Plan_Exercises",
        "Workout_Logs",
        "Workout_Log_Exercises",
        "Tasks",
        "Events",
        "Notes",
        "Portfolio_Projects",
        "Portfolio_Achievements",
        "Portfolio_Certificates",
        "Portfolio_Files",
        "Portfolio_Skills",
    ]
    for table in tables:
        row = app_module.db.fetch_one(
            f"SELECT COUNT(*) AS count FROM {table} WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        assert row["count"] == 0, f"{table} должен быть пустым для нового пользователя"


def test_registration_validation(client):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Иван",
            "email": "bad-email",
            "password": "123",
            "password_confirm": "456",
        },
    )

    assert response.status_code == 400


def test_profile_can_select_existing_group(registered_client):
    metadata = registered_client.get("/api/profile").json()["metadata"]
    group = metadata["groups"][0]

    response = registered_client.patch("/api/profile", json={"group_id": group["group_id"]})

    assert response.status_code == 200
    assert response.json()["profile"]["group_id"] == group["group_id"]


def test_profile_rejects_unknown_group(registered_client):
    response = registered_client.patch("/api/profile", json={"group_id": 999999})

    assert response.status_code == 400
