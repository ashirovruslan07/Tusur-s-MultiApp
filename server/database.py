from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("MULTIAPP_DB_PATH", ROOT_DIR / "multi_app.sqlite"))
SQL_PATH = ROOT_DIR / "sql.txt"
APP_TZ = ZoneInfo(os.environ.get("MULTIAPP_TIMEZONE", "Asia/Tomsk"))


def _dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = _dict_factory
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def transaction() -> Iterable[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_all(sql: str, params: dict[str, Any] | None = None, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    close = conn is None
    conn = conn or connect()
    try:
        return list(conn.execute(sql, params or {}).fetchall())
    finally:
        if close:
            conn.close()


def fetch_one(sql: str, params: dict[str, Any] | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    close = conn is None
    conn = conn or connect()
    try:
        return conn.execute(sql, params or {}).fetchone()
    finally:
        if close:
            conn.close()


def execute(sql: str, params: dict[str, Any] | None = None, conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
    close = conn is None
    conn = conn or connect()
    try:
        cursor = conn.execute(sql, params or {})
        if close:
            conn.commit()
        return cursor
    finally:
        if close:
            conn.close()


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {column["name"] for column in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def add_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name not in table_columns(conn, table_name):
        conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {definition}')


def relax_weekly_schedule_check(conn: sqlite3.Connection) -> None:
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'Weekly_Schedule'"
    ).fetchone()
    schema = schema_row["sql"] if schema_row else ""
    accepts_canonical = "'четная'" in schema and "'нечетная'" in schema
    accepts_legacy = "'числитель'" in schema and "'знаменатель'" in schema
    if accepts_canonical and accepts_legacy:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS Weekly_Schedule_new (
            schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            week_type TEXT NOT NULL
                CHECK (week_type IN ('четная', 'нечетная', 'обычная', 'числитель', 'знаменатель')),
            FOREIGN KEY (group_id) REFERENCES Groups(group_id)
        );

        INSERT INTO Weekly_Schedule_new (schedule_id, group_id, week_type)
            SELECT schedule_id, group_id, week_type FROM Weekly_Schedule;

        DROP TABLE Weekly_Schedule;
        ALTER TABLE Weekly_Schedule_new RENAME TO Weekly_Schedule;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def migrate(conn: sqlite3.Connection) -> None:
    if SQL_PATH.exists():
        conn.executescript(SQL_PATH.read_text(encoding="utf-8"))

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS Users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS Sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES Users(user_id)
        );

        CREATE TABLE IF NOT EXISTS Portfolio_Skills (
            skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            category TEXT DEFAULT 'Общее',
            level INTEGER DEFAULT 70 CHECK (level BETWEEN 0 AND 100),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES Users(user_id)
        );

        CREATE TABLE IF NOT EXISTS Schedule_Sync_Settings (
            setting_id INTEGER PRIMARY KEY CHECK (setting_id = 1),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            lead_days INTEGER NOT NULL DEFAULT 2 CHECK (lead_days BETWEEN 0 AND 6),
            run_time TEXT NOT NULL DEFAULT '18:00',
            sync_mode TEXT NOT NULL DEFAULT 'next'
                CHECK (sync_mode IN ('current', 'next', 'current_next')),
            last_run_key TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS Schedule_Sync_Log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            trigger_type TEXT NOT NULL,
            target_scope TEXT NOT NULL,
            target_week TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            total_groups INTEGER NOT NULL DEFAULT 0,
            synced_groups INTEGER NOT NULL DEFAULT 0,
            lesson_count INTEGER NOT NULL DEFAULT 0,
            empty_groups INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            message TEXT
        );
        """
    )

    relax_weekly_schedule_check(conn)

    add_column(conn, "Users", "is_admin", "is_admin INTEGER NOT NULL DEFAULT 0")
    add_column(conn, "Faculties", "site_code", "site_code TEXT")
    add_column(conn, "Student_Profile", "user_id", "user_id INTEGER")
    add_column(conn, "Student_Profile", "bio", "bio TEXT")
    add_column(conn, "Student_Profile", "specialization", "specialization TEXT")
    add_column(conn, "App_Settings", "user_id", "user_id INTEGER")
    add_column(conn, "App_Settings", "language", "language TEXT DEFAULT 'ru'")
    add_column(conn, "App_Settings", "date_format", "date_format TEXT DEFAULT 'DD.MM.YYYY'")
    add_column(conn, "App_Settings", "auto_update_schedule", "auto_update_schedule INTEGER NOT NULL DEFAULT 1")
    add_column(conn, "App_Settings", "lesson_reminder_minutes", "lesson_reminder_minutes INTEGER NOT NULL DEFAULT 15")
    add_column(conn, "Weekly_Schedule", "source_week_id", "source_week_id INTEGER")
    add_column(conn, "Weekly_Schedule", "week_number", "week_number INTEGER")
    add_column(conn, "Weekly_Schedule", "starts_at", "starts_at TEXT")
    add_column(conn, "Weekly_Schedule", "synced_at", "synced_at TEXT")

    for table_name in [
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
    ]:
        add_column(conn, table_name, "user_id", "user_id INTEGER")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_faculties_site_code
            ON Faculties(site_code) WHERE site_code IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_weekly_schedule_group_id
            ON Weekly_Schedule(group_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON Sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON Accounts(user_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON Transactions(user_id);
        CREATE INDEX IF NOT EXISTS idx_transfers_user_id ON Transfers(user_id);
        CREATE INDEX IF NOT EXISTS idx_workout_plans_user_id ON Workout_Plans(user_id);
        CREATE INDEX IF NOT EXISTS idx_plan_exercises_user_id ON Plan_Exercises(user_id);
        CREATE INDEX IF NOT EXISTS idx_workout_logs_user_id ON Workout_Logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON Tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_events_user_id ON Events(user_id);
        CREATE INDEX IF NOT EXISTS idx_notes_user_id ON Notes(user_id);
        CREATE INDEX IF NOT EXISTS idx_portfolio_projects_user_id ON Portfolio_Projects(user_id);
        CREATE INDEX IF NOT EXISTS idx_portfolio_skills_user_id ON Portfolio_Skills(user_id);
        CREATE INDEX IF NOT EXISTS idx_schedule_sync_log_started_at ON Schedule_Sync_Log(started_at);
        """
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO Schedule_Sync_Settings
            (setting_id, enabled, lead_days, run_time, sync_mode)
        VALUES
            (1, 1, 2, '18:00', 'next')
        """
    )


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False

    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            )
            return hmac.compare_digest(actual.hex(), digest_hex)
        except Exception:
            return False

    # Compatibility with the earlier Node.js prototype hash format: salt:hex_scrypt_hash.
    try:
        salt_hex, digest_hex = stored_hash.split(":", 1)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt_hex.encode("utf-8"),
            n=16_384,
            r=8,
            p=1,
            dklen=64,
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except Exception:
        return False


def public_user(user_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT user_id, full_name, email, is_admin, created_at
        FROM Users
        WHERE user_id = :user_id
        """,
        {"user_id": user_id},
        conn,
    )


def get_user_by_email(email: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT * FROM Users WHERE email = :email",
        {"email": normalize_email(email)},
        conn,
    )


def create_user(conn: sqlite3.Connection, full_name: str, email: str, password: str) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO Users (full_name, email, password_hash)
        VALUES (:full_name, :email, :password_hash)
        """,
        {
            "full_name": full_name.strip(),
            "email": normalize_email(email),
            "password_hash": hash_password(password),
        },
    )
    user = public_user(cursor.lastrowid, conn)
    initialize_user_account(conn, user["user_id"], user)
    return user


def create_session(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    session_id = secrets.token_hex(32)
    expires_at = (datetime.now(APP_TZ).date() + timedelta(days=14)).isoformat() + "T23:59:59"
    conn.execute(
        """
        INSERT INTO Sessions (session_id, user_id, expires_at)
        VALUES (:session_id, :user_id, :expires_at)
        """,
        {"session_id": session_id, "user_id": user_id, "expires_at": expires_at},
    )
    return {"session_id": session_id, "expires_at": expires_at}


def get_user_by_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    return fetch_one(
        """
        SELECT u.user_id, u.full_name, u.email, u.is_admin, u.created_at
        FROM Sessions s
        JOIN Users u ON u.user_id = s.user_id
        WHERE s.session_id = :session_id
          AND s.expires_at > datetime('now')
        """,
        {"session_id": session_id},
    )


def delete_session(session_id: str | None) -> None:
    if session_id:
        execute("DELETE FROM Sessions WHERE session_id = :session_id", {"session_id": session_id})


def ensure_courses(conn: sqlite3.Connection) -> None:
    for course_number in range(1, 7):
        conn.execute(
            "INSERT OR IGNORE INTO Courses (course_number) VALUES (:course_number)",
            {"course_number": course_number},
        )


def upsert_faculty(conn: sqlite3.Connection, code: str, full_name: str, abbreviation: str | None = None) -> int:
    existing = conn.execute(
        """
        SELECT faculty_id
        FROM Faculties
        WHERE site_code = :code OR abbreviation = :code
        LIMIT 1
        """,
        {"code": code},
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE Faculties
            SET full_name = :full_name,
                abbreviation = :abbreviation,
                site_code = :code
            WHERE faculty_id = :faculty_id
            """,
            {
                "full_name": full_name,
                "abbreviation": abbreviation or code.upper(),
                "code": code,
                "faculty_id": existing["faculty_id"],
            },
        )
        return existing["faculty_id"]

    cursor = conn.execute(
        """
        INSERT INTO Faculties (full_name, abbreviation, site_code)
        VALUES (:full_name, :abbreviation, :code)
        """,
        {"full_name": full_name, "abbreviation": abbreviation or code.upper(), "code": code},
    )
    return cursor.lastrowid


def upsert_group(conn: sqlite3.Connection, faculty_code: str, faculty_name: str, course_number: int, group_name: str) -> int:
    ensure_courses(conn)
    faculty_id = upsert_faculty(conn, faculty_code, faculty_name, faculty_code.upper())
    course = conn.execute(
        "SELECT course_id FROM Courses WHERE course_number = :course_number",
        {"course_number": int(course_number)},
    ).fetchone()
    existing = conn.execute(
        "SELECT group_id FROM Groups WHERE group_name = :group_name",
        {"group_name": group_name},
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE Groups
            SET faculty_id = :faculty_id,
                course_id = :course_id
            WHERE group_id = :group_id
            """,
            {
                "faculty_id": faculty_id,
                "course_id": course["course_id"],
                "group_id": existing["group_id"],
            },
        )
        return existing["group_id"]

    cursor = conn.execute(
        """
        INSERT INTO Groups (faculty_id, course_id, group_name)
        VALUES (:faculty_id, :course_id, :group_name)
        """,
        {"faculty_id": faculty_id, "course_id": course["course_id"], "group_name": group_name},
    )
    return cursor.lastrowid


def seed_reference_data(conn: sqlite3.Connection) -> None:
    ensure_courses(conn)
    faculties = {
        "iret": "Институт радиоэлектронной техники",
        "aspirantura": "Аспирантура",
        "pish": 'Передовая инженерная школа "Электронное приборостроение и системы связи" им. А.В. Кобзева',
        "fvs": "Факультет вычислительных систем",
        "fsu": "Факультет систем управления",
        "fit": "Факультет инновационных технологий",
        "ef": "Экономический факультет",
        "gf": "Гуманитарный факультет",
        "yuf": "Юридический факультет",
        "fb": "Факультет безопасности",
        "fdo": "Факультет дистанционного обучения",
    }
    for code, name in faculties.items():
        upsert_faculty(conn, code, name, code.upper())

    upsert_group(conn, "fvs", faculties["fvs"], 4, "592-1")


def seed_demo_schedule(conn: sqlite3.Connection, group_id: int) -> None:
    lesson_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM Weekly_Schedule ws
        JOIN Daily_Schedule ds ON ds.schedule_id = ws.schedule_id
        WHERE ws.group_id = :group_id
        """,
        {"group_id": group_id},
    ).fetchone()["count"]
    if lesson_count:
        return

    schedule_id = conn.execute(
        """
        INSERT INTO Weekly_Schedule (group_id, week_type)
        VALUES (:group_id, 'четная')
        """,
        {"group_id": group_id},
    ).lastrowid

    lessons = [
        (1, 1, "Базы данных", "Лекция", "214", "Иванов И.И.", "09:00", "10:30"),
        (1, 2, "Проектирование ИС", "Практика", "308", "Петров А.А.", "10:40", "12:10"),
        (2, 3, "Сети и телекоммуникации", "Лабораторная", "112", "Сидоров П.П.", "13:00", "14:30"),
        (3, 2, "Web-разработка", "Практика", "221", "Козлова Е.В.", "10:40", "12:10"),
        (4, 4, "Физическая культура", "Практика", "Спортзал", "Орлов Д.С.", "14:40", "16:10"),
    ]
    conn.executemany(
        """
        INSERT INTO Daily_Schedule
            (schedule_id, day_number, lesson_number, discipline, lesson_type,
             auditorium, teacher_name, start_time, end_time)
        VALUES
            (:schedule_id, :day_number, :lesson_number, :discipline, :lesson_type,
             :auditorium, :teacher_name, :start_time, :end_time)
        """,
        [
            {
                "schedule_id": schedule_id,
                "day_number": day_number,
                "lesson_number": lesson_number,
                "discipline": discipline,
                "lesson_type": lesson_type,
                "auditorium": auditorium,
                "teacher_name": teacher_name,
                "start_time": start_time,
                "end_time": end_time,
            }
            for day_number, lesson_number, discipline, lesson_type, auditorium, teacher_name, start_time, end_time in lessons
        ],
    )


def _today(offset: int = 0) -> str:
    return (datetime.now(APP_TZ).date() + timedelta(days=offset)).isoformat()


def initialize_user_account(conn: sqlite3.Connection, user_id: int, user: dict[str, Any], *, demo: bool = False) -> None:
    seed_reference_data(conn)
    group = conn.execute("SELECT group_id FROM Groups WHERE group_name = '592-1'").fetchone()
    group_id = group["group_id"] if demo and group else None

    if not conn.execute("SELECT profile_id FROM Student_Profile WHERE user_id = :user_id", {"user_id": user_id}).fetchone():
        conn.execute(
            """
            INSERT INTO Student_Profile
                (user_id, full_name, group_id, email, phone, bio, specialization)
            VALUES
                (:user_id, :full_name, :group_id, :email, :phone, :bio, :specialization)
            """,
            {
                "user_id": user_id,
                "full_name": user.get("full_name") or "",
                "group_id": group_id,
                "email": user.get("email") or "student@example.com",
                "phone": "+7 (900) 123-45-67" if demo else None,
                "bio": (
                    "Студент, интересующийся разработкой web-приложений, "
                    "проектированием баз данных и пользовательскими интерфейсами."
                ) if demo else None,
                "specialization": "Информационные системы" if demo else None,
            },
        )

    if not conn.execute("SELECT setting_id FROM App_Settings WHERE user_id = :user_id", {"user_id": user_id}).fetchone():
        conn.execute(
            """
            INSERT INTO App_Settings
                (user_id, selected_group_id, selected_week_type, theme, notifications_enabled,
                 language, date_format, auto_update_schedule, lesson_reminder_minutes)
            VALUES
                (:user_id, :group_id, 'числитель', 'light', 1, 'ru', 'DD.MM.YYYY', 1, 15)
            """,
            {"user_id": user_id, "group_id": group_id},
        )


def seed_user_data(conn: sqlite3.Connection, user_id: int, user: dict[str, Any]) -> None:
    initialize_user_account(conn, user_id, user, demo=True)
    group = conn.execute("SELECT group_id FROM Groups WHERE group_name = '592-1'").fetchone()
    seed_demo_schedule(conn, group["group_id"])

    seed_finance(conn, user_id)
    seed_workouts(conn, user_id)
    seed_planner(conn, user_id)
    seed_portfolio(conn, user_id)


def seed_finance(conn: sqlite3.Connection, user_id: int) -> None:
    if not conn.execute("SELECT account_id FROM Accounts WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        conn.executemany(
            """
            INSERT INTO Accounts (user_id, account_name, account_type, balance, currency, is_active)
            VALUES (:user_id, :account_name, :account_type, :balance, 'RUB', 1)
            """,
            [
                {"user_id": user_id, "account_name": "Сбербанк", "account_type": "карта", "balance": 12300},
                {"user_id": user_id, "account_name": "Наличные", "account_type": "наличные", "balance": 2150},
                {"user_id": user_id, "account_name": "Накопительный счет", "account_type": "счет", "balance": 4000},
            ],
        )

    if not conn.execute("SELECT category_id FROM Categories WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        conn.executemany(
            """
            INSERT INTO Categories (user_id, category_name, category_type, icon_name, color)
            VALUES (:user_id, :category_name, :category_type, :icon_name, :color)
            """,
            [
                {"user_id": user_id, "category_name": "Стипендия", "category_type": "income", "icon_name": "wallet", "color": "#3c388d"},
                {"user_id": user_id, "category_name": "Подработка", "category_type": "income", "icon_name": "briefcase", "color": "#9fc53a"},
                {"user_id": user_id, "category_name": "Продукты", "category_type": "expense", "icon_name": "cart", "color": "#652580"},
                {"user_id": user_id, "category_name": "Транспорт", "category_type": "expense", "icon_name": "bus", "color": "#36cce8"},
                {"user_id": user_id, "category_name": "Связь", "category_type": "expense", "icon_name": "phone", "color": "#8c3ba7"},
                {"user_id": user_id, "category_name": "Учеба", "category_type": "expense", "icon_name": "book", "color": "#3c388d"},
            ],
        )

    if not conn.execute("SELECT transaction_id FROM Transactions WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        account = conn.execute(
            "SELECT account_id FROM Accounts WHERE user_id = :user_id AND account_name = 'Сбербанк'",
            {"user_id": user_id},
        ).fetchone()
        categories = {
            row["category_name"]: row["category_id"]
            for row in conn.execute("SELECT category_id, category_name FROM Categories WHERE user_id = :user_id", {"user_id": user_id})
        }
        conn.executemany(
            """
            INSERT INTO Transactions
                (user_id, account_id, category_id, transaction_type, amount, description, transaction_date)
            VALUES
                (:user_id, :account_id, :category_id, :transaction_type, :amount, :description, :transaction_date)
            """,
            [
                {
                    "user_id": user_id,
                    "account_id": account["account_id"],
                    "category_id": categories["Стипендия"],
                    "transaction_type": "income",
                    "amount": 3500,
                    "description": "Ежемесячная выплата",
                    "transaction_date": _today(-1),
                },
                {
                    "user_id": user_id,
                    "account_id": account["account_id"],
                    "category_id": categories["Продукты"],
                    "transaction_type": "expense",
                    "amount": 890,
                    "description": "Супермаркет",
                    "transaction_date": _today(),
                },
                {
                    "user_id": user_id,
                    "account_id": account["account_id"],
                    "category_id": categories["Транспорт"],
                    "transaction_type": "expense",
                    "amount": 120,
                    "description": "Проезд",
                    "transaction_date": _today(-2),
                },
            ],
        )


def seed_workouts(conn: sqlite3.Connection, user_id: int) -> None:
    for type_name in ["Силовая", "Кардио", "Растяжка"]:
        conn.execute("INSERT OR IGNORE INTO Workout_Types (type_name) VALUES (:type_name)", {"type_name": type_name})

    exercises = [
        ("Подтягивания", "Спина", "Силовое"),
        ("Тяга штанги в наклоне", "Спина", "Силовое"),
        ("Жим гантелей сидя", "Плечи", "Силовое"),
        ("Кардио", "Выносливость", "Кардио"),
        ("Приседания", "Ноги", "Силовое"),
        ("Жим лежа", "Грудь", "Силовое"),
    ]
    for exercise_name, muscle_group, exercise_type in exercises:
        if not conn.execute("SELECT exercise_id FROM Exercises WHERE exercise_name = :exercise_name", {"exercise_name": exercise_name}).fetchone():
            conn.execute(
                """
                INSERT INTO Exercises (exercise_name, muscle_group, exercise_type)
                VALUES (:exercise_name, :muscle_group, :exercise_type)
                """,
                {"exercise_name": exercise_name, "muscle_group": muscle_group, "exercise_type": exercise_type},
            )

    if conn.execute("SELECT plan_id FROM Workout_Plans WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        return

    workout_type = conn.execute("SELECT workout_type_id FROM Workout_Types WHERE type_name = 'Силовая'").fetchone()
    plan_ids = []
    for plan_name, day_number, description in [
        ("Грудь и трицепс", 1, "Базовая силовая тренировка"),
        ("Ноги", 3, "Нагрузка на ноги и корпус"),
        ("Спина и плечи", 5, "Тяговые упражнения и плечи"),
    ]:
        plan_ids.append(
            conn.execute(
                """
                INSERT INTO Workout_Plans
                    (user_id, plan_name, workout_type_id, day_number, description)
                VALUES
                    (:user_id, :plan_name, :workout_type_id, :day_number, :description)
                """,
                {
                    "user_id": user_id,
                    "plan_name": plan_name,
                    "workout_type_id": workout_type["workout_type_id"],
                    "day_number": day_number,
                    "description": description,
                },
            ).lastrowid
        )

    main_plan_id = plan_ids[-1]
    for order, (exercise_name, sets_count, reps_count, duration_minutes) in enumerate(
        [
            ("Подтягивания", 4, 10, None),
            ("Тяга штанги в наклоне", 4, 12, None),
            ("Жим гантелей сидя", 3, 12, None),
            ("Кардио", None, None, 15),
        ],
        start=1,
    ):
        exercise = conn.execute("SELECT exercise_id FROM Exercises WHERE exercise_name = :exercise_name", {"exercise_name": exercise_name}).fetchone()
        conn.execute(
            """
            INSERT INTO Plan_Exercises
                (user_id, plan_id, exercise_id, sets_count, reps_count, duration_minutes, exercise_order)
            VALUES
                (:user_id, :plan_id, :exercise_id, :sets_count, :reps_count, :duration_minutes, :exercise_order)
            """,
            {
                "user_id": user_id,
                "plan_id": main_plan_id,
                "exercise_id": exercise["exercise_id"],
                "sets_count": sets_count,
                "reps_count": reps_count,
                "duration_minutes": duration_minutes,
                "exercise_order": order,
            },
        )

    for workout_date, duration_minutes, calories_burned, notes in [
        (_today(-1), 65, 420, "Самочувствие хорошее"),
        (_today(-3), 70, 460, "Стабильный темп"),
        (_today(-6), 30, 250, "Легкая кардио-сессия"),
    ]:
        conn.execute(
            """
            INSERT INTO Workout_Logs
                (user_id, plan_id, workout_date, duration_minutes, calories_burned, notes)
            VALUES
                (:user_id, :plan_id, :workout_date, :duration_minutes, :calories_burned, :notes)
            """,
            {
                "user_id": user_id,
                "plan_id": main_plan_id,
                "workout_date": workout_date,
                "duration_minutes": duration_minutes,
                "calories_burned": calories_burned,
                "notes": notes,
            },
        )


def seed_planner(conn: sqlite3.Connection, user_id: int) -> None:
    for category_name in ["Учеба", "Финансы", "Спорт", "Личное"]:
        conn.execute(
            "INSERT OR IGNORE INTO Planner_Categories (category_name) VALUES (:category_name)",
            {"category_name": category_name},
        )

    if not conn.execute("SELECT task_id FROM Tasks WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        categories = {
            row["category_name"]: row["planner_category_id"]
            for row in conn.execute("SELECT planner_category_id, category_name FROM Planner_Categories")
        }
        for category_name, title, description, priority, status, due_date in [
            ("Учеба", "Подготовить отчет по практике", "Собрать материалы и оформить выводы", "high", "in_progress", _today(1)),
            ("Финансы", "Оплатить интернет", "Плановый платеж", "medium", "planned", _today(2)),
            ("Спорт", "Сходить в зал", "Тренировка по плану", "medium", "planned", _today(1)),
            ("Личное", "Сделать резервную копию проекта", "Сохранить исходники и БД", "low", "planned", _today(3)),
        ]:
            conn.execute(
                """
                INSERT INTO Tasks
                    (user_id, planner_category_id, title, description, priority, status, due_date)
                VALUES
                    (:user_id, :planner_category_id, :title, :description, :priority, :status, :due_date)
                """,
                {
                    "user_id": user_id,
                    "planner_category_id": categories[category_name],
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "status": status,
                    "due_date": due_date,
                },
            )

    if not conn.execute("SELECT event_id FROM Events WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        study_category = conn.execute(
            "SELECT planner_category_id FROM Planner_Categories WHERE category_name = 'Учеба'"
        ).fetchone()
        for title, event_date, start_time, end_time, location in [
            ("Консультация по диплому", _today(), "16:00", "17:00", "ТУСУР"),
            ("Семинар по БД", _today(3), "14:30", "16:00", "Ауд. 214"),
            ("Защита отчета", _today(6), "10:00", "11:30", "Кафедра"),
        ]:
            conn.execute(
                """
                INSERT INTO Events
                    (user_id, planner_category_id, title, description, event_date, start_time, end_time, location)
                VALUES
                    (:user_id, :planner_category_id, :title, '', :event_date, :start_time, :end_time, :location)
                """,
                {
                    "user_id": user_id,
                    "planner_category_id": study_category["planner_category_id"],
                    "title": title,
                    "event_date": event_date,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": location,
                },
            )

    if not conn.execute("SELECT note_id FROM Notes WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        conn.execute(
            """
            INSERT INTO Notes (user_id, title, content)
            VALUES
                (:user_id, 'Идеи для дипломного проекта',
                 'Добавить экран статистики по учебной нагрузке, улучшить фильтры в финансах, продумать экспорт расписания.')
            """,
            {"user_id": user_id},
        )


def seed_portfolio(conn: sqlite3.Connection, user_id: int) -> None:
    for category_name in ["Учебные проекты", "Личные проекты", "Сертификаты", "Достижения"]:
        conn.execute(
            "INSERT OR IGNORE INTO Portfolio_Categories (category_name) VALUES (:category_name)",
            {"category_name": category_name},
        )

    if not conn.execute("SELECT skill_id FROM Portfolio_Skills WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        for skill_name, category, level in [
            ("HTML/CSS", "Frontend", 82),
            ("JavaScript", "Frontend", 78),
            ("SQL", "Базы данных", 84),
            ("SQLite", "Базы данных", 80),
            ("Python", "Backend", 74),
            ("FastAPI", "Backend", 79),
            ("Git", "Инструменты", 76),
            ("UI-дизайн", "Дизайн", 72),
            ("Документация", "Проектирование", 80),
        ]:
            conn.execute(
                """
                INSERT INTO Portfolio_Skills (user_id, skill_name, category, level)
                VALUES (:user_id, :skill_name, :category, :level)
                """,
                {"user_id": user_id, "skill_name": skill_name, "category": category, "level": level},
            )

    if not conn.execute("SELECT project_id FROM Portfolio_Projects WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        category = conn.execute(
            "SELECT portfolio_category_id FROM Portfolio_Categories WHERE category_name = 'Учебные проекты'"
        ).fetchone()
        for title, project_type, technologies, start_date, end_date, status, result_text in [
            ("MultiApp для студентов", "дипломный", "Python, FastAPI, SQLite, HTML/CSS", "2026-01-15", None, "in_progress", "Модули расписания, финансов, тренировок, ежедневника и портфолио"),
            ("Money Tracker", "личный", "FastAPI, React, PostgreSQL", "2025-04-01", "2025-08-20", "completed", "Учет финансов и аналитика"),
            ("VideoTeka", "учебный", "C#, WPF, SQLite", "2025-02-10", "2025-05-30", "completed", "Каталог фильмов и прокат"),
        ]:
            conn.execute(
                """
                INSERT INTO Portfolio_Projects
                    (user_id, portfolio_category_id, title, project_type, technologies, description,
                     start_date, end_date, status, result_text, repository_url, project_url)
                VALUES
                    (:user_id, :category_id, :title, :project_type, :technologies, :description,
                     :start_date, :end_date, :status, :result_text, '', '')
                """,
                {
                    "user_id": user_id,
                    "category_id": category["portfolio_category_id"],
                    "title": title,
                    "project_type": project_type,
                    "technologies": technologies,
                    "description": result_text,
                    "start_date": start_date,
                    "end_date": end_date,
                    "status": status,
                    "result_text": result_text,
                },
            )

    if not conn.execute("SELECT achievement_id FROM Portfolio_Achievements WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        category = conn.execute(
            "SELECT portfolio_category_id FROM Portfolio_Categories WHERE category_name = 'Достижения'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO Portfolio_Achievements
                (user_id, portfolio_category_id, title, achievement_type, issuer, achievement_date, description)
            VALUES
                (:user_id, :category_id, 'Участие в хакатоне по разработке ИС',
                 'участие', 'ТУСУР', '2025-11-20', 'Командная разработка прототипа информационной системы')
            """,
            {"user_id": user_id, "category_id": category["portfolio_category_id"]},
        )

    if not conn.execute("SELECT certificate_id FROM Portfolio_Certificates WHERE user_id = :user_id LIMIT 1", {"user_id": user_id}).fetchone():
        category = conn.execute(
            "SELECT portfolio_category_id FROM Portfolio_Categories WHERE category_name = 'Сертификаты'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO Portfolio_Certificates
                (user_id, portfolio_category_id, title, organization, issue_date, certificate_number, description, file_path)
            VALUES
                (:user_id, :category_id, 'Сертификат по SQL и базам данных',
                 'Образовательная платформа', '2025-06-10', 'SQL-2025-001',
                 'Курс по проектированию и запросам SQL', '')
            """,
            {"user_id": user_id, "category_id": category["portfolio_category_id"]},
        )


def ensure_demo_user(conn: sqlite3.Connection) -> None:
    demo_email = "student@example.com"
    user = get_user_by_email(demo_email, conn)
    if not user:
        user = create_user(conn, "Дмитрий Иванов", demo_email, "student123")
    public = public_user(user["user_id"], conn)
    seed_user_data(conn, public["user_id"], public)

    admin_email = "admin@example.com"
    admin = get_user_by_email(admin_email, conn)
    if not admin:
        conn.execute(
            """
            INSERT INTO Users (full_name, email, password_hash, is_admin)
            VALUES ('Администратор расписания', :email, :password_hash, 1)
            """,
            {"email": admin_email, "password_hash": hash_password("admin123")},
        )
    else:
        conn.execute(
            "UPDATE Users SET is_admin = 1 WHERE user_id = :user_id",
            {"user_id": admin["user_id"]},
        )


def init_db() -> None:
    conn = connect()
    try:
        migrate(conn)
        seed_reference_data(conn)
        ensure_demo_user(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
