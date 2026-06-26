from __future__ import annotations

import asyncio
import os
import re
import threading
import uuid
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from . import database as db
from .tusur_timetable import FACULTIES, TusurTimetableClient


SESSION_COOKIE = "multiapp_sid"
ROOT_DIR = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT_DIR / "public"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{0,32}$")
APP_TZ = ZoneInfo(os.environ.get("MULTIAPP_TIMEZONE", "Asia/Tomsk"))
SYNC_PROGRESS_JOBS: dict[str, dict[str, Any]] = {}
SYNC_PROGRESS_LOCK = threading.Lock()

db.init_db()

app = FastAPI(
    title="MultiApp API",
    description="FastAPI backend для дипломного проекта MultiApp для студентов ТУСУР.",
    version="1.0.0",
)


async def schedule_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_scheduled_sync_if_due)
        except Exception as exc:
            print(f"Schedule auto sync failed: {exc}")
        await asyncio.sleep(60 * 60)


@app.on_event("startup")
async def start_schedule_sync_loop() -> None:
    asyncio.create_task(schedule_sync_loop())


def set_session_cookie(response: Response, session: dict[str, str]) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session["session_id"],
        httponly=True,
        samesite="lax",
        expires=session["expires_at"],
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user_or_none(request: Request) -> dict[str, Any] | None:
    return db.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> dict[str, Any]:
    user = current_user_or_none(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return user


def clean_text(value: Any, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if max_length is not None:
        text = text[:max_length]
    return text or None


def validate_email(email: str) -> str:
    normalized = db.normalize_email(email)
    if not normalized or not EMAIL_RE.match(normalized):
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    return normalized


def validate_full_name(full_name: str) -> str:
    cleaned = clean_text(full_name, 120)
    if not cleaned or len(cleaned.split()) < 2:
        raise HTTPException(status_code=400, detail="Укажите имя и фамилию")
    return cleaned


def validate_phone(phone: Any) -> str | None:
    cleaned = clean_text(phone, 32)
    if cleaned and not PHONE_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Телефон может содержать только цифры, пробелы, +, -, скобки")
    return cleaned


def validate_group_id(conn, group_id: Any) -> int | None:
    if group_id in (None, ""):
        return None
    try:
        normalized = int(group_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная группа")
    group = conn.execute("SELECT group_id FROM Groups WHERE group_id = :group_id", {"group_id": normalized}).fetchone()
    if not group:
        raise HTTPException(status_code=400, detail="Выбранная группа не найдена")
    return normalized


def validate_choice(value: Any, allowed: set[str], field_label: str) -> str:
    normalized = clean_text(value, 40)
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail=f"Некорректное значение поля: {field_label}")
    return normalized


def validate_money(value: Any, field_label: str, *, positive: bool = True) -> float:
    try:
        amount = round(float(str(value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Укажите корректную сумму: {field_label}")
    if positive and amount <= 0:
        raise HTTPException(status_code=400, detail=f"Сумма должна быть больше нуля: {field_label}")
    return amount


def validate_iso_date(value: Any, field_label: str) -> str:
    raw = clean_text(value, 10)
    if not raw:
        raise HTTPException(status_code=400, detail=f"Укажите дату: {field_label}")
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Некорректная дата: {field_label}")
    return raw


def payload_value(payload: dict[str, Any], key: str, default: Any = None) -> Any:
    value = payload.get(key, default)
    return None if value == "" else value


def pick(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: payload_value(payload, field) for field in fields if field in payload}


def insert_row(conn, table: str, data: dict[str, Any]) -> int:
    fields = [field for field, value in data.items() if value is not None or field == "user_id"]
    columns = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join(f":{field}" for field in fields)
    return conn.execute(
        f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
        {field: data[field] for field in fields},
    ).lastrowid


def update_row(
    conn,
    table: str,
    id_field: str,
    item_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
) -> None:
    fields = list(data)
    if not fields:
        return
    assignments = ", ".join(f'"{field}" = :{field}' for field in fields)
    params = {**data, "item_id": item_id}
    user_filter = ""
    if user_id is not None:
        user_filter = " AND user_id = :user_id"
        params["user_id"] = user_id
    conn.execute(
        f'UPDATE "{table}" SET {assignments} WHERE "{id_field}" = :item_id{user_filter}',
        params,
    )


def get_owned_row(conn, table: str, id_field: str, item_id: int, user_id: int) -> dict[str, Any] | None:
    return conn.execute(
        f'SELECT * FROM "{table}" WHERE "{id_field}" = :item_id AND user_id = :user_id',
        {"item_id": item_id, "user_id": user_id},
    ).fetchone()


def delete_row(conn, table: str, id_field: str, item_id: int, user_id: int | None = None) -> None:
    params = {"item_id": item_id}
    user_filter = ""
    if user_id is not None:
        user_filter = " AND user_id = :user_id"
        params["user_id"] = user_id
    conn.execute(f'DELETE FROM "{table}" WHERE "{id_field}" = :item_id{user_filter}', params)


def today(offset: int = 0) -> str:
    return (datetime.now(APP_TZ).date() + timedelta(days=offset)).isoformat()


def weekday_number(offset: int = 0) -> int:
    return (datetime.now(APP_TZ).date() + timedelta(days=offset)).isoweekday()


def month_start() -> str:
    return f"{date.today():%Y-%m}-01"


def normalize_week_for_settings(week_type: str | None) -> str:
    if week_type in ("четная", "числитель"):
        return "числитель"
    if week_type in ("нечетная", "знаменатель"):
        return "знаменатель"
    return "обычная"


def normalize_week_for_schedule(week_type: str | None) -> str:
    if week_type == "числитель":
        return "четная"
    if week_type == "знаменатель":
        return "нечетная"
    return week_type or "четная"


def serialize_date(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def parse_schedule_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def monday_for_date(value: date | None = None) -> date:
    target = value or datetime.now(APP_TZ).date()
    return target - timedelta(days=target.weekday())


def fallback_week_start(offset: int = 0) -> date:
    return monday_for_date() + timedelta(days=offset * 7)


def upsert_schedule_week(conn, week: dict[str, Any], fallback_starts_at: date | None = None) -> dict[str, Any]:
    starts_at = parse_schedule_date(week.get("starts_at")) or fallback_starts_at or monday_for_date()
    ends_at = starts_at + timedelta(days=6)
    week_type = week.get("week_type") or "обычная"
    params = {
        "source_week_id": week.get("week_id"),
        "week_number": week.get("week_number"),
        "week_type": week_type,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "synced_at": datetime.now(APP_TZ).isoformat(timespec="seconds"),
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO Schedule_Weeks
            (source_week_id, week_number, week_type, starts_at, ends_at, synced_at)
        VALUES
            (:source_week_id, :week_number, :week_type, :starts_at, :ends_at, :synced_at)
        """,
        params,
    )
    if params["source_week_id"] is not None:
        week_row = conn.execute(
            "SELECT * FROM Schedule_Weeks WHERE source_week_id = :source_week_id",
            {"source_week_id": params["source_week_id"]},
        ).fetchone()
        if not week_row:
            week_row = conn.execute(
                """
                SELECT *
                FROM Schedule_Weeks
                WHERE starts_at = :starts_at AND week_type = :week_type
                """,
                params,
            ).fetchone()
            if week_row:
                conn.execute(
                    """
                    UPDATE Schedule_Weeks
                    SET source_week_id = COALESCE(source_week_id, :source_week_id)
                    WHERE schedule_week_id = :schedule_week_id
                    """,
                    {"source_week_id": params["source_week_id"], "schedule_week_id": week_row["schedule_week_id"]},
                )
                week_row = conn.execute(
                    "SELECT * FROM Schedule_Weeks WHERE schedule_week_id = :schedule_week_id",
                    {"schedule_week_id": week_row["schedule_week_id"]},
                ).fetchone()
    else:
        week_row = conn.execute(
            """
            SELECT *
            FROM Schedule_Weeks
            WHERE starts_at = :starts_at AND week_type = :week_type
            """,
            params,
        ).fetchone()
    conn.execute(
        """
        UPDATE Schedule_Weeks
        SET week_number = COALESCE(:week_number, week_number),
            ends_at = :ends_at,
            synced_at = :synced_at
        WHERE schedule_week_id = :schedule_week_id
        """,
        {**params, "schedule_week_id": week_row["schedule_week_id"]},
    )
    return {
        **dict(week_row),
        "week_type": week_type,
        "starts_at": params["starts_at"],
        "ends_at": params["ends_at"],
    }


def schedule_metadata(conn=None) -> dict[str, Any]:
    return {
        "faculties": db.fetch_all(
            """
            SELECT faculty_id, full_name, abbreviation, site_code
            FROM Faculties
            ORDER BY full_name
            """,
            conn=conn,
        ),
        "courses": db.fetch_all(
            "SELECT course_id, course_number FROM Courses ORDER BY course_number",
            conn=conn,
        ),
        "groups": db.fetch_all(
            """
            SELECT g.group_id, g.group_name, g.faculty_id, g.course_id,
                   c.course_number, f.full_name AS faculty_name, f.site_code AS faculty_code
            FROM Groups g
            JOIN Courses c ON c.course_id = g.course_id
            JOIN Faculties f ON f.faculty_id = g.faculty_id
            ORDER BY f.full_name, c.course_number, g.group_name
            """,
            conn=conn,
        ),
    }


def profile_payload(user_id: int, conn=None) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT sp.*, g.group_name, g.faculty_id, g.course_id,
               c.course_number, f.full_name AS faculty_name, f.site_code AS faculty_code
        FROM Student_Profile sp
        LEFT JOIN Groups g ON g.group_id = sp.group_id
        LEFT JOIN Courses c ON c.course_id = g.course_id
        LEFT JOIN Faculties f ON f.faculty_id = g.faculty_id
        WHERE sp.user_id = :user_id
        ORDER BY sp.profile_id DESC
        LIMIT 1
        """,
        {"user_id": user_id},
        conn,
    )


def settings_payload(user_id: int, conn=None) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT s.*, g.group_name, f.full_name AS faculty_name, f.site_code AS faculty_code
        FROM App_Settings s
        LEFT JOIN Groups g ON g.group_id = s.selected_group_id
        LEFT JOIN Faculties f ON f.faculty_id = g.faculty_id
        WHERE s.user_id = :user_id
        ORDER BY s.setting_id DESC
        LIMIT 1
        """,
        {"user_id": user_id},
        conn,
    )


def schedule_payload(
    user_id: int,
    group_id: int | None = None,
    week_type: str | None = None,
    week_start: str | date | None = None,
    conn=None,
) -> dict[str, Any]:
    settings = settings_payload(user_id, conn)
    profile = profile_payload(user_id, conn)
    selected_group_id = int(group_id or (profile or {}).get("group_id") or (settings or {}).get("selected_group_id") or 0)
    selected_week_type = normalize_week_for_schedule(week_type or (settings or {}).get("selected_week_type"))
    current_date = today()
    selected_week_start = monday_for_date(parse_schedule_date(week_start) or datetime.now(APP_TZ).date())
    selected_week_end = selected_week_start + timedelta(days=6)
    target_date = selected_week_start.isoformat()

    group = None
    if selected_group_id:
        group = db.fetch_one(
            """
            SELECT g.*, c.course_number, f.full_name AS faculty_name, f.site_code AS faculty_code
            FROM Groups g
            JOIN Courses c ON c.course_id = g.course_id
            JOIN Faculties f ON f.faculty_id = g.faculty_id
            WHERE g.group_id = :group_id
            """,
            {"group_id": selected_group_id},
            conn,
        )

    weekly = None
    if group:
        weekly = db.fetch_one(
            """
            SELECT ws.*, sw.schedule_week_id,
                   COALESCE(sw.starts_at, ws.starts_at) AS starts_at,
                   COALESCE(sw.ends_at, ws.starts_at) AS ends_at
            FROM Weekly_Schedule ws
            LEFT JOIN Schedule_Weeks sw ON sw.schedule_week_id = ws.schedule_week_id
            WHERE ws.group_id = :group_id
              AND ws.week_type = :week_type
              AND COALESCE(sw.starts_at, ws.starts_at) IS NOT NULL
              AND :target_date BETWEEN substr(COALESCE(sw.starts_at, ws.starts_at), 1, 10)
                  AND substr(COALESCE(sw.ends_at, ws.starts_at), 1, 10)
            ORDER BY ws.schedule_id DESC
            LIMIT 1
            """,
            {"group_id": group["group_id"], "week_type": selected_week_type, "target_date": target_date},
            conn,
        ) or db.fetch_one(
            """
            SELECT ws.*, sw.schedule_week_id,
                   COALESCE(sw.starts_at, ws.starts_at) AS starts_at,
                   COALESCE(sw.ends_at, ws.starts_at) AS ends_at
            FROM Weekly_Schedule ws
            LEFT JOIN Schedule_Weeks sw ON sw.schedule_week_id = ws.schedule_week_id
            WHERE ws.group_id = :group_id
              AND COALESCE(sw.starts_at, ws.starts_at) IS NOT NULL
              AND :target_date BETWEEN substr(COALESCE(sw.starts_at, ws.starts_at), 1, 10)
                  AND substr(COALESCE(sw.ends_at, ws.starts_at), 1, 10)
            ORDER BY ws.schedule_id DESC
            LIMIT 1
            """,
            {"group_id": group["group_id"], "target_date": target_date},
            conn,
        )

    lessons = []
    if weekly:
        lessons = db.fetch_all(
            """
            SELECT *
            FROM Daily_Schedule
            WHERE schedule_id = :schedule_id
            ORDER BY day_number, lesson_number, start_time
            """,
            {"schedule_id": weekly["schedule_id"]},
            conn,
        )

    available_weeks = []
    if group:
        available_weeks = db.fetch_all(
            """
            SELECT COALESCE(sw.starts_at, ws.starts_at) AS starts_at,
                   COALESCE(sw.ends_at, ws.starts_at) AS ends_at,
                   ws.week_type, ws.week_number, ws.source_week_id,
                   COUNT(ds.daily_schedule_id) AS lesson_count
            FROM Weekly_Schedule ws
            LEFT JOIN Schedule_Weeks sw ON sw.schedule_week_id = ws.schedule_week_id
            LEFT JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
            WHERE ws.group_id = :group_id
              AND COALESCE(sw.starts_at, ws.starts_at) IS NOT NULL
            GROUP BY ws.schedule_id, sw.starts_at, sw.ends_at, ws.starts_at,
                     ws.week_type, ws.week_number, ws.source_week_id
            ORDER BY COALESCE(sw.starts_at, ws.starts_at)
            """,
            {"group_id": group["group_id"]},
            conn,
        )

    selected_week = weekly or {
        "starts_at": selected_week_start.isoformat(),
        "ends_at": selected_week_end.isoformat(),
        "week_type": selected_week_type,
    }

    return {
        "group": group,
        "week": selected_week,
        "lessons": lessons,
        "availableWeeks": available_weeks,
        "navigation": {
            "current": fallback_week_start().isoformat(),
            "selected": selected_week_start.isoformat(),
            "previous": (selected_week_start - timedelta(days=7)).isoformat(),
            "next": (selected_week_start + timedelta(days=7)).isoformat(),
        },
        "dates": {"today": current_date},
        "message": None if weekly else "На выбранную неделю расписание еще не загружено администратором.",
        "metadata": schedule_metadata(conn),
    }


def save_parsed_schedule(
    conn,
    faculty: str,
    group_name: str,
    week: dict[str, Any],
    lessons: list[dict[str, Any]],
    allow_empty: bool = False,
    fallback_starts_at: date | None = None,
) -> dict[str, Any]:
    week_type = week.get("week_type") or "обычная"
    existing_group = conn.execute(
        """
        SELECT g.*, c.course_number, f.full_name AS faculty_name
        FROM Groups g
        JOIN Courses c ON c.course_id = g.course_id
        JOIN Faculties f ON f.faculty_id = g.faculty_id
        WHERE g.group_name = :group_name
        LIMIT 1
        """,
        {"group_name": group_name},
    ).fetchone()
    group_id = db.upsert_group(
        conn,
        faculty,
        (existing_group or {}).get("faculty_name") or FACULTIES.get(faculty, faculty),
        int((existing_group or {}).get("course_number") or 1),
        group_name,
    )

    if not lessons and not allow_empty:
        return {"group_id": group_id, "schedule_id": None, "saved_lessons": 0, "skipped_empty": True}

    week_row = upsert_schedule_week(conn, week, fallback_starts_at)
    starts_at = week_row["starts_at"]
    ends_at = week_row["ends_at"]
    schedule_week_id = week_row["schedule_week_id"]

    if schedule_week_id:
        old_schedules = conn.execute(
            """
            SELECT schedule_id
            FROM Weekly_Schedule
            WHERE group_id = :group_id AND schedule_week_id = :schedule_week_id
            """,
            {"group_id": group_id, "schedule_week_id": schedule_week_id},
        ).fetchall()
    else:
        old_schedules = conn.execute(
            """
            SELECT schedule_id
            FROM Weekly_Schedule
            WHERE group_id = :group_id
              AND week_type = :week_type
              AND starts_at = :starts_at
            """,
            {"group_id": group_id, "week_type": week_type, "starts_at": starts_at},
        ).fetchall()
    for schedule in old_schedules:
        conn.execute("DELETE FROM Daily_Schedule WHERE schedule_id = :schedule_id", {"schedule_id": schedule["schedule_id"]})
        conn.execute("DELETE FROM Weekly_Schedule WHERE schedule_id = :schedule_id", {"schedule_id": schedule["schedule_id"]})

    schedule_id = conn.execute(
        """
        INSERT INTO Weekly_Schedule
            (group_id, week_type, source_week_id, week_number, starts_at, synced_at, schedule_week_id)
        VALUES
            (:group_id, :week_type, :source_week_id, :week_number, :starts_at, :synced_at, :schedule_week_id)
        """,
        {
            "group_id": group_id,
            "week_type": week_type,
            "source_week_id": week.get("week_id"),
            "week_number": week.get("week_number"),
            "starts_at": starts_at,
            "synced_at": datetime.now(APP_TZ).isoformat(timespec="seconds"),
            "schedule_week_id": schedule_week_id,
        },
    ).lastrowid

    for lesson in lessons:
        conn.execute(
            """
            INSERT INTO Daily_Schedule
                (schedule_id, lesson_number, day_number, discipline, lesson_type,
                 auditorium, teacher_name, start_time, end_time)
            VALUES
                (:schedule_id, :lesson_number, :day_number, :discipline, :lesson_type,
                 :auditorium, :teacher_name, :start_time, :end_time)
            """,
            {
                "schedule_id": schedule_id,
                "lesson_number": lesson["lesson_number"],
                "day_number": lesson["day_number"],
                "discipline": lesson["discipline"],
                "lesson_type": lesson["lesson_type"],
                "auditorium": lesson.get("auditorium"),
                "teacher_name": lesson.get("teacher_name"),
                "start_time": lesson.get("start_time"),
                "end_time": lesson.get("end_time"),
            },
        )

    return {
        "group_id": group_id,
        "schedule_id": schedule_id,
        "schedule_week_id": schedule_week_id,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "saved_lessons": len(lessons),
        "skipped_empty": False,
    }


def schedule_sync_settings(conn=None) -> dict[str, Any]:
    settings = db.fetch_one(
        "SELECT * FROM Schedule_Sync_Settings WHERE setting_id = 1",
        conn=conn,
    )
    if settings:
        return settings
    if conn is None:
        with db.transaction() as local_conn:
            local_conn.execute(
                """
                INSERT OR IGNORE INTO Schedule_Sync_Settings
                    (setting_id, enabled, lead_days, run_time, sync_mode)
                VALUES
                    (1, 1, 2, '18:00', 'next')
                """
            )
        return schedule_sync_settings()
    conn.execute(
        """
        INSERT OR IGNORE INTO Schedule_Sync_Settings
            (setting_id, enabled, lead_days, run_time, sync_mode)
        VALUES
            (1, 1, 2, '18:00', 'next')
        """
    )
    return conn.execute("SELECT * FROM Schedule_Sync_Settings WHERE setting_id = 1").fetchone()


def schedule_sync_logs(limit: int = 8, conn=None) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT *
        FROM Schedule_Sync_Log
        ORDER BY started_at DESC, log_id DESC
        LIMIT :limit
        """,
        {"limit": limit},
        conn,
    )


def create_sync_log(trigger_type: str, target_scope: str, target_week: str, total_groups: int = 0) -> int:
    return db.execute(
        """
        INSERT INTO Schedule_Sync_Log
            (started_at, trigger_type, target_scope, target_week, total_groups)
        VALUES
            (:started_at, :trigger_type, :target_scope, :target_week, :total_groups)
        """,
        {
            "started_at": datetime.now(APP_TZ).isoformat(timespec="seconds"),
            "trigger_type": trigger_type,
            "target_scope": target_scope,
            "target_week": target_week,
            "total_groups": total_groups,
        },
    ).lastrowid


def finish_sync_log(log_id: int, status: str, result: dict[str, Any], message: str = "") -> None:
    db.execute(
        """
        UPDATE Schedule_Sync_Log
        SET finished_at = :finished_at,
            status = :status,
            total_groups = :total_groups,
            synced_groups = :synced_groups,
            lesson_count = :lesson_count,
            empty_groups = :empty_groups,
            error_count = :error_count,
            message = :message
        WHERE log_id = :log_id
        """,
        {
            "finished_at": datetime.now(APP_TZ).isoformat(timespec="seconds"),
            "status": status,
            "total_groups": result.get("total_groups", 0),
            "synced_groups": result.get("synced_groups", 0),
            "lesson_count": result.get("lesson_count", 0),
            "empty_groups": result.get("empty_groups", 0),
            "error_count": result.get("error_count", 0),
            "message": message[:2000] if message else "",
            "log_id": log_id,
        },
    )


def progress_percent(job: dict[str, Any]) -> int:
    total = int(job.get("total") or 0)
    processed = int(job.get("processed") or 0)
    if job.get("done"):
        return 100
    if total <= 0:
        return 0
    return max(1, min(99, round(processed / total * 100)))


def update_sync_progress(job_id: str, **changes: Any) -> None:
    with SYNC_PROGRESS_LOCK:
        job = SYNC_PROGRESS_JOBS.get(job_id)
        if not job:
            return
        job.update({key: value for key, value in changes.items() if value is not None})
        job["percent"] = progress_percent(job)
        job["updated_at"] = datetime.now(APP_TZ).isoformat(timespec="seconds")


def sync_progress_snapshot(job_id: str) -> dict[str, Any]:
    with SYNC_PROGRESS_LOCK:
        job = SYNC_PROGRESS_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задача синхронизации не найдена")
        return dict(job)


def start_sync_progress_job(title: str, runner) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    now = datetime.now(APP_TZ).isoformat(timespec="seconds")
    with SYNC_PROGRESS_LOCK:
        SYNC_PROGRESS_JOBS[job_id] = {
            "job_id": job_id,
            "title": title,
            "status": "running",
            "done": False,
            "processed": 0,
            "total": 0,
            "percent": 0,
            "message": "Подготовка синхронизации",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": "",
        }

    def run() -> None:
        try:
            result = runner(lambda **changes: update_sync_progress(job_id, **changes))
            update_sync_progress(
                job_id,
                status="success" if result.get("ok", True) else "failed",
                done=True,
                processed=result.get("total_groups") or SYNC_PROGRESS_JOBS[job_id].get("total") or 1,
                total=result.get("total_groups") or SYNC_PROGRESS_JOBS[job_id].get("total") or 1,
                message=result.get("message") or "Синхронизация завершена",
                result=result,
            )
        except Exception as exc:
            update_sync_progress(
                job_id,
                status="failed",
                done=True,
                message="Синхронизация завершилась ошибкой",
                error=str(exc),
                result={"ok": False, "message": str(exc)},
            )

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "job_id": job_id, "progress": sync_progress_snapshot(job_id)}


def target_offsets(sync_mode: str) -> list[int]:
    if sync_mode == "current":
        return [0]
    if sync_mode == "current_next":
        return [0, 1]
    return [1]


def week_type_for_offset(current_week_type: str | None, offset: int) -> str:
    current = current_week_type or "обычная"
    if current == "обычная":
        return "обычная"
    if offset % 2 == 0:
        return current
    return "нечетная" if current == "четная" else "четная"


def week_start_for_offset(current_week: Any, offset: int) -> date:
    base_start = parse_schedule_date(getattr(current_week, "starts_at", None))
    if base_start:
        return base_start + timedelta(days=offset * 7)
    return fallback_week_start(offset)


def complete_week_info(week: dict[str, Any], current_week: Any, offset: int) -> dict[str, Any]:
    completed = {**week}
    if offset != 0 and getattr(current_week, "week_id", None) is not None:
        completed["week_id"] = current_week.week_id + offset
    elif completed.get("week_id") is None and getattr(current_week, "week_id", None) is not None:
        completed["week_id"] = current_week.week_id + offset
    if offset != 0 and getattr(current_week, "week_number", None) is not None:
        completed["week_number"] = current_week.week_number + offset
    elif completed.get("week_number") is None and getattr(current_week, "week_number", None) is not None:
        completed["week_number"] = current_week.week_number + offset
    if offset != 0:
        completed["week_type"] = week_type_for_offset(getattr(current_week, "week_type", None), offset)
    elif not completed.get("week_type"):
        completed["week_type"] = week_type_for_offset(getattr(current_week, "week_type", None), offset)
    if offset != 0:
        completed["starts_at"] = week_start_for_offset(current_week, offset)
    elif not completed.get("starts_at"):
        completed["starts_at"] = week_start_for_offset(current_week, offset)
    return completed


def ensure_future_week_ids(conn, current_week: Any, weeks_ahead: int = 3) -> list[dict[str, Any]]:
    if current_week is None or getattr(current_week, "week_id", None) is None:
        return []
    rows = []
    for offset in range(0, weeks_ahead + 1):
        week = complete_week_info(
            {"week_id": None, "week_number": None, "week_type": None, "starts_at": None},
            current_week,
            offset,
        )
        rows.append(upsert_schedule_week(conn, week, week_start_for_offset(current_week, offset)))
    return rows


def schedule_for_offset(client: TusurTimetableClient, faculty: str, group_name: str, offset: int, current_week=None):
    if offset == 0:
        return client.fetch_schedule(faculty, group_name)
    current_week = current_week or client.fetch_current_week(faculty, group_name)
    week_id = current_week.week_id + offset if current_week.week_id is not None else None
    return client.fetch_schedule(faculty, group_name, week_id)


def group_rows_for_sync(faculty: str | None = None, course_number: int | None = None, group_name: str | None = None) -> list[dict[str, Any]]:
    filters = []
    params: dict[str, Any] = {}
    if faculty:
        filters.append("f.site_code = :faculty")
        params["faculty"] = faculty
    if course_number:
        filters.append("c.course_number = :course_number")
        params["course_number"] = course_number
    if group_name:
        filters.append("g.group_name = :group_name")
        params["group_name"] = group_name
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    return db.fetch_all(
        f"""
        SELECT g.group_id, g.group_name, f.site_code AS faculty_code,
               f.full_name AS faculty_name, c.course_number
        FROM Groups g
        JOIN Faculties f ON f.faculty_id = g.faculty_id
        JOIN Courses c ON c.course_id = g.course_id
        {where}
        ORDER BY f.full_name, c.course_number, g.group_name
        """,
        params,
    )


def sync_schedule_groups(
    *,
    trigger_type: str = "manual",
    faculty: str | None = None,
    course_number: int | None = None,
    group_name: str | None = None,
    sync_mode: str = "next",
    max_groups: int | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    groups = group_rows_for_sync(faculty, course_number, group_name)
    if max_groups:
        groups = groups[: max(0, int(max_groups))]
    offsets = target_offsets(sync_mode)
    total_targets = len(groups) * len(offsets)
    if progress_callback:
        progress_callback(total=total_targets, processed=0, message="Подготовлено групп к синхронизации: " + str(total_targets))
    log_id = create_sync_log(
        trigger_type=trigger_type,
        target_scope=group_name or faculty or "all",
        target_week=sync_mode,
        total_groups=total_targets,
    )
    client = TusurTimetableClient(timeout=25)
    result = {
        "ok": True,
        "log_id": log_id,
        "total_groups": total_targets,
        "synced_groups": 0,
        "lesson_count": 0,
        "empty_groups": 0,
        "error_count": 0,
        "status": "running",
        "errors": [],
        "items": [],
    }
    processed_targets = 0

    try:
        for group in groups:
            current_week = None
            try:
                current_week = client.fetch_current_week(group["faculty_code"], group["group_name"])
            except Exception:
                current_week = None
            if current_week is not None:
                with db.transaction() as conn:
                    ensure_future_week_ids(conn, current_week)
            for offset in offsets:
                try:
                    if progress_callback:
                        progress_callback(message=f"Обновляем {group['group_name']}")
                    parsed = schedule_for_offset(client, group["faculty_code"], group["group_name"], offset, current_week)
                    week = complete_week_info(asdict(parsed.week), current_week, offset)
                    lessons = [asdict(lesson) for lesson in parsed.lessons]
                    with db.transaction() as conn:
                        save_result = save_parsed_schedule(
                            conn,
                            group["faculty_code"],
                            group["group_name"],
                            week,
                            lessons,
                            allow_empty=False,
                            fallback_starts_at=fallback_week_start(offset),
                        )
                    if save_result["skipped_empty"]:
                        result["empty_groups"] += 1
                    else:
                        result["synced_groups"] += 1
                        result["lesson_count"] += len(lessons)
                    result["items"].append(
                        {
                            "faculty": group["faculty_code"],
                            "group": group["group_name"],
                            "offset": offset,
                            "week": week,
                            "lessons": len(lessons),
                            "saved": not save_result["skipped_empty"],
                        }
                    )
                except Exception as exc:
                    result["error_count"] += 1
                    result["errors"].append(
                        {
                            "faculty": group["faculty_code"],
                            "group": group["group_name"],
                            "offset": offset,
                            "error": str(exc),
                        }
                    )
                finally:
                    processed_targets += 1
                    if progress_callback:
                        progress_callback(
                            processed=processed_targets,
                            total=total_targets,
                            message=f"Обработано {processed_targets} из {total_targets}",
                        )
        result["ok"] = result["error_count"] == 0
        status = "success" if result["ok"] else ("partial" if result["synced_groups"] else "failed")
        result["status"] = status
        finish_sync_log(log_id, status, result, "; ".join(error["error"] for error in result["errors"][:5]))
        return result
    except Exception as exc:
        result["ok"] = False
        result["error_count"] += 1
        result["status"] = "failed"
        finish_sync_log(log_id, "failed", result, str(exc))
        raise


def next_monday(value: date) -> date:
    days_ahead = (7 - value.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return value + timedelta(days=days_ahead)


def scheduled_run_key(settings: dict[str, Any], now: datetime | None = None) -> tuple[str, bool]:
    now = now or datetime.now(APP_TZ)
    monday = next_monday(now.date())
    run_date = monday - timedelta(days=int(settings.get("lead_days") or 0))
    run_time_raw = settings.get("run_time") or "18:00"
    try:
        hour, minute = [int(part) for part in run_time_raw.split(":", 1)]
    except Exception:
        hour, minute = 18, 0
    run_at = datetime.combine(run_date, time(hour=hour, minute=minute), tzinfo=APP_TZ)
    should_run = now >= run_at
    return f"{monday.isoformat()}:{settings.get('sync_mode') or 'next'}", should_run


def run_scheduled_sync_if_due(force: bool = False, progress_callback=None) -> dict[str, Any]:
    settings = schedule_sync_settings()
    if not settings.get("enabled") and not force:
        return {"ok": True, "skipped": True, "reason": "Автообновление выключено"}
    run_key, should_run = scheduled_run_key(settings)
    if not force and (not should_run or settings.get("last_run_key") == run_key):
        return {"ok": True, "skipped": True, "run_key": run_key}

    result = sync_schedule_groups(
        trigger_type="auto" if not force else "manual-auto",
        sync_mode=settings.get("sync_mode") or "next",
        progress_callback=progress_callback,
    )
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE Schedule_Sync_Settings
            SET last_run_key = :run_key,
                updated_at = CURRENT_TIMESTAMP
            WHERE setting_id = 1
            """,
            {"run_key": run_key},
        )
    result["run_key"] = run_key
    return result


def admin_schedule_payload(conn=None) -> dict[str, Any]:
    groups = db.fetch_all(
        """
        SELECT g.group_id, g.group_name, g.faculty_id, g.course_id,
               c.course_number, f.full_name AS faculty_name, f.site_code AS faculty_code,
               COUNT(DISTINCT ws.schedule_id) AS schedule_count,
               COUNT(ds.daily_schedule_id) AS lesson_count,
               MAX(ws.schedule_id) AS last_schedule_id,
               MAX(ws.synced_at) AS last_synced_at
        FROM Groups g
        JOIN Courses c ON c.course_id = g.course_id
        JOIN Faculties f ON f.faculty_id = g.faculty_id
        LEFT JOIN Weekly_Schedule ws ON ws.group_id = g.group_id
        LEFT JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
        GROUP BY g.group_id, g.group_name, g.faculty_id, g.course_id,
                 c.course_number, f.full_name, f.site_code
        ORDER BY f.full_name, c.course_number, g.group_name
        """,
        conn=conn,
    )
    schedules = db.fetch_all(
        """
        SELECT ws.schedule_id, ws.group_id, ws.week_type, g.group_name,
               f.full_name AS faculty_name, f.site_code AS faculty_code,
               c.course_number, ws.week_number,
               COALESCE(sw.starts_at, ws.starts_at) AS starts_at,
               COALESCE(sw.ends_at, ws.starts_at) AS ends_at,
               ws.synced_at,
               COUNT(ds.daily_schedule_id) AS lesson_count
        FROM Weekly_Schedule ws
        JOIN Groups g ON g.group_id = ws.group_id
        JOIN Courses c ON c.course_id = g.course_id
        JOIN Faculties f ON f.faculty_id = g.faculty_id
        LEFT JOIN Schedule_Weeks sw ON sw.schedule_week_id = ws.schedule_week_id
        LEFT JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
        GROUP BY ws.schedule_id, ws.group_id, ws.week_type, g.group_name,
                 f.full_name, f.site_code, c.course_number, ws.week_number,
                 sw.starts_at, sw.ends_at, ws.starts_at, ws.synced_at
        ORDER BY ws.schedule_id DESC
        LIMIT 60
        """,
        conn=conn,
    )
    weeks = db.fetch_all(
        """
        SELECT sw.schedule_week_id, sw.source_week_id, sw.week_number, sw.week_type,
               sw.starts_at, sw.ends_at, sw.synced_at,
               COUNT(DISTINCT ws.group_id) AS group_count,
               COUNT(DISTINCT ws.schedule_id) AS schedule_count,
               COUNT(ds.daily_schedule_id) AS lesson_count
        FROM Schedule_Weeks sw
        LEFT JOIN Weekly_Schedule ws ON ws.schedule_week_id = sw.schedule_week_id
        LEFT JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
        GROUP BY sw.schedule_week_id, sw.source_week_id, sw.week_number,
                 sw.week_type, sw.starts_at, sw.ends_at, sw.synced_at
        ORDER BY sw.starts_at DESC, sw.schedule_week_id DESC
        LIMIT 20
        """,
        conn=conn,
    )
    current_week = db.fetch_one(
        """
        SELECT sw.schedule_week_id, sw.source_week_id, sw.week_number, sw.week_type,
               sw.starts_at, sw.ends_at, sw.synced_at,
               COUNT(DISTINCT ws.group_id) AS group_count,
               COUNT(ds.daily_schedule_id) AS lesson_count
        FROM Schedule_Weeks sw
        LEFT JOIN Weekly_Schedule ws ON ws.schedule_week_id = sw.schedule_week_id
        LEFT JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
        WHERE :current_date BETWEEN substr(sw.starts_at, 1, 10) AND substr(sw.ends_at, 1, 10)
        GROUP BY sw.schedule_week_id, sw.source_week_id, sw.week_number,
                 sw.week_type, sw.starts_at, sw.ends_at, sw.synced_at
        ORDER BY sw.source_week_id DESC, sw.schedule_week_id DESC
        LIMIT 1
        """,
        {"current_date": today()},
        conn=conn,
    )
    user_summary = db.fetch_one(
        """
        SELECT COUNT(*) AS total_users,
               SUM(CASE WHEN u.is_admin = 1 THEN 1 ELSE 0 END) AS admin_users,
               SUM(CASE WHEN u.is_admin = 0 THEN 1 ELSE 0 END) AS student_users
        FROM Users u
        """,
        conn=conn,
    )
    users_by_group = db.fetch_all(
        """
        SELECT f.full_name AS faculty_name, f.site_code AS faculty_code,
               g.group_name, c.course_number, COUNT(u.user_id) AS user_count
        FROM Users u
        JOIN Student_Profile sp ON sp.user_id = u.user_id
        LEFT JOIN Groups g ON g.group_id = sp.group_id
        LEFT JOIN Courses c ON c.course_id = g.course_id
        LEFT JOIN Faculties f ON f.faculty_id = g.faculty_id
        WHERE u.is_admin = 0
        GROUP BY g.group_id, f.full_name, f.site_code, g.group_name, c.course_number
        ORDER BY user_count DESC, f.full_name, g.group_name
        """,
        conn=conn,
    )
    users_by_faculty = db.fetch_all(
        """
        SELECT f.full_name AS faculty_name, f.site_code AS faculty_code,
               COUNT(u.user_id) AS user_count
        FROM Users u
        JOIN Student_Profile sp ON sp.user_id = u.user_id
        LEFT JOIN Groups g ON g.group_id = sp.group_id
        LEFT JOIN Faculties f ON f.faculty_id = g.faculty_id
        WHERE u.is_admin = 0
        GROUP BY f.faculty_id, f.full_name, f.site_code
        ORDER BY user_count DESC, f.full_name
        """,
        conn=conn,
    )
    groups_with_lessons = len([group for group in groups if group["lesson_count"]])
    groups_with_schedules = len([group for group in groups if group["schedule_count"]])
    return {
        **schedule_metadata(conn),
        "groups": groups,
        "schedules": schedules,
        "weeks": weeks,
        "currentWeek": current_week,
        "facultyCodes": FACULTIES,
        "summary": {
            **(user_summary or {}),
            "total_groups": len(groups),
            "groups_with_schedules": groups_with_schedules,
            "groups_with_lessons": groups_with_lessons,
            "coverage_percent": round(groups_with_schedules / max(len(groups), 1) * 100),
        },
        "syncSettings": schedule_sync_settings(conn),
        "syncLogs": schedule_sync_logs(conn=conn),
        "usersByGroup": users_by_group,
        "usersByFaculty": users_by_faculty,
    }


def finance_payload(user_id: int, conn=None) -> dict[str, Any]:
    accounts = db.fetch_all(
        """
        SELECT *
        FROM Accounts
        WHERE user_id = :user_id
        ORDER BY is_active DESC, account_name
        """,
        {"user_id": user_id},
        conn,
    )
    categories = db.fetch_all(
        """
        SELECT *
        FROM Categories
        WHERE user_id = :user_id
        ORDER BY category_type, category_name
        """,
        {"user_id": user_id},
        conn,
    )
    transactions = db.fetch_all(
        """
        SELECT t.*, a.account_name, c.category_name, c.color
        FROM Transactions t
        JOIN Accounts a ON a.account_id = t.account_id
        JOIN Categories c ON c.category_id = t.category_id
        WHERE t.user_id = :user_id
        ORDER BY t.transaction_date DESC, t.transaction_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    transfers = db.fetch_all(
        """
        SELECT tr.*, fa.account_name AS from_account_name, ta.account_name AS to_account_name
        FROM Transfers tr
        JOIN Accounts fa ON fa.account_id = tr.from_account_id
        JOIN Accounts ta ON ta.account_id = tr.to_account_id
        WHERE tr.user_id = :user_id
        ORDER BY tr.transfer_date DESC, tr.transfer_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    month_transactions = [item for item in transactions if item["transaction_date"] >= month_start()]
    expense_by_category: dict[str, float] = {}
    income_by_category: dict[str, float] = {}
    for item in month_transactions:
        target = income_by_category if item["transaction_type"] == "income" else expense_by_category
        target[item["category_name"]] = target.get(item["category_name"], 0) + float(item["amount"] or 0)
    recent_activity = sorted(
        [
            *[
                {
                    "kind": item["transaction_type"],
                    "date": item["transaction_date"],
                    "title": item["category_name"],
                    "account": item["account_name"],
                    "amount": float(item["amount"] or 0),
                    "description": item.get("description") or "",
                    "id": item["transaction_id"],
                }
                for item in transactions
            ],
            *[
                {
                    "kind": "transfer",
                    "date": item["transfer_date"],
                    "title": "Перевод",
                    "account": f"{item['from_account_name']} -> {item['to_account_name']}",
                    "amount": float(item["amount"] or 0),
                    "description": item.get("comment") or "",
                    "id": item["transfer_id"],
                }
                for item in transfers
            ],
        ],
        key=lambda item: (item["date"], item["id"]),
        reverse=True,
    )
    income_total = float(
        db.fetch_one(
            """
            SELECT COALESCE(SUM(amount), 0) AS value
            FROM Transactions
            WHERE user_id = :user_id
              AND transaction_type = 'income'
              AND transaction_date >= :month_start
            """,
            {"user_id": user_id, "month_start": month_start()},
            conn,
        )["value"]
    )
    expense_total = float(
        db.fetch_one(
            """
            SELECT COALESCE(SUM(amount), 0) AS value
            FROM Transactions
            WHERE user_id = :user_id
              AND transaction_type = 'expense'
              AND transaction_date >= :month_start
            """,
            {"user_id": user_id, "month_start": month_start()},
            conn,
        )["value"]
    )
    stats = {
        "balance": float(
            db.fetch_one(
                """
                SELECT COALESCE(SUM(balance), 0) AS value
                FROM Accounts
                WHERE user_id = :user_id AND is_active = 1
                """,
                {"user_id": user_id},
                conn,
            )["value"]
        ),
        "income": income_total,
        "expense": expense_total,
        "net": income_total - expense_total,
        "savingsRate": round((income_total - expense_total) / income_total * 100) if income_total else 0,
        "activeAccounts": len([account for account in accounts if account["is_active"]]),
        "inactiveAccounts": len([account for account in accounts if not account["is_active"]]),
        "transactionsThisMonth": len(month_transactions),
    }
    analytics = {
        "expenseByCategory": [
            {"category": key, "amount": value}
            for key, value in sorted(expense_by_category.items(), key=lambda item: item[1], reverse=True)
        ],
        "incomeByCategory": [
            {"category": key, "amount": value}
            for key, value in sorted(income_by_category.items(), key=lambda item: item[1], reverse=True)
        ],
        "recentActivity": recent_activity[:12],
    }
    return {
        "accounts": accounts,
        "categories": categories,
        "transactions": transactions,
        "transfers": transfers,
        "stats": stats,
        "analytics": analytics,
    }


def apply_transaction_balance(conn, transaction: dict[str, Any], direction: int = 1) -> None:
    amount = float(transaction.get("amount") or 0)
    delta = amount if transaction["transaction_type"] == "income" else -amount
    conn.execute(
        """
        UPDATE Accounts
        SET balance = balance + :delta
        WHERE account_id = :account_id AND user_id = :user_id
        """,
        {
            "delta": delta * direction,
            "account_id": transaction["account_id"],
            "user_id": transaction["user_id"],
        },
    )


def apply_transfer_balance(conn, transfer: dict[str, Any], direction: int = 1) -> None:
    amount = float(transfer.get("amount") or 0) * direction
    conn.execute(
        """
        UPDATE Accounts
        SET balance = balance - :amount
        WHERE account_id = :from_account_id AND user_id = :user_id
        """,
        {"amount": amount, "from_account_id": transfer["from_account_id"], "user_id": transfer["user_id"]},
    )
    conn.execute(
        """
        UPDATE Accounts
        SET balance = balance + :amount
        WHERE account_id = :to_account_id AND user_id = :user_id
        """,
        {"amount": amount, "to_account_id": transfer["to_account_id"], "user_id": transfer["user_id"]},
    )


def owned_account(conn, user_id: int, account_id: Any, *, active_only: bool = False) -> dict[str, Any]:
    try:
        normalized = int(account_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный счет")
    active_filter = "AND is_active = 1" if active_only else ""
    account = conn.execute(
        f"""
        SELECT *
        FROM Accounts
        WHERE account_id = :account_id
          AND user_id = :user_id
          {active_filter}
        """,
        {"account_id": normalized, "user_id": user_id},
    ).fetchone()
    if not account:
        raise HTTPException(status_code=400, detail="Счет не найден или недоступен")
    return account


def owned_category(conn, user_id: int, category_id: Any, category_type: str | None = None) -> dict[str, Any]:
    try:
        normalized = int(category_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная категория")
    category = conn.execute(
        """
        SELECT *
        FROM Categories
        WHERE category_id = :category_id
          AND user_id = :user_id
        """,
        {"category_id": normalized, "user_id": user_id},
    ).fetchone()
    if not category:
        raise HTTPException(status_code=400, detail="Категория не найдена или недоступна")
    if category_type and category["category_type"] != category_type:
        raise HTTPException(status_code=400, detail="Категория не соответствует типу операции")
    return category


def normalize_account_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("account_name"), 80)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название счета")
    currency = (clean_text(source.get("currency"), 8) or "RUB").upper()
    data = {
        "account_name": name,
        "account_type": validate_choice(source.get("account_type"), {"карта", "наличные", "счет", "вклад"}, "тип счета"),
        "balance": validate_money(source.get("balance", 0), "баланс", positive=False),
        "currency": currency,
        "is_active": int(source.get("is_active", 1) or 0),
    }
    if data["is_active"] not in (0, 1):
        raise HTTPException(status_code=400, detail="Некорректный статус счета")
    return data


def normalize_category_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("category_name"), 80)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название категории")
    return {
        "category_name": name,
        "category_type": validate_choice(source.get("category_type"), {"income", "expense"}, "тип категории"),
        "icon_name": clean_text(source.get("icon_name"), 40),
        "color": clean_text(source.get("color"), 20) or "#3c388d",
    }


def normalize_transaction_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    transaction_type = validate_choice(source.get("transaction_type"), {"income", "expense"}, "тип операции")
    account = owned_account(conn, user_id, source.get("account_id"), active_only=True)
    category = owned_category(conn, user_id, source.get("category_id"), transaction_type)
    return {
        "account_id": account["account_id"],
        "category_id": category["category_id"],
        "transaction_type": transaction_type,
        "amount": validate_money(source.get("amount"), "операция"),
        "description": clean_text(source.get("description"), 220),
        "transaction_date": validate_iso_date(source.get("transaction_date"), "операция"),
    }


def normalize_transfer_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    from_account = owned_account(conn, user_id, source.get("from_account_id"), active_only=True)
    to_account = owned_account(conn, user_id, source.get("to_account_id"), active_only=True)
    if int(from_account["account_id"]) == int(to_account["account_id"]):
        raise HTTPException(status_code=400, detail="Нельзя перевести деньги на тот же счет")
    return {
        "from_account_id": from_account["account_id"],
        "to_account_id": to_account["account_id"],
        "amount": validate_money(source.get("amount"), "перевод"),
        "transfer_date": validate_iso_date(source.get("transfer_date"), "перевод"),
        "comment": clean_text(source.get("comment"), 220),
    }


def validate_time_value(value: Any, field_label: str, *, required: bool = False) -> str | None:
    raw = clean_text(value, 5)
    if not raw:
        if required:
            raise HTTPException(status_code=400, detail=f"Укажите время: {field_label}")
        return None
    try:
        datetime.strptime(raw, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Некорректное время: {field_label}")
    return raw


def planner_category_id(conn, category_id: Any) -> int | None:
    if category_id in (None, ""):
        return None
    try:
        normalized = int(category_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная категория ежедневника")
    category = conn.execute(
        """
        SELECT planner_category_id
        FROM Planner_Categories
        WHERE planner_category_id = :category_id
        """,
        {"category_id": normalized},
    ).fetchone()
    if not category:
        raise HTTPException(status_code=400, detail="Категория ежедневника не найдена")
    return normalized


def normalize_planner_category_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("category_name"), 80)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название категории")
    return {"category_name": name}


def normalize_task_payload(conn, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    title = clean_text(source.get("title"), 140)
    if not title:
        raise HTTPException(status_code=400, detail="Укажите название задачи")
    due_date = clean_text(source.get("due_date"), 10)
    if due_date:
        due_date = validate_iso_date(due_date, "срок задачи")
    return {
        "planner_category_id": planner_category_id(conn, source.get("planner_category_id")),
        "title": title,
        "description": clean_text(source.get("description"), 800),
        "priority": validate_choice(source.get("priority") or "medium", {"low", "medium", "high"}, "приоритет"),
        "status": validate_choice(source.get("status") or "planned", {"planned", "in_progress", "done"}, "статус"),
        "due_date": due_date,
    }


def normalize_event_payload(conn, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    title = clean_text(source.get("title"), 140)
    if not title:
        raise HTTPException(status_code=400, detail="Укажите название события")
    start_time = validate_time_value(source.get("start_time"), "начало события")
    end_time = validate_time_value(source.get("end_time"), "окончание события")
    if start_time and end_time and end_time < start_time:
        raise HTTPException(status_code=400, detail="Окончание события не может быть раньше начала")
    return {
        "planner_category_id": planner_category_id(conn, source.get("planner_category_id")),
        "title": title,
        "description": clean_text(source.get("description"), 800),
        "event_date": validate_iso_date(source.get("event_date"), "событие"),
        "start_time": start_time,
        "end_time": end_time,
        "location": clean_text(source.get("location"), 160),
    }


def normalize_note_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    title = clean_text(source.get("title"), 140)
    if not title:
        raise HTTPException(status_code=400, detail="Укажите заголовок заметки")
    return {
        "title": title,
        "content": clean_text(source.get("content"), 3000),
        "updated_at": date.today().isoformat(),
    }


def normalize_app_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if "theme" in payload:
        data["theme"] = validate_choice(payload.get("theme") or "light", {"light", "dark"}, "тема")
    if "notifications_enabled" in payload:
        data["notifications_enabled"] = 1 if str(payload.get("notifications_enabled")) in {"1", "true", "on"} else 0
    if "lesson_reminder_minutes" in payload:
        data["lesson_reminder_minutes"] = validate_int_value(
            payload.get("lesson_reminder_minutes"),
            "напоминание перед парой",
            required=True,
            minimum=0,
            maximum=180,
        )
    return data


def validate_int_value(value: Any, field_label: str, *, required: bool = False, minimum: int = 0, maximum: int | None = None) -> int | None:
    if value in (None, ""):
        if required:
            raise HTTPException(status_code=400, detail=f"Укажите значение: {field_label}")
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Некорректное число: {field_label}")
    if normalized < minimum or (maximum is not None and normalized > maximum):
        raise HTTPException(status_code=400, detail=f"Некорректное значение: {field_label}")
    return normalized


def validate_float_value(value: Any, field_label: str, *, required: bool = False, minimum: float = 0) -> float | None:
    if value in (None, ""):
        if required:
            raise HTTPException(status_code=400, detail=f"Укажите значение: {field_label}")
        return None
    try:
        normalized = round(float(str(value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Некорректное число: {field_label}")
    if normalized < minimum:
        raise HTTPException(status_code=400, detail=f"Некорректное значение: {field_label}")
    return normalized


def workout_type_id(conn, workout_type_id: Any) -> int:
    normalized = validate_int_value(workout_type_id, "тип тренировки", required=True, minimum=1)
    row = conn.execute(
        "SELECT workout_type_id FROM Workout_Types WHERE workout_type_id = :workout_type_id",
        {"workout_type_id": normalized},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Тип тренировки не найден")
    return normalized


def exercise_id(conn, exercise_id_value: Any) -> int:
    normalized = validate_int_value(exercise_id_value, "упражнение", required=True, minimum=1)
    row = conn.execute(
        "SELECT exercise_id FROM Exercises WHERE exercise_id = :exercise_id",
        {"exercise_id": normalized},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Упражнение не найдено")
    return normalized


def owned_workout_plan(conn, user_id: int, plan_id_value: Any, *, required: bool = True) -> dict[str, Any] | None:
    if plan_id_value in (None, ""):
        if required:
            raise HTTPException(status_code=400, detail="Выберите план тренировки")
        return None
    normalized = validate_int_value(plan_id_value, "план тренировки", required=True, minimum=1)
    row = conn.execute(
        """
        SELECT *
        FROM Workout_Plans
        WHERE plan_id = :plan_id AND user_id = :user_id
        """,
        {"plan_id": normalized, "user_id": user_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="План тренировки не найден")
    return dict(row)


def owned_workout_log(conn, user_id: int, workout_log_id_value: Any) -> dict[str, Any]:
    normalized = validate_int_value(workout_log_id_value, "запись журнала", required=True, minimum=1)
    row = conn.execute(
        """
        SELECT *
        FROM Workout_Logs
        WHERE workout_log_id = :workout_log_id AND user_id = :user_id
        """,
        {"workout_log_id": normalized, "user_id": user_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Запись журнала не найдена")
    return dict(row)


def normalize_workout_type_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("type_name"), 80)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название типа тренировки")
    return {"type_name": name}


def normalize_exercise_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("exercise_name"), 120)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название упражнения")
    return {
        "exercise_name": name,
        "muscle_group": clean_text(source.get("muscle_group"), 80),
        "exercise_type": clean_text(source.get("exercise_type"), 80),
    }


def normalize_workout_plan_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    name = clean_text(source.get("plan_name"), 120)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название плана")
    return {
        "plan_name": name,
        "workout_type_id": workout_type_id(conn, source.get("workout_type_id")),
        "day_number": validate_int_value(source.get("day_number"), "день недели", required=True, minimum=1, maximum=7),
        "description": clean_text(source.get("description"), 500),
    }


def normalize_plan_exercise_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    plan = owned_workout_plan(conn, user_id, source.get("plan_id"), required=True)
    order = validate_int_value(source.get("exercise_order"), "порядок упражнения", minimum=1)
    if order is None:
        order_row = conn.execute(
            """
            SELECT COALESCE(MAX(exercise_order), 0) + 1 AS next_order
            FROM Plan_Exercises
            WHERE plan_id = :plan_id AND user_id = :user_id
            """,
            {"plan_id": plan["plan_id"], "user_id": user_id},
        ).fetchone()
        order = int(order_row["next_order"])
    return {
        "plan_id": plan["plan_id"],
        "exercise_id": exercise_id(conn, source.get("exercise_id")),
        "sets_count": validate_int_value(source.get("sets_count"), "подходы", minimum=0),
        "reps_count": validate_int_value(source.get("reps_count"), "повторения", minimum=0),
        "duration_minutes": validate_int_value(source.get("duration_minutes"), "минуты", minimum=0),
        "exercise_order": order,
    }


def normalize_workout_log_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    plan = owned_workout_plan(conn, user_id, source.get("plan_id"), required=False)
    return {
        "plan_id": plan["plan_id"] if plan else None,
        "workout_date": validate_iso_date(source.get("workout_date"), "дата тренировки"),
        "duration_minutes": validate_int_value(source.get("duration_minutes"), "минуты", minimum=0),
        "calories_burned": validate_int_value(source.get("calories_burned"), "ккал", minimum=0),
        "notes": clean_text(source.get("notes"), 500),
    }


def normalize_workout_log_exercise_payload(conn, user_id: int, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(existing or {}), **payload}
    workout_log = owned_workout_log(conn, user_id, source.get("workout_log_id"))
    return {
        "workout_log_id": workout_log["workout_log_id"],
        "exercise_id": exercise_id(conn, source.get("exercise_id")),
        "sets_done": validate_int_value(source.get("sets_done"), "выполнено подходов", minimum=0),
        "reps_done": validate_int_value(source.get("reps_done"), "выполнено повторений", minimum=0),
        "weight_used": validate_float_value(source.get("weight_used"), "вес", minimum=0),
        "duration_minutes": validate_int_value(source.get("duration_minutes"), "минуты", minimum=0),
    }


def workouts_payload(user_id: int, conn=None) -> dict[str, Any]:
    types = db.fetch_all("SELECT * FROM Workout_Types ORDER BY type_name", conn=conn)
    exercises = db.fetch_all("SELECT * FROM Exercises ORDER BY exercise_name", conn=conn)
    plans = db.fetch_all(
        """
        SELECT p.*, wt.type_name,
               COUNT(pe.plan_exercise_id) AS exercise_count,
               COALESCE(SUM(pe.duration_minutes), 0) AS planned_minutes
        FROM Workout_Plans p
        JOIN Workout_Types wt ON wt.workout_type_id = p.workout_type_id
        LEFT JOIN Plan_Exercises pe ON pe.plan_id = p.plan_id AND pe.user_id = p.user_id
        WHERE p.user_id = :user_id
        GROUP BY p.plan_id, p.plan_name, p.workout_type_id, p.day_number,
                 p.description, p.user_id, wt.type_name
        ORDER BY p.day_number, p.plan_name
        """,
        {"user_id": user_id},
        conn,
    )
    plan_exercises = db.fetch_all(
        """
        SELECT pe.*, e.exercise_name, e.muscle_group, e.exercise_type, p.plan_name
        FROM Plan_Exercises pe
        JOIN Exercises e ON e.exercise_id = pe.exercise_id
        JOIN Workout_Plans p ON p.plan_id = pe.plan_id
        WHERE pe.user_id = :user_id
        ORDER BY pe.plan_id, pe.exercise_order
        """,
        {"user_id": user_id},
        conn,
    )
    logs = db.fetch_all(
        """
        SELECT wl.*, p.plan_name
        FROM Workout_Logs wl
        LEFT JOIN Workout_Plans p ON p.plan_id = wl.plan_id
        WHERE wl.user_id = :user_id
        ORDER BY wl.workout_date DESC, wl.workout_log_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    log_exercises = db.fetch_all(
        """
        SELECT wle.*, e.exercise_name, e.muscle_group
        FROM Workout_Log_Exercises wle
        JOIN Exercises e ON e.exercise_id = wle.exercise_id
        WHERE wle.user_id = :user_id
        ORDER BY wle.workout_log_id, wle.log_exercise_id
        """,
        {"user_id": user_id},
        conn,
    )
    weekly_logs = [log for log in logs if log["workout_date"] >= today(-7)]
    today_logs = [log for log in logs if log["workout_date"] == today()]
    plan_days = {int(plan.get("day_number") or 0) for plan in plans}
    completed_days = set()
    for log in weekly_logs:
        try:
            completed_days.add(date.fromisoformat(log["workout_date"]).isoweekday())
        except (TypeError, ValueError):
            continue
    week_days = []
    for offset in range(7):
        day_date = date.today() + timedelta(days=offset)
        day_number = day_date.isoweekday()
        day_plans = [plan for plan in plans if int(plan.get("day_number") or 0) == day_number]
        day_logs = [log for log in logs if log["workout_date"] == day_date.isoformat()]
        week_days.append(
            {
                "date": day_date.isoformat(),
                "dayNumber": day_number,
                "plans": day_plans,
                "logs": day_logs,
                "isToday": offset == 0,
            }
        )
    next_plans = [
        plan
        for plan in plans
        if int(plan.get("day_number") or 0) >= weekday_number()
    ][:3] or plans[:3]
    return {
        "types": types,
        "exercises": exercises,
        "plans": plans,
        "planExercises": plan_exercises,
        "logs": logs,
        "logExercises": log_exercises,
        "groups": {
            "weekDays": week_days,
            "todayPlans": [plan for plan in plans if int(plan.get("day_number") or 0) == weekday_number()],
            "todayLogs": today_logs,
            "nextPlans": next_plans,
            "recentLogs": logs[:5],
        },
        "stats": {
            "weeklyLogs": len(weekly_logs),
            "todayLogs": len(today_logs),
            "totalLogs": len(logs),
            "totalExercises": len(exercises),
            "plannedDays": len(plan_days),
            "completedDays": len(completed_days & plan_days),
            "weeklyMinutes": sum(int(log.get("duration_minutes") or 0) for log in weekly_logs),
            "weeklyCalories": sum(int(log.get("calories_burned") or 0) for log in weekly_logs),
            "totalMinutes": sum(int(log.get("duration_minutes") or 0) for log in logs),
            "totalCalories": sum(int(log.get("calories_burned") or 0) for log in logs),
            "completion": min(100, round(len(completed_days & plan_days) / max(len(plan_days), 1) * 100)),
        },
    }


def planner_payload(user_id: int, conn=None) -> dict[str, Any]:
    categories = db.fetch_all("SELECT * FROM Planner_Categories ORDER BY category_name", conn=conn)
    tasks = db.fetch_all(
        """
        SELECT t.*, pc.category_name
        FROM Tasks t
        LEFT JOIN Planner_Categories pc ON pc.planner_category_id = t.planner_category_id
        WHERE t.user_id = :user_id
        ORDER BY
          CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
          t.due_date IS NULL,
          t.due_date
        """,
        {"user_id": user_id},
        conn,
    )
    events = db.fetch_all(
        """
        SELECT e.*, pc.category_name
        FROM Events e
        LEFT JOIN Planner_Categories pc ON pc.planner_category_id = e.planner_category_id
        WHERE e.user_id = :user_id
        ORDER BY e.event_date, e.start_time
        """,
        {"user_id": user_id},
        conn,
    )
    notes = db.fetch_all(
        """
        SELECT *
        FROM Notes
        WHERE user_id = :user_id
        ORDER BY updated_at DESC, note_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    today_value = today()
    week_end = today(7)
    active_tasks = [task for task in tasks if task["status"] != "done"]
    overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < today_value]
    today_tasks = [task for task in active_tasks if task.get("due_date") == today_value]
    week_tasks = [
        task
        for task in active_tasks
        if task.get("due_date") and today_value <= task["due_date"] <= week_end
    ]
    done_tasks = [task for task in tasks if task["status"] == "done"]
    week_events = [event for event in events if today_value <= event["event_date"] <= week_end]
    today_events = [event for event in events if event["event_date"] == today_value]
    calendar_days = []
    for offset in range(7):
        day_value = today(offset)
        calendar_days.append(
            {
                "date": day_value,
                "events": [event for event in events if event["event_date"] == day_value],
                "tasks": [task for task in active_tasks if task.get("due_date") == day_value],
            }
        )
    return {
        "categories": categories,
        "tasks": tasks,
        "events": events,
        "notes": notes,
        "groups": {
            "overdueTasks": overdue_tasks,
            "todayTasks": today_tasks,
            "weekTasks": week_tasks,
            "doneTasks": done_tasks[:8],
            "todayEvents": today_events,
            "weekEvents": week_events,
            "calendarDays": calendar_days,
        },
        "stats": {
            "activeTasks": len(active_tasks),
            "overdueTasks": len(overdue_tasks),
            "todayTasks": len(today_tasks),
            "doneTasks": len(done_tasks),
            "highPriority": len([task for task in active_tasks if task["priority"] == "high"]),
            "weekEvents": len(week_events),
            "todayEvents": len(today_events),
            "notes": len(notes),
        },
    }


def portfolio_payload(user_id: int, conn=None) -> dict[str, Any]:
    categories = db.fetch_all("SELECT * FROM Portfolio_Categories ORDER BY category_name", conn=conn)
    skills = db.fetch_all(
        """
        SELECT *
        FROM Portfolio_Skills
        WHERE user_id = :user_id
        ORDER BY level DESC, skill_name
        """,
        {"user_id": user_id},
        conn,
    )
    projects = db.fetch_all(
        """
        SELECT p.*, pc.category_name
        FROM Portfolio_Projects p
        LEFT JOIN Portfolio_Categories pc ON pc.portfolio_category_id = p.portfolio_category_id
        WHERE p.user_id = :user_id
        ORDER BY p.created_at DESC, p.project_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    achievements = db.fetch_all(
        """
        SELECT a.*, pc.category_name
        FROM Portfolio_Achievements a
        LEFT JOIN Portfolio_Categories pc ON pc.portfolio_category_id = a.portfolio_category_id
        WHERE a.user_id = :user_id
        ORDER BY a.achievement_date DESC, a.achievement_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    certificates = db.fetch_all(
        """
        SELECT c.*, pc.category_name
        FROM Portfolio_Certificates c
        LEFT JOIN Portfolio_Categories pc ON pc.portfolio_category_id = c.portfolio_category_id
        WHERE c.user_id = :user_id
        ORDER BY c.issue_date DESC, c.certificate_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    files = db.fetch_all(
        """
        SELECT *
        FROM Portfolio_Files
        WHERE user_id = :user_id
        ORDER BY uploaded_at DESC, file_id DESC
        """,
        {"user_id": user_id},
        conn,
    )
    completion_parts = [
        bool(projects),
        bool(achievements),
        bool(certificates),
        len(skills) >= 5,
        bool((profile_payload(user_id, conn) or {}).get("bio")),
    ]
    return {
        "categories": categories,
        "skills": skills,
        "projects": projects,
        "achievements": achievements,
        "certificates": certificates,
        "files": files,
        "stats": {
            "projects": len(projects),
            "achievements": len(achievements),
            "certificates": len(certificates),
            "skills": len(skills),
            "completion": round(sum(completion_parts) / len(completion_parts) * 100),
        },
    }


def dashboard_payload(user_id: int) -> dict[str, Any]:
    schedule = schedule_payload(user_id)
    finance = finance_payload(user_id)
    workouts = workouts_payload(user_id)
    planner = planner_payload(user_id)
    portfolio = portfolio_payload(user_id)
    today_lessons = [lesson for lesson in schedule["lessons"] if lesson["day_number"] == weekday_number()]
    tomorrow_lessons = [lesson for lesson in schedule["lessons"] if lesson["day_number"] == weekday_number(1)]
    next_lessons = sorted(
        [*today_lessons, *tomorrow_lessons],
        key=lambda lesson: (lesson["day_number"], lesson.get("lesson_number") or 0),
    )[:5]
    today_tasks = [task for task in planner["tasks"] if task["due_date"] == today() and task["status"] != "done"]
    today_events = [event for event in planner["events"] if event["event_date"] == today()]
    upcoming_tasks = [
        task
        for task in planner["tasks"]
        if task["status"] != "done" and (not task.get("due_date") or task["due_date"] <= today(7))
    ][:5]
    upcoming_events = [event for event in planner["events"] if today() <= event["event_date"] <= today(7)][:5]
    recent_transactions = finance["transactions"][:5]
    upcoming_workouts = [
        plan
        for plan in workouts["plans"]
        if int(plan.get("day_number") or 0) >= weekday_number()
    ][:3] or workouts["plans"][:3]
    latest_portfolio = [
        *[
            {
                "kind": "Проект",
                "title": project["title"],
                "text": project.get("status") or project.get("project_type") or "",
                "date": project.get("created_at"),
            }
            for project in portfolio["projects"][:2]
        ],
        *[
            {
                "kind": "Достижение",
                "title": achievement["title"],
                "text": achievement.get("issuer") or "",
                "date": achievement.get("achievement_date"),
            }
            for achievement in portfolio["achievements"][:2]
        ],
        *[
            {
                "kind": "Сертификат",
                "title": certificate["title"],
                "text": certificate.get("organization") or "",
                "date": certificate.get("issue_date"),
            }
            for certificate in portfolio["certificates"][:2]
        ],
    ][:4]

    return {
        "profile": profile_payload(user_id),
        "settings": settings_payload(user_id),
        "kpi": {
            "todayLessons": len(today_lessons),
            "tomorrowLessons": len(tomorrow_lessons),
            "firstTodayLesson": (today_lessons[0] or {}).get("start_time") if today_lessons else None,
            "firstTomorrowLesson": (tomorrow_lessons[0] or {}).get("start_time") if tomorrow_lessons else None,
            "balance": finance["stats"]["balance"],
            "monthlyIncome": finance["stats"]["income"],
            "monthlyExpense": finance["stats"]["expense"],
            "weeklyWorkouts": workouts["stats"]["weeklyLogs"],
            "activeTasks": planner["stats"]["activeTasks"],
            "todayTasks": len(today_tasks),
            "portfolioCompletion": portfolio["stats"]["completion"],
        },
        "modules": {
            "schedule": {
                "todayLessons": len(today_lessons),
                "tomorrowLessons": len(tomorrow_lessons),
                "group": (schedule["group"] or {}).get("group_name", "не выбрана"),
                "weekType": (schedule["week"] or {}).get("week_type", "обычная"),
            },
            "finance": finance["stats"],
            "workouts": workouts["stats"],
            "planner": planner["stats"],
            "portfolio": portfolio["stats"],
        },
        "widgets": {
            "lessons": next_lessons,
            "tasks": upcoming_tasks,
            "events": upcoming_events,
            "transactions": recent_transactions,
            "workouts": upcoming_workouts,
            "portfolio": latest_portfolio,
            "accounts": finance["accounts"][:4],
        },
        "todayFeed": [
            *[
                {
                    "kind": "Расписание",
                    "title": f"Пара: {lesson['discipline']}",
                    "text": f"{lesson.get('start_time') or ''}-{lesson.get('end_time') or ''} · {lesson.get('auditorium') or 'аудитория не указана'}",
                }
                for lesson in today_lessons
            ],
            *[
                {
                    "kind": "Ежедневник",
                    "title": event["title"],
                    "text": f"{event.get('start_time') or ''} {event.get('location') or ''}".strip(),
                }
                for event in today_events
            ],
            *[
                {"kind": "Задача", "title": task["title"], "text": f"Срок: {task['due_date']}"}
                for task in today_tasks
            ],
        ],
        "dates": {"today": today(), "tomorrow": today(1)},
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "db": db.db_label(), "database": "postgresql" if db.IS_POSTGRES else "sqlite"}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user = current_user_or_none(request)
    return {"user": user, "profile": profile_payload(user["user_id"]) if user else None}


@app.post("/api/auth/register", status_code=201)
def register(response: Response, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    full_name = validate_full_name(payload.get("full_name") or "")
    email = validate_email(payload.get("email") or "")
    password = payload.get("password") or ""
    password_confirm = payload.get("password_confirm")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 6 символов")
    if password_confirm is not None and password != password_confirm:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")

    with db.transaction() as conn:
        if db.get_user_by_email(email, conn):
            raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
        user = db.create_user(conn, full_name, email, password)
        session = db.create_session(conn, user["user_id"])
        profile = profile_payload(user["user_id"], conn)

    set_session_cookie(response, session)
    return {"user": user, "profile": profile, "message": "Аккаунт создан"}


@app.post("/api/auth/login")
def login(response: Response, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    email = validate_email(payload.get("email") or "")
    user_record = db.get_user_by_email(email)
    if not user_record or not db.verify_password(payload.get("password") or "", user_record["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    with db.transaction() as conn:
        user = db.public_user(user_record["user_id"], conn)
        db.initialize_user_account(conn, user["user_id"], user)
        session = db.create_session(conn, user["user_id"])
        profile = profile_payload(user["user_id"], conn)

    set_session_cookie(response, session)
    return {"user": user, "profile": profile, "message": "Вход выполнен"}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    db.delete_session(request.cookies.get(SESSION_COOKIE))
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return {
        "user": user,
        "profile": profile_payload(user["user_id"]),
        "settings": settings_payload(user["user_id"]),
        "scheduleMetadata": schedule_metadata(),
    }


@app.get("/api/dashboard")
def dashboard(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return dashboard_payload(user["user_id"])


@app.get("/api/profile")
def get_profile(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return {"profile": profile_payload(user["user_id"]), "metadata": schedule_metadata()}


@app.patch("/api/profile")
@app.put("/api/profile")
def update_profile(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    with db.transaction() as conn:
        profile = profile_payload(user["user_id"], conn)
        data: dict[str, Any] = {}
        if "full_name" in payload:
            data["full_name"] = validate_full_name(payload.get("full_name") or "")
        if "email" in payload:
            data["email"] = validate_email(payload.get("email") or "")
            existing = db.get_user_by_email(data["email"], conn)
            if existing and int(existing["user_id"]) != int(user["user_id"]):
                raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
        if "phone" in payload:
            data["phone"] = validate_phone(payload.get("phone"))
        if "bio" in payload:
            data["bio"] = clean_text(payload.get("bio"), 600)
        if "specialization" in payload:
            data["specialization"] = clean_text(payload.get("specialization"), 160)
        if "group_id" in payload:
            data["group_id"] = validate_group_id(conn, payload.get("group_id"))

        if profile:
            update_row(conn, "Student_Profile", "profile_id", profile["profile_id"], data, user["user_id"])
        else:
            insert_row(
                conn,
                "Student_Profile",
                {
                    "user_id": user["user_id"],
                    "full_name": data.get("full_name") or user["full_name"],
                    "email": data.get("email") or user["email"],
                    **data,
                },
            )
        if data.get("full_name"):
            conn.execute(
                "UPDATE Users SET full_name = :full_name WHERE user_id = :user_id",
                {"full_name": data["full_name"], "user_id": user["user_id"]},
            )
        if data.get("email"):
            conn.execute(
                "UPDATE Users SET email = :email WHERE user_id = :user_id",
                {"email": data["email"], "user_id": user["user_id"]},
            )
        if data.get("group_id"):
            settings = settings_payload(user["user_id"], conn)
            if settings:
                update_row(
                    conn,
                    "App_Settings",
                    "setting_id",
                    settings["setting_id"],
                    {"selected_group_id": data["group_id"]},
                    user["user_id"],
                )
            else:
                insert_row(
                    conn,
                    "App_Settings",
                    {
                        "user_id": user["user_id"],
                        "selected_group_id": data["group_id"],
                        "selected_week_type": "числитель",
                    },
                )
        fresh_user = db.public_user(user["user_id"], conn)
        fresh_profile = profile_payload(user["user_id"], conn)
    return {"user": fresh_user, "profile": fresh_profile, "message": "Профиль сохранен"}


@app.get("/api/settings")
def get_settings(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return {
        "settings": settings_payload(user["user_id"]),
        "profile": profile_payload(user["user_id"]),
    }


@app.patch("/api/settings")
@app.put("/api/settings")
def update_settings(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    data = normalize_app_settings_payload(payload)
    if not data:
        raise HTTPException(status_code=400, detail="Нет настроек для сохранения")

    with db.transaction() as conn:
        settings = settings_payload(user["user_id"], conn)
        if settings:
            update_row(conn, "App_Settings", "setting_id", settings["setting_id"], data, user["user_id"])
        else:
            profile = profile_payload(user["user_id"], conn) or {}
            insert_row(
                conn,
                "App_Settings",
                {
                    "selected_group_id": profile.get("group_id"),
                    "selected_week_type": "числитель",
                    **data,
                    "user_id": user["user_id"],
                },
            )

    return {
        "settings": settings_payload(user["user_id"]),
        "profile": profile_payload(user["user_id"]),
        "message": "Настройки сохранены",
    }


@app.get("/api/schedule")
def get_schedule(
    group_id: int | None = None,
    week_type: str | None = None,
    week_start: str | None = None,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return schedule_payload(user["user_id"], group_id, week_type, week_start)


@app.get("/api/schedule/metadata")
def get_schedule_metadata(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return schedule_metadata()


@app.get("/api/admin/schedule")
def admin_schedule(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return admin_schedule_payload()


@app.get("/api/admin/schedule/progress/{job_id}")
def admin_schedule_progress(job_id: str, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return sync_progress_snapshot(job_id)


def sync_groups_now(faculty: str, progress_callback=None) -> dict[str, Any]:
    client = TusurTimetableClient()
    if progress_callback:
        progress_callback(total=1, processed=0, message=f"Загружаем группы факультета {faculty}")
    groups_by_course = client.fetch_groups_for_faculty(faculty)
    faculty_name = FACULTIES.get(faculty, faculty)
    total_groups = sum(len(group_names) for group_names in groups_by_course.values())
    if progress_callback:
        progress_callback(total=max(total_groups, 1), processed=0, message=f"Сохраняем группы: {total_groups}")

    processed = 0
    with db.transaction() as conn:
        for course_number, group_names in groups_by_course.items():
            for group_name in group_names:
                db.upsert_group(conn, faculty, faculty_name, int(course_number), group_name)
                processed += 1
                if progress_callback:
                    progress_callback(processed=processed, total=max(total_groups, 1), message=f"Сохранено групп {processed} из {total_groups}")

    return {
        "ok": True,
        "faculty": faculty,
        "facultyName": faculty_name,
        "groups": groups_by_course,
        "metadata": schedule_metadata(),
        "admin": admin_schedule_payload(),
        "message": f"Обновлено групп: {total_groups}",
    }


def sync_all_groups_now(progress_callback=None) -> dict[str, Any]:
    client = TusurTimetableClient()
    imported: dict[str, int] = {}
    errors: dict[str, str] = {}
    parsed: dict[str, dict[int, list[str]]] = {}
    total_faculties = len(FACULTIES)

    if progress_callback:
        progress_callback(total=total_faculties, processed=0, message="Загружаем списки групп факультетов")
    for index, faculty in enumerate(FACULTIES, start=1):
        try:
            parsed[faculty] = client.fetch_groups_for_faculty(faculty)
        except Exception as exc:
            errors[faculty] = str(exc)
        if progress_callback:
            progress_callback(processed=index, total=total_faculties, message=f"Загружено факультетов {index} из {total_faculties}")

    total_groups = sum(len(group_names) for courses in parsed.values() for group_names in courses.values())
    processed_groups = 0
    if progress_callback:
        progress_callback(total=max(total_groups, 1), processed=0, message=f"Сохраняем группы: {total_groups}")
    with db.transaction() as conn:
        for faculty, groups_by_course in parsed.items():
            count = 0
            for course_number, group_names in groups_by_course.items():
                for group_name in group_names:
                    db.upsert_group(conn, faculty, FACULTIES[faculty], int(course_number), group_name)
                    count += 1
                    processed_groups += 1
                    if progress_callback:
                        progress_callback(processed=processed_groups, total=max(total_groups, 1), message=f"Сохранено групп {processed_groups} из {total_groups}")
            imported[faculty] = count

    return {
        "ok": not errors,
        "message": f"Обновлено групп: {sum(imported.values())}",
        "imported": imported,
        "errors": errors,
        "admin": admin_schedule_payload(),
    }


@app.post("/api/admin/schedule/sync-groups")
@app.post("/api/schedule/sync-groups")
def sync_groups(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    faculty = payload.get("faculty") or "fvs"
    if payload.get("_progress"):
        return start_sync_progress_job(
            "Обновление групп факультета",
            lambda progress: sync_groups_now(faculty, progress),
        )
    return sync_groups_now(faculty)


@app.post("/api/admin/schedule/sync-all-groups")
def sync_all_groups(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if payload.get("_progress"):
        return start_sync_progress_job(
            "Обновление групп",
            lambda progress: sync_all_groups_now(progress),
        )
    return sync_all_groups_now()


@app.patch("/api/admin/schedule/settings")
@app.put("/api/admin/schedule/settings")
def update_schedule_sync_settings(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    data = pick(payload, ["enabled", "lead_days", "run_time", "sync_mode"])
    with db.transaction() as conn:
        schedule_sync_settings(conn)
        update_row(conn, "Schedule_Sync_Settings", "setting_id", 1, data)
        conn.execute(
            "UPDATE Schedule_Sync_Settings SET updated_at = CURRENT_TIMESTAMP WHERE setting_id = 1"
        )
    return {"ok": True, "message": "Настройки автообновления сохранены", "admin": admin_schedule_payload()}


@app.post("/api/admin/schedule/run-auto")
def run_auto_schedule_now(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if payload.get("_progress"):
        def runner(progress):
            result = run_scheduled_sync_if_due(force=True, progress_callback=progress)
            return {
                **result,
                "message": f"Автообновление выполнено: сохранено групп {result.get('synced_groups', 0)}, пар {result.get('lesson_count', 0)}",
                "admin": admin_schedule_payload(),
            }

        return start_sync_progress_job(
            "Автообновление расписания",
            runner,
        )
    result = run_scheduled_sync_if_due(force=True)
    return {
        **result,
        "message": f"Автообновление выполнено: сохранено групп {result.get('synced_groups', 0)}, пар {result.get('lesson_count', 0)}",
        "admin": admin_schedule_payload(),
    }


@app.post("/api/admin/schedule/sync-all")
def sync_all_schedules(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if payload.get("_progress"):
        def runner(progress):
            refresh_groups = str(payload.get("refresh_groups", "1")).lower() not in {"0", "false", "no"}
            if refresh_groups:
                sync_all_groups_now(progress)
            result = sync_schedule_groups(
                trigger_type="manual",
                faculty=payload.get("faculty") or None,
                course_number=payload.get("course_number") or None,
                group_name=payload.get("group") or payload.get("group_name") or None,
                sync_mode=payload.get("sync_mode") or schedule_sync_settings().get("sync_mode") or "next",
                max_groups=payload.get("max_groups") or None,
                progress_callback=progress,
            )
            return {
                **result,
                "message": f"Синхронизация завершена: сохранено групп {result.get('synced_groups', 0)}, пар {result.get('lesson_count', 0)}, пустых ответов {result.get('empty_groups', 0)}",
                "admin": admin_schedule_payload(),
            }

        return start_sync_progress_job("Массовая синхронизация", runner)

    refresh_groups = str(payload.get("refresh_groups", "1")).lower() not in {"0", "false", "no"}
    if refresh_groups:
        sync_all_groups_now()
    result = sync_schedule_groups(
        trigger_type="manual",
        faculty=payload.get("faculty") or None,
        course_number=payload.get("course_number") or None,
        group_name=payload.get("group") or payload.get("group_name") or None,
        sync_mode=payload.get("sync_mode") or schedule_sync_settings().get("sync_mode") or "next",
        max_groups=payload.get("max_groups") or None,
    )
    return {
        **result,
        "message": f"Синхронизация завершена: сохранено групп {result.get('synced_groups', 0)}, пар {result.get('lesson_count', 0)}, пустых ответов {result.get('empty_groups', 0)}",
        "admin": admin_schedule_payload(),
    }


@app.post("/api/admin/schedule/sync")
@app.post("/api/schedule/sync")
def sync_schedule(payload: dict[str, Any] = Body(default={}), user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    faculty = payload.get("faculty") or "fvs"
    group_name = payload.get("group") or payload.get("group_name")
    week_id = payload.get("week_id") or None
    week_offset = None
    if payload.get("week_offset") not in (None, ""):
        try:
            week_offset = int(payload.get("week_offset"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Некорректная неделя для парса")
    if not group_name:
        raise HTTPException(status_code=400, detail="Укажите группу")
    if payload.get("_progress"):
        def runner(progress):
            progress(total=1, processed=0, message=f"Обновляем группу {group_name}")
            clean_payload = {**payload}
            clean_payload.pop("_progress", None)
            result = sync_schedule(clean_payload, user)
            progress(total=1, processed=1, message=result.get("message") or "Группа обработана")
            return result

        return start_sync_progress_job("Синхронизация группы", runner)

    target_week_label = week_id or (str(week_offset) if week_offset is not None else "current")
    log_id = create_sync_log(
        trigger_type="manual-one",
        target_scope=f"{faculty}:{group_name}",
        target_week=str(target_week_label),
        total_groups=1,
    )
    result = {
        "ok": True,
        "log_id": log_id,
        "total_groups": 1,
        "synced_groups": 0,
        "lesson_count": 0,
        "empty_groups": 0,
        "error_count": 0,
    }

    try:
        client = TusurTimetableClient()
        current_week = client.fetch_current_week(faculty, group_name)
        with db.transaction() as conn:
            ensure_future_week_ids(conn, current_week)
        if week_id is None and week_offset is not None and current_week.week_id is not None:
            week_id = current_week.week_id + week_offset
        parsed = client.fetch_schedule(faculty, group_name, week_id)
        week_offset = 0
        if week_id and current_week.week_id is not None:
            try:
                week_offset = int(week_id) - int(current_week.week_id)
            except (TypeError, ValueError):
                week_offset = 0
        week = complete_week_info(asdict(parsed.week), current_week, week_offset)
        lessons = [asdict(lesson) for lesson in parsed.lessons]
        week_type = week.get("week_type") or "обычная"

        if not lessons:
            result["empty_groups"] = 1
            finish_sync_log(
                log_id,
                "success",
                result,
                f"Для группы {group_name} расписание не опубликовано или занятий нет",
            )
            return {
                "ok": True,
                "warning": "На выбранную неделю расписание не опубликовано или занятий нет. Текущие локальные записи сохранены.",
                "parsed": {"faculty": parsed.faculty, "group": parsed.group, "week": week, "lessons": lessons},
                "schedule": schedule_payload(user["user_id"]),
                "admin": admin_schedule_payload(),
            }

        with db.transaction() as conn:
            save_parsed_schedule(conn, faculty, group_name, week, lessons, fallback_starts_at=week_start_for_offset(current_week, week_offset))
            saved_group = conn.execute("SELECT group_id FROM Groups WHERE group_name = :group_name", {"group_name": group_name}).fetchone()
            group_id = saved_group["group_id"]

            settings = settings_payload(user["user_id"], conn)
            settings_data = {
                "selected_group_id": group_id,
                "selected_week_type": normalize_week_for_settings(week_type),
            }
            if settings:
                update_row(conn, "App_Settings", "setting_id", settings["setting_id"], settings_data, user["user_id"])
            else:
                insert_row(conn, "App_Settings", {**settings_data, "user_id": user["user_id"]})

        result["synced_groups"] = 1
        result["lesson_count"] = len(lessons)
        finish_sync_log(log_id, "success", result, f"Группа {group_name} обновлена")
        return {
            "ok": True,
            "message": f"Расписание группы {group_name} обновлено: {len(lessons)} пар",
            "parsed": {"faculty": parsed.faculty, "group": parsed.group, "week": week, "lessons": lessons},
            "schedule": schedule_payload(user["user_id"]),
            "admin": admin_schedule_payload(),
        }
    except Exception as exc:
        result["ok"] = False
        result["error_count"] = 1
        finish_sync_log(log_id, "failed", result, str(exc))
        raise


@app.get("/api/finance")
def finance(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return finance_payload(user["user_id"])


@app.api_route("/api/finance/{resource}", methods=["POST"])
@app.api_route("/api/finance/{resource}/{item_id}", methods=["PUT", "PATCH", "DELETE"])
def finance_crud(
    request: Request,
    resource: str,
    item_id: int | None = None,
    payload: dict[str, Any] | None = Body(default=None),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    payload = payload or {}
    user_id = user["user_id"]
    configs = {
        "accounts": ("Accounts", "account_id", ["account_name", "account_type", "balance", "currency", "is_active"]),
        "categories": ("Categories", "category_id", ["category_name", "category_type", "icon_name", "color"]),
    }

    if resource in configs:
        table, id_field, fields = configs[resource]
        message = "Данные сохранены"
        with db.transaction() as conn:
            if request.method == "POST":
                if resource == "accounts":
                    data = normalize_account_payload(payload)
                    message = "Счет добавлен"
                else:
                    data = normalize_category_payload(payload)
                    message = "Категория добавлена"
                insert_row(conn, table, {**data, "user_id": user_id})
            elif request.method in ("PUT", "PATCH") and item_id:
                old_row = get_owned_row(conn, table, id_field, item_id, user_id)
                if not old_row:
                    raise HTTPException(status_code=404, detail="Запись не найдена")
                data = normalize_account_payload(payload, old_row) if resource == "accounts" else normalize_category_payload(payload, old_row)
                if resource == "categories" and data["category_type"] != old_row["category_type"]:
                    used = conn.execute(
                        """
                        SELECT transaction_id
                        FROM Transactions
                        WHERE category_id = :item_id AND user_id = :user_id
                        LIMIT 1
                        """,
                        {"item_id": item_id, "user_id": user_id},
                    ).fetchone()
                    if used:
                        raise HTTPException(status_code=400, detail="Нельзя сменить тип категории, которая используется в операциях")
                update_row(conn, table, id_field, item_id, data, user_id)
                message = "Запись обновлена"
            elif request.method == "DELETE" and item_id:
                if resource == "accounts":
                    conn.execute(
                        "UPDATE Accounts SET is_active = 0 WHERE account_id = :item_id AND user_id = :user_id",
                        {"item_id": item_id, "user_id": user_id},
                    )
                    message = "Счет скрыт из активных"
                else:
                    used = conn.execute(
                        """
                        SELECT transaction_id
                        FROM Transactions
                        WHERE category_id = :item_id AND user_id = :user_id
                        LIMIT 1
                        """,
                        {"item_id": item_id, "user_id": user_id},
                    ).fetchone()
                    if used:
                        raise HTTPException(status_code=400, detail="Категория используется в операциях")
                    delete_row(conn, table, id_field, item_id, user_id)
                    message = "Категория удалена"
            else:
                raise HTTPException(status_code=405, detail="Метод не поддерживается")
        result = finance_payload(user_id)
        result["message"] = message
        return result

    if resource == "transactions":
        message = "Операция сохранена"
        with db.transaction() as conn:
            if request.method == "POST":
                row = {**normalize_transaction_payload(conn, user_id, payload), "user_id": user_id}
                transaction_id = insert_row(conn, "Transactions", row)
                apply_transaction_balance(conn, {**row, "transaction_id": transaction_id}, 1)
                message = "Операция добавлена"
            elif request.method in ("PUT", "PATCH") and item_id:
                old_row = get_owned_row(conn, "Transactions", "transaction_id", item_id, user_id)
                if not old_row:
                    raise HTTPException(status_code=404, detail="Операция не найдена")
                apply_transaction_balance(conn, old_row, -1)
                data = normalize_transaction_payload(conn, user_id, payload, old_row)
                update_row(conn, "Transactions", "transaction_id", item_id, data, user_id)
                apply_transaction_balance(conn, get_owned_row(conn, "Transactions", "transaction_id", item_id, user_id), 1)
                message = "Операция обновлена"
            elif request.method == "DELETE" and item_id:
                old_row = get_owned_row(conn, "Transactions", "transaction_id", item_id, user_id)
                if old_row:
                    apply_transaction_balance(conn, old_row, -1)
                    delete_row(conn, "Transactions", "transaction_id", item_id, user_id)
                message = "Операция удалена"
            else:
                raise HTTPException(status_code=405, detail="Метод не поддерживается")
        result = finance_payload(user_id)
        result["message"] = message
        return result

    if resource == "transfers":
        message = "Перевод сохранен"
        with db.transaction() as conn:
            if request.method == "POST":
                row = {**normalize_transfer_payload(conn, user_id, payload), "user_id": user_id}
                transfer_id = insert_row(conn, "Transfers", row)
                apply_transfer_balance(conn, {**row, "transfer_id": transfer_id}, 1)
                message = "Перевод добавлен"
            elif request.method in ("PUT", "PATCH") and item_id:
                old_row = get_owned_row(conn, "Transfers", "transfer_id", item_id, user_id)
                if not old_row:
                    raise HTTPException(status_code=404, detail="Перевод не найден")
                apply_transfer_balance(conn, old_row, -1)
                data = normalize_transfer_payload(conn, user_id, payload, old_row)
                update_row(conn, "Transfers", "transfer_id", item_id, data, user_id)
                apply_transfer_balance(conn, get_owned_row(conn, "Transfers", "transfer_id", item_id, user_id), 1)
                message = "Перевод обновлен"
            elif request.method == "DELETE" and item_id:
                old_row = get_owned_row(conn, "Transfers", "transfer_id", item_id, user_id)
                if old_row:
                    apply_transfer_balance(conn, old_row, -1)
                    delete_row(conn, "Transfers", "transfer_id", item_id, user_id)
                message = "Перевод удален"
            else:
                raise HTTPException(status_code=405, detail="Метод не поддерживается")
        result = finance_payload(user_id)
        result["message"] = message
        return result

    raise HTTPException(status_code=404, detail="Раздел финансов не найден")


@app.get("/api/workouts")
def workouts(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return workouts_payload(user["user_id"])


@app.get("/api/planner")
def planner(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return planner_payload(user["user_id"])


@app.get("/api/portfolio")
def portfolio(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return portfolio_payload(user["user_id"])


CRUD_CONFIGS = {
    "workouts/types": ("Workout_Types", "workout_type_id", ["type_name"], workouts_payload, False),
    "workouts/exercises": ("Exercises", "exercise_id", ["exercise_name", "muscle_group", "exercise_type"], workouts_payload, False),
    "workouts/plans": ("Workout_Plans", "plan_id", ["plan_name", "workout_type_id", "day_number", "description"], workouts_payload, True),
    "workouts/plan-exercises": (
        "Plan_Exercises",
        "plan_exercise_id",
        ["plan_id", "exercise_id", "sets_count", "reps_count", "duration_minutes", "exercise_order"],
        workouts_payload,
        True,
    ),
    "workouts/logs": ("Workout_Logs", "workout_log_id", ["plan_id", "workout_date", "duration_minutes", "calories_burned", "notes"], workouts_payload, True),
    "workouts/log-exercises": (
        "Workout_Log_Exercises",
        "log_exercise_id",
        ["workout_log_id", "exercise_id", "sets_done", "reps_done", "weight_used", "duration_minutes"],
        workouts_payload,
        True,
    ),
    "planner/categories": ("Planner_Categories", "planner_category_id", ["category_name"], planner_payload, False),
    "planner/tasks": ("Tasks", "task_id", ["planner_category_id", "title", "description", "priority", "status", "due_date"], planner_payload, True),
    "planner/events": (
        "Events",
        "event_id",
        ["planner_category_id", "title", "description", "event_date", "start_time", "end_time", "location"],
        planner_payload,
        True,
    ),
    "planner/notes": ("Notes", "note_id", ["title", "content"], planner_payload, True),
    "portfolio/categories": ("Portfolio_Categories", "portfolio_category_id", ["category_name"], portfolio_payload, False),
    "portfolio/skills": ("Portfolio_Skills", "skill_id", ["skill_name", "category", "level"], portfolio_payload, True),
    "portfolio/projects": (
        "Portfolio_Projects",
        "project_id",
        [
            "portfolio_category_id",
            "title",
            "project_type",
            "technologies",
            "description",
            "start_date",
            "end_date",
            "status",
            "result_text",
            "repository_url",
            "project_url",
        ],
        portfolio_payload,
        True,
    ),
    "portfolio/achievements": (
        "Portfolio_Achievements",
        "achievement_id",
        ["portfolio_category_id", "title", "achievement_type", "issuer", "achievement_date", "description"],
        portfolio_payload,
        True,
    ),
    "portfolio/certificates": (
        "Portfolio_Certificates",
        "certificate_id",
        ["portfolio_category_id", "title", "organization", "issue_date", "expiry_date", "certificate_number", "description", "file_path"],
        portfolio_payload,
        True,
    ),
    "portfolio/files": (
        "Portfolio_Files",
        "file_id",
        ["project_id", "achievement_id", "certificate_id", "file_name", "file_type", "file_path", "file_size"],
        portfolio_payload,
        True,
    ),
}


@app.api_route("/api/{module}/{resource}", methods=["POST"])
@app.api_route("/api/{module}/{resource}/{item_id}", methods=["PUT", "PATCH", "DELETE"])
def generic_crud(
    request: Request,
    module: str,
    resource: str,
    item_id: int | None = None,
    payload: dict[str, Any] | None = Body(default=None),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    key = f"{module}/{resource}"
    if key not in CRUD_CONFIGS:
        raise HTTPException(status_code=404, detail="Раздел не найден")

    table, id_field, fields, payload_fn, user_scoped = CRUD_CONFIGS[key]
    payload = payload or {}
    user_id = user["user_id"]
    data = pick(payload, fields)

    with db.transaction() as conn:
        if request.method == "POST":
            if key == "workouts/types":
                row = normalize_workout_type_payload(payload)
                message = "Тип тренировки добавлен"
            elif key == "workouts/exercises":
                row = normalize_exercise_payload(payload)
                message = "Упражнение добавлено"
            elif key == "workouts/plans":
                row = normalize_workout_plan_payload(conn, user_id, payload)
                message = "План тренировки добавлен"
            elif key == "workouts/plan-exercises":
                row = normalize_plan_exercise_payload(conn, user_id, payload)
                message = "Упражнение добавлено в план"
            elif key == "workouts/logs":
                row = normalize_workout_log_payload(conn, user_id, payload)
                message = "Тренировка записана"
            elif key == "workouts/log-exercises":
                row = normalize_workout_log_exercise_payload(conn, user_id, payload)
                message = "Подходы записаны"
            elif key == "planner/categories":
                row = normalize_planner_category_payload(payload)
                message = "Категория добавлена"
            elif key == "planner/tasks":
                row = normalize_task_payload(conn, payload)
                message = "Задача добавлена"
            elif key == "planner/events":
                row = normalize_event_payload(conn, payload)
                message = "Событие добавлено"
            elif key == "planner/notes":
                row = normalize_note_payload(payload)
                message = "Заметка добавлена"
            else:
                row = data.copy()
                message = "Запись добавлена"
            if user_scoped:
                row["user_id"] = user_id
            insert_row(conn, table, row)
        elif request.method in ("PUT", "PATCH") and item_id:
            old_row = get_owned_row(conn, table, id_field, item_id, user_id) if user_scoped else conn.execute(
                f'SELECT * FROM "{table}" WHERE "{id_field}" = :item_id',
                {"item_id": item_id},
            ).fetchone()
            if not old_row:
                raise HTTPException(status_code=404, detail="Запись не найдена")
            if key == "workouts/types":
                data = normalize_workout_type_payload(payload, old_row)
                message = "Тип тренировки обновлен"
            elif key == "workouts/exercises":
                data = normalize_exercise_payload(payload, old_row)
                message = "Упражнение обновлено"
            elif key == "workouts/plans":
                data = normalize_workout_plan_payload(conn, user_id, payload, old_row)
                message = "План тренировки обновлен"
            elif key == "workouts/plan-exercises":
                data = normalize_plan_exercise_payload(conn, user_id, payload, old_row)
                message = "Упражнение в плане обновлено"
            elif key == "workouts/logs":
                data = normalize_workout_log_payload(conn, user_id, payload, old_row)
                message = "Тренировка обновлена"
            elif key == "workouts/log-exercises":
                data = normalize_workout_log_exercise_payload(conn, user_id, payload, old_row)
                message = "Подходы обновлены"
            elif key == "planner/categories":
                data = normalize_planner_category_payload(payload, old_row)
                message = "Категория обновлена"
            elif key == "planner/tasks":
                data = normalize_task_payload(conn, payload, old_row)
                message = "Задача обновлена"
            elif key == "planner/events":
                data = normalize_event_payload(conn, payload, old_row)
                message = "Событие обновлено"
            elif key == "planner/notes":
                data = normalize_note_payload(payload, old_row)
                message = "Заметка обновлена"
            else:
                message = "Запись обновлена"
            update_row(conn, table, id_field, item_id, data, user_id if user_scoped else None)
        elif request.method == "DELETE" and item_id:
            if key == "workouts/types":
                used = conn.execute(
                    "SELECT 1 FROM Workout_Plans WHERE workout_type_id = :item_id LIMIT 1",
                    {"item_id": item_id},
                ).fetchone()
                if used:
                    raise HTTPException(status_code=400, detail="Тип используется в планах тренировок")
            if key == "workouts/exercises":
                used = conn.execute(
                    "SELECT 1 FROM Plan_Exercises WHERE exercise_id = :item_id LIMIT 1",
                    {"item_id": item_id},
                ).fetchone() or conn.execute(
                    "SELECT 1 FROM Workout_Log_Exercises WHERE exercise_id = :item_id LIMIT 1",
                    {"item_id": item_id},
                ).fetchone()
                if used:
                    raise HTTPException(status_code=400, detail="Упражнение используется в планах или журнале")
            if key == "workouts/plans":
                conn.execute("DELETE FROM Plan_Exercises WHERE plan_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
                conn.execute("UPDATE Workout_Logs SET plan_id = NULL WHERE plan_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
            if key == "workouts/logs":
                conn.execute("DELETE FROM Workout_Log_Exercises WHERE workout_log_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
            if key == "planner/categories":
                used = conn.execute(
                    """
                    SELECT 1
                    FROM Tasks
                    WHERE planner_category_id = :item_id
                    LIMIT 1
                    """,
                    {"item_id": item_id},
                ).fetchone() or conn.execute(
                    """
                    SELECT 1
                    FROM Events
                    WHERE planner_category_id = :item_id
                    LIMIT 1
                    """,
                    {"item_id": item_id},
                ).fetchone()
                if used:
                    raise HTTPException(status_code=400, detail="Категория используется в задачах или событиях")
            if key == "portfolio/projects":
                conn.execute("DELETE FROM Portfolio_Files WHERE project_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
            if key == "portfolio/achievements":
                conn.execute("DELETE FROM Portfolio_Files WHERE achievement_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
            if key == "portfolio/certificates":
                conn.execute("DELETE FROM Portfolio_Files WHERE certificate_id = :item_id AND user_id = :user_id", {"item_id": item_id, "user_id": user_id})
            delete_row(conn, table, id_field, item_id, user_id if user_scoped else None)
            message = "Запись удалена"
        else:
            raise HTTPException(status_code=405, detail="Метод не поддерживается")

    result = payload_fn(user_id)
    result["message"] = message
    return result


@app.get("/{file_path:path}")
def frontend(file_path: str = ""):
    requested = PUBLIC_DIR / (file_path or "index.html")
    if requested.is_file():
        return FileResponse(requested)
    return FileResponse(PUBLIC_DIR / "index.html")
