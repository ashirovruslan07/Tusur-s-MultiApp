from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from tusur_timetable import FACULTIES, FACULTY_CODES, TusurTimetableClient


def schedule_to_dict(schedule) -> dict:
    return {
        "faculty": schedule.faculty,
        "group": schedule.group,
        "week": asdict(schedule.week),
        "lessons": [asdict(lesson) for lesson in schedule.lessons],
    }


def emit(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge for TUSUR timetable parser")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("faculties")

    groups_parser = subparsers.add_parser("groups")
    groups_parser.add_argument("--faculty", default="fvs")

    all_groups_parser = subparsers.add_parser("all-groups")
    all_groups_parser.add_argument("--faculty", action="append")

    schedule_parser = subparsers.add_parser("schedule")
    schedule_parser.add_argument("--faculty", default="fvs")
    schedule_parser.add_argument("--group", default="533-2")
    schedule_parser.add_argument("--week-id")

    args = parser.parse_args()
    client = TusurTimetableClient(timeout=25)

    if args.command == "faculties":
        fetched = client.fetch_faculties()
        emit({"faculties": fetched or FACULTIES})
        return 0

    if args.command == "groups":
        emit(
            {
                "faculty": args.faculty,
                "faculty_name": FACULTIES.get(args.faculty, args.faculty),
                "groups": client.fetch_groups_for_faculty(args.faculty),
            }
        )
        return 0

    if args.command == "all-groups":
        faculty_codes = args.faculty or list(FACULTY_CODES)
        emit({"groups": client.fetch_all_groups(faculty_codes)})
        return 0

    if args.command == "schedule":
        week_id = args.week_id if args.week_id not in (None, "") else None
        emit(schedule_to_dict(client.fetch_schedule(args.faculty, args.group, week_id)))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
