"""수집 메타 저장소

휴장 일자(`holidays.json`)와 실패 일자(`failures.json`)를 관리한다 (스펙 §7.2·§7.3).

휴장 기록은 재조회를 막는 캐시 역할을 하므로, 일시적 조회 실패를 휴장으로 기록하면
해당 일자가 영구히 누락된다. 휴장 기록은 재시도를 모두 소진한 뒤에만 수행한다.
"""

import json
from datetime import date, datetime
from pathlib import Path

# 메타 파일에 기록하는 일자 형식
DATE_FORMAT = "%Y%m%d"


def _write_json(path: Path, payload: list[str] | dict[str, str]) -> None:
    """JSON 파일을 저장한다 (상위 디렉토리 자동 생성).

    Args:
        path: 저장 경로
        payload: 직렬화할 데이터
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)


def _parse_date(raw: str, path: Path) -> date:
    """메타 파일의 일자 문자열을 파싱한다.

    Args:
        raw: 일자 문자열
        path: 예외 메시지에 사용할 파일 경로

    Returns:
        파싱된 일자

    Raises:
        ValueError: 형식이 맞지 않는 경우
    """
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except ValueError as error:
        raise ValueError(f"메타 파일의 일자 형식이 올바르지 않습니다 ({path}): {raw!r}") from error


def load_holidays(path: Path) -> set[date]:
    """휴장으로 기록된 일자를 읽는다.

    Args:
        path: `holidays.json` 경로

    Returns:
        휴장 일자 집합 (파일이 없으면 빈 집합)
    """
    if not path.exists():
        return set()

    with path.open("r", encoding="utf-8") as file:
        raw_dates: list[str] = json.load(file)

    return {_parse_date(raw, path) for raw in raw_dates}


def record_holiday(path: Path, target: date) -> None:
    """일자를 휴장으로 기록한다 (중복 기록해도 한 건으로 유지된다).

    Args:
        path: `holidays.json` 경로
        target: 휴장으로 판정된 일자
    """
    holidays = load_holidays(path)
    holidays.add(target)
    _write_json(path, [holiday.strftime(DATE_FORMAT) for holiday in sorted(holidays)])


def load_failures(path: Path) -> dict[date, str]:
    """수집 실패로 기록된 일자와 사유를 읽는다.

    Args:
        path: `failures.json` 경로

    Returns:
        일자 → 실패 사유 (파일이 없으면 빈 dict)
    """
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        raw_failures: dict[str, str] = json.load(file)

    return {_parse_date(raw, path): reason for raw, reason in raw_failures.items()}


def record_failure(path: Path, target: date, reason: str) -> None:
    """일자를 수집 실패로 기록한다 (재수집 대상).

    Args:
        path: `failures.json` 경로
        target: 실패한 일자
        reason: 실패 사유
    """
    failures = load_failures(path)
    failures[target] = reason
    _write_json(
        path,
        {failed.strftime(DATE_FORMAT): failures[failed] for failed in sorted(failures)},
    )


def remove_failure(path: Path, target: date) -> None:
    """재수집에 성공한 일자를 실패 목록에서 제거한다.

    기록에 없는 일자를 제거해도 예외를 발생시키지 않는다.

    Args:
        path: `failures.json` 경로
        target: 제거할 일자
    """
    failures = load_failures(path)
    if target not in failures:
        return

    del failures[target]
    _write_json(
        path,
        {failed.strftime(DATE_FORMAT): failures[failed] for failed in sorted(failures)},
    )
