def test_settings_update_and_validation(registered_client):
    response = registered_client.patch(
        "/api/settings",
        json={"theme": "dark", "notifications_enabled": "1", "lesson_reminder_minutes": 30},
    )

    assert response.status_code == 200
    assert response.json()["settings"]["theme"] == "dark"
    assert response.json()["settings"]["lesson_reminder_minutes"] == 30

    invalid = registered_client.patch("/api/settings", json={"theme": "neon"})
    assert invalid.status_code == 400


def test_finance_account_category_transaction_flow(registered_client):
    account_response = registered_client.post(
        "/api/finance/accounts",
        json={
            "account_name": "Основная карта",
            "account_type": "карта",
            "balance": 1000,
            "currency": "rub",
        },
    )
    assert account_response.status_code == 200
    account = account_response.json()["accounts"][0]
    assert account["account_name"] == "Основная карта"
    assert float(account["balance"]) == 1000
    assert account["currency"] == "RUB"

    category_response = registered_client.post(
        "/api/finance/categories",
        json={"category_name": "Стипендия", "category_type": "income"},
    )
    assert category_response.status_code == 200
    category = category_response.json()["categories"][0]

    transaction_response = registered_client.post(
        "/api/finance/transactions",
        json={
            "account_id": account["account_id"],
            "category_id": category["category_id"],
            "transaction_type": "income",
            "amount": 500,
            "description": "Начисление",
            "transaction_date": "2026-05-25",
        },
    )
    assert transaction_response.status_code == 200
    payload = transaction_response.json()
    assert len(payload["transactions"]) == 1
    updated_account = next(item for item in payload["accounts"] if item["account_id"] == account["account_id"])
    assert float(updated_account["balance"]) == 1500


def test_finance_transaction_requires_owned_account_and_category(registered_client):
    response = registered_client.post(
        "/api/finance/transactions",
        json={
            "account_id": 999999,
            "category_id": 999999,
            "transaction_type": "expense",
            "amount": 100,
            "transaction_date": "2026-05-25",
        },
    )

    assert response.status_code == 400
