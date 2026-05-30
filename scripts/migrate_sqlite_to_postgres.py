from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server import database as db


TABLES = [
    "Users",
    "Sessions",
    "Faculties",
    "Courses",
    "Groups",
    "Student_Profile",
    "App_Settings",
    "Schedule_Weeks",
    "Weekly_Schedule",
    "Daily_Schedule",
    "Accounts",
    "Categories",
    "Transactions",
    "Transfers",
    "Workout_Types",
    "Exercises",
    "Workout_Plans",
    "Plan_Exercises",
    "Workout_Logs",
    "Workout_Log_Exercises",
    "Planner_Categories",
    "Tasks",
    "Events",
    "Notes",
    "Portfolio_Categories",
    "Portfolio_Projects",
    "Portfolio_Achievements",
    "Portfolio_Certificates",
    "Portfolio_Files",
    "Portfolio_Skills",
    "Schedule_Sync_Settings",
    "Schedule_Sync_Log",
]


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def reset_identity(pg_conn, table_name: str, pk_field: str) -> None:
    pg_conn.execute(
        sql.SQL(
            """
            SELECT setval(
                pg_get_serial_sequence({table_literal}, {pk_literal}),
                GREATEST(COALESCE(MAX({pk}), 1), 1),
                COALESCE(MAX({pk}), 0) > 0
            )
            FROM {table}
            """
        ).format(
            table_literal=sql.Literal(table_name.lower()),
            pk_literal=sql.Literal(pk_field.lower()),
            pk=sql.Identifier(pk_field.lower()),
            table=sql.Identifier(table_name.lower()),
        )
    )


def copy_table(sqlite_conn: sqlite3.Connection, pg_conn, table_name: str) -> int:
    rows = sqlite_conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
    if not rows:
        return 0

    columns = list(rows[0].keys())
    insert_sql = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
        table=sql.Identifier(table_name.lower()),
        columns=sql.SQL(", ").join(sql.Identifier(column.lower()) for column in columns),
        values=sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
    )
    pg_conn.cursor().executemany(insert_sql, [dict(row) for row in rows])

    pk_field = db.PK_FIELDS.get(table_name)
    if pk_field and pk_field in columns:
        reset_identity(pg_conn, table_name, pk_field)
    return len(rows)


def main() -> int:
    sqlite_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MULTIAPP_SQLITE_PATH", "/app/data/multi_app.sqlite"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL не задан. Запустите скрипт внутри контейнера backend или задайте переменную окружения.")
        return 1
    if not sqlite_path.exists():
        print(f"SQLite-файл не найден: {sqlite_path}")
        return 1

    db.init_db()

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    source_tables = sqlite_tables(sqlite_conn)

    with psycopg.connect(database_url) as pg_conn:
        pg_conn.execute(
            sql.SQL("TRUNCATE {tables} RESTART IDENTITY CASCADE").format(
                tables=sql.SQL(", ").join(sql.Identifier(table.lower()) for table in TABLES)
            )
        )

        copied: dict[str, int] = {}
        for table_name in TABLES:
            if table_name not in source_tables:
                continue
            copied[table_name] = copy_table(sqlite_conn, pg_conn, table_name)

        pg_conn.commit()

    with db.transaction() as conn:
        db.backfill_schedule_weeks(conn)

    total = sum(copied.values())
    print(f"Перенос завершен. Скопировано строк: {total}")
    for table_name, count in copied.items():
        print(f"{table_name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
