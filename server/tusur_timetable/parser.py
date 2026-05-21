from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from .faculties import FACULTY_CODES


BASE_URL = "https://timetable.tusur.ru"

MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


@dataclass(frozen=True)
class WeekInfo:
    week_id: int | None
    week_number: int | None
    week_type: str
    starts_at: date | None = None


@dataclass(frozen=True)
class Lesson:
    day_number: int
    lesson_number: int
    discipline: str
    lesson_type: str
    auditorium: str | None = None
    teacher_name: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True)
class Schedule:
    faculty: str
    group: str
    week: WeekInfo
    lessons: tuple[Lesson, ...]

    def by_day(self) -> dict[int, list[Lesson]]:
        result: dict[int, list[Lesson]] = {}
        for lesson in self.lessons:
            result.setdefault(lesson.day_number, []).append(lesson)
        return result


class TusurTimetableClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_faculties(self) -> dict[str, str]:
        soup = self._get_soup("/faculties")
        faculties: dict[str, str] = {}

        for link in soup.find_all("a", href=True):
            href = link["href"]
            match = re.fullmatch(r"/faculties/([^/]+)", href)
            name = link.get_text(" ", strip=True)
            if match and name:
                faculties[match.group(1)] = name

        return faculties

    def fetch_groups_for_faculty(self, faculty_code: str) -> dict[int, list[str]]:
        soup = self._get_soup(f"/faculties/{faculty_code}")
        groups_by_course: dict[int, list[str]] = {}

        for group_list in soup.find_all("ul", class_="list-inline"):
            course_header = group_list.find_previous("h2")
            if course_header is None:
                continue

            match = re.search(r"\d+", course_header.get_text(" ", strip=True))
            if match is None:
                continue

            course_number = int(match.group())
            groups = [
                link.get_text(strip=True)
                for link in group_list.find_all("a", href=True)
                if link.get_text(strip=True)
            ]
            groups_by_course.setdefault(course_number, []).extend(groups)

        return groups_by_course

    def fetch_all_groups(
        self,
        faculty_codes: Iterable[str] = FACULTY_CODES,
    ) -> dict[str, dict[int, list[str]]]:
        return {
            faculty_code: self.fetch_groups_for_faculty(faculty_code)
            for faculty_code in faculty_codes
        }

    def fetch_current_week(self, faculty_code: str, group_name: str) -> WeekInfo:
        soup = self._get_schedule_soup(faculty_code, group_name)
        return _parse_week_info(soup)

    def fetch_schedule(
        self,
        faculty_code: str,
        group_name: str,
        week_id: int | str | None = None,
    ) -> Schedule:
        soup = self._get_schedule_soup(faculty_code, group_name, week_id)
        week = _parse_week_info(soup)
        lessons = _parse_lessons(soup)

        return Schedule(
            faculty=faculty_code,
            group=group_name,
            week=week,
            lessons=tuple(lessons),
        )

    def _get_schedule_soup(
        self,
        faculty_code: str,
        group_name: str,
        week_id: int | str | None = None,
    ) -> BeautifulSoup:
        path = f"/faculties/{faculty_code}/groups/{group_name}"
        params = {"week_id": str(week_id)} if week_id is not None else None
        return self._get_soup(path, params=params)

    def _get_soup(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> BeautifulSoup:
        response = self.session.get(
            urljoin(self.base_url + "/", path.lstrip("/")),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        apparent_encoding = response.apparent_encoding
        if apparent_encoding and apparent_encoding.lower() not in {"ascii", response.encoding.lower() if response.encoding else ""}:
            response.encoding = apparent_encoding
        elif response.encoding is None:
            response.encoding = "utf-8"
        return BeautifulSoup(response.text, "html.parser")


def _parse_week_info(soup: BeautifulSoup) -> WeekInfo:
    current_week = soup.select_one(".current-week")
    if current_week is None:
        return WeekInfo(week_id=None, week_number=None, week_type="обычная")

    text = current_week.get_text(" ", strip=True)
    week_id = _parse_week_id(current_week)
    week_number = _parse_first_int(text)
    week_type = _normalize_week_type(text)
    starts_at = _parse_start_date(text)
    return WeekInfo(
        week_id=week_id,
        week_number=week_number,
        week_type=week_type,
        starts_at=starts_at,
    )


def _parse_week_id(current_week: Tag) -> int | None:
    link = current_week.find("a", href=True)
    if link is None:
        return None

    query = parse_qs(urlparse(link["href"]).query)
    raw_week_id = query.get("week_id", [None])[0]
    return int(raw_week_id) if raw_week_id and raw_week_id.isdigit() else None


def _parse_first_int(text: str) -> int | None:
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _normalize_week_type(text: str) -> str:
    normalized = text.lower().replace("ё", "е")
    if "нечет" in normalized:
        return "нечетная"
    if "чет" in normalized:
        return "четная"
    return "обычная"


def _parse_start_date(text: str) -> date | None:
    match = re.search(
        r"с\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})",
        text.lower(),
    )
    if match is None:
        return None

    day = int(match.group(1))
    month = MONTHS_RU.get(match.group(2).replace("ё", "е"))
    year = int(match.group(3))
    if month is None:
        return None

    return date(year, month, day)


def _parse_lessons(soup: BeautifulSoup) -> list[Lesson]:
    lessons: list[Lesson] = []
    seen: set[tuple[int, int, str, str, str | None, str | None]] = set()

    for row in soup.find_all(
        lambda tag: tag.name == "tr" and _has_lesson_class(tag.get("class", []))
    ):
        lesson_number = _lesson_number_from_classes(row.get("class", []))
        if lesson_number is None:
            continue

        start_time, end_time = _parse_time(row)

        for day_cell in row.find_all(
            lambda tag: tag.name == "td"
            and _has_lesson_cell_class(tag.get("class", []))
        ):
            day_number = _day_number_from_classes(day_cell.get("class", []))
            if day_number is None:
                continue

            wrapper = day_cell.find(class_="lessons-wrapper") or day_cell
            for cell in wrapper.find_all(class_="lesson-cell"):
                lesson = _parse_lesson_cell(
                    cell=cell,
                    day_number=day_number,
                    lesson_number=lesson_number,
                    start_time=start_time,
                    end_time=end_time,
                )
                if lesson is None:
                    continue

                key = (
                    lesson.day_number,
                    lesson.lesson_number,
                    lesson.discipline,
                    lesson.lesson_type,
                    lesson.auditorium,
                    lesson.teacher_name,
                )
                if key in seen:
                    continue
                seen.add(key)
                lessons.append(lesson)

    return sorted(lessons, key=lambda item: (item.day_number, item.lesson_number))


def _has_lesson_class(value: object) -> bool:
    classes = _class_list(value)
    return any(re.fullmatch(r"lesson_\d+", class_name) for class_name in classes)


def _has_lesson_cell_class(value: object) -> bool:
    classes = _class_list(value)
    return "lesson_cell" in classes and any(
        re.fullmatch(r"day_\d+", class_name) for class_name in classes
    )


def _class_list(value: object) -> list[str]:
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _lesson_number_from_classes(classes: Iterable[str]) -> int | None:
    for class_name in classes:
        match = re.fullmatch(r"lesson_(\d+)", class_name)
        if match:
            return int(match.group(1))
    return None


def _day_number_from_classes(classes: Iterable[str]) -> int | None:
    for class_name in classes:
        match = re.fullmatch(r"day_(\d+)", class_name)
        if match:
            return int(match.group(1))
    return None


def _parse_time(row: Tag) -> tuple[str | None, str | None]:
    time_cell = row.find("th", class_="time")
    if time_cell is None:
        return None, None

    times = re.findall(r"\d{2}:\d{2}", time_cell.get_text(" ", strip=True))
    if len(times) < 2:
        return None, None
    return times[0], times[1]


def _parse_lesson_cell(
    cell: Tag,
    day_number: int,
    lesson_number: int,
    start_time: str | None,
    end_time: str | None,
) -> Lesson | None:
    discipline = _prefer_print_text(cell, "discipline")
    lesson_type = _prefer_print_text(cell, "kind")
    if not discipline or not lesson_type:
        return None

    auditorium = _prefer_print_text(cell, "auditoriums") or None
    teacher_name = _prefer_print_text(cell, "group") or None

    return Lesson(
        day_number=day_number,
        lesson_number=lesson_number,
        discipline=discipline,
        lesson_type=lesson_type,
        auditorium=auditorium,
        teacher_name=teacher_name,
        start_time=start_time,
        end_time=end_time,
    )


def _prefer_print_text(cell: Tag, class_name: str) -> str:
    print_node = cell.select_one(f".for_print .{class_name}")
    visible_node = cell.select_one(f".training .{class_name}") or cell.select_one(
        f".{class_name}"
    )
    return _clean_text(print_node or visible_node)


def _clean_text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
