def test_planner_task_event_note_flow(registered_client):
    category_response = registered_client.post(
        "/api/planner/categories",
        json={"category_name": "Учебные тесты"},
    )
    assert category_response.status_code == 200
    category = next(item for item in category_response.json()["categories"] if item["category_name"] == "Учебные тесты")

    task_response = registered_client.post(
        "/api/planner/tasks",
        json={
            "planner_category_id": category["planner_category_id"],
            "title": "Подготовить отчет",
            "description": "Проверить backend API",
            "priority": "high",
            "status": "planned",
            "due_date": "2026-05-25",
        },
    )
    assert task_response.status_code == 200
    task = task_response.json()["tasks"][0]
    assert task["title"] == "Подготовить отчет"
    assert task["priority"] == "high"
    assert task_response.json()["stats"]["activeTasks"] == 1

    event_response = registered_client.post(
        "/api/planner/events",
        json={
            "planner_category_id": category["planner_category_id"],
            "title": "Консультация",
            "event_date": "2026-05-25",
            "start_time": "12:00",
            "end_time": "13:00",
            "location": "Аудитория 425",
        },
    )
    assert event_response.status_code == 200
    assert event_response.json()["events"][0]["title"] == "Консультация"

    note_response = registered_client.post(
        "/api/planner/notes",
        json={"title": "Идея", "content": "Добавить проверку расписания"},
    )
    assert note_response.status_code == 200
    assert note_response.json()["stats"]["notes"] == 1

    done_response = registered_client.patch(
        f"/api/planner/tasks/{task['task_id']}",
        json={"status": "done"},
    )
    assert done_response.status_code == 200
    assert done_response.json()["stats"]["doneTasks"] == 1


def test_planner_rejects_invalid_event_time(registered_client):
    response = registered_client.post(
        "/api/planner/events",
        json={
            "title": "Некорректное событие",
            "event_date": "2026-05-25",
            "start_time": "14:00",
            "end_time": "13:00",
        },
    )

    assert response.status_code == 400


def test_workout_plan_log_and_exercise_flow(registered_client):
    metadata = registered_client.get("/api/workouts").json()
    workout_type = metadata["types"][0]
    exercise = metadata["exercises"][0]

    plan_response = registered_client.post(
        "/api/workouts/plans",
        json={
            "plan_name": "Проверочная тренировка",
            "workout_type_id": workout_type["workout_type_id"],
            "day_number": 1,
            "description": "План для backend-теста",
        },
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()["plans"][0]
    assert plan["plan_name"] == "Проверочная тренировка"

    plan_exercise_response = registered_client.post(
        "/api/workouts/plan-exercises",
        json={
            "plan_id": plan["plan_id"],
            "exercise_id": exercise["exercise_id"],
            "sets_count": 3,
            "reps_count": 12,
            "duration_minutes": 20,
        },
    )
    assert plan_exercise_response.status_code == 200
    assert plan_exercise_response.json()["plans"][0]["exercise_count"] == 1

    log_response = registered_client.post(
        "/api/workouts/logs",
        json={
            "plan_id": plan["plan_id"],
            "workout_date": "2026-05-25",
            "duration_minutes": 35,
            "calories_burned": 210,
            "notes": "Выполнено",
        },
    )
    assert log_response.status_code == 200
    log = log_response.json()["logs"][0]
    assert log["plan_id"] == plan["plan_id"]

    log_exercise_response = registered_client.post(
        "/api/workouts/log-exercises",
        json={
            "workout_log_id": log["workout_log_id"],
            "exercise_id": exercise["exercise_id"],
            "sets_done": 3,
            "reps_done": 12,
            "weight_used": 10,
            "duration_minutes": 20,
        },
    )
    assert log_exercise_response.status_code == 200
    assert log_exercise_response.json()["stats"]["totalLogs"] == 1
    assert log_exercise_response.json()["logExercises"][0]["sets_done"] == 3


def test_workout_plan_requires_existing_type(registered_client):
    response = registered_client.post(
        "/api/workouts/plans",
        json={
            "plan_name": "Невозможный план",
            "workout_type_id": 999999,
            "day_number": 1,
        },
    )

    assert response.status_code == 400
