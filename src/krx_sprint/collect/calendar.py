"""수집 대상 일자 판정

증분 로직(스펙 §7.3)의 "누락 일자만 수집" 판정과, 당일 미확정 데이터를 막는
확정 시각 게이트를 담당한다.

확정 시각 게이트의 근거: 조회가 성공해도 장중 미확정 값일 수 있음이 실측으로 확인됐다(스펙 §0).
"""

from collections.abc import Container
from datetime import date, datetime, timedelta

from krx_sprint.common_constants import COLLECTION_START_DATE, KST, SNAPSHOT_CONFIRM_HOUR_KST

# 주말 시작 요일 (월=0 기준, 토=5)
WEEKEND_START_WEEKDAY = 5


def resolve_last_collectable_date(now: datetime, confirm_hour: int = SNAPSHOT_CONFIRM_HOUR_KST) -> date:
    """수집 가능한 마지막 일자를 판정한다.

    확정 시각 이전이면 당일 데이터가 미확정일 수 있으므로 전날까지만 대상으로 삼는다.

    Args:
        now: 현재 시각 (타임존 정보를 가진 datetime)
        confirm_hour: 당일 확정으로 간주하는 KST 기준 시각 (0~23)

    Returns:
        수집 가능한 마지막 일자

    Raises:
        ValueError: 타임존이 없는 시각이거나 확정 시각이 0~23 범위를 벗어난 경우
    """
    if now.tzinfo is None:
        raise ValueError("타임존이 없는 시각은 사용할 수 없습니다 (KST 기준 aware datetime 필요)")

    if not 0 <= confirm_hour <= 23:
        raise ValueError(f"확정 시각은 0~23이어야 합니다: {confirm_hour}")

    now_kst = now.astimezone(KST)
    if now_kst.hour >= confirm_hour:
        return now_kst.date()

    return now_kst.date() - timedelta(days=1)


def list_target_dates(
    start: date,
    end: date,
    collected: Container[date],
    holidays: Container[date],
) -> list[date]:
    """수집해야 할 일자를 오름차순으로 산출한다.

    주말, 이미 저장된 일자(체크포인트), 휴장으로 기록된 일자를 제외한다.

    Args:
        start: 조회 시작 일자
        end: 조회 종료 일자 (포함)
        collected: 이미 수집 완료된 일자
        holidays: 휴장으로 기록된 일자

    Returns:
        수집 대상 일자 목록 (오름차순)

    Raises:
        ValueError: 시작일이 수집 시작일보다 이르거나 종료일보다 늦은 경우
    """
    if start < COLLECTION_START_DATE:
        raise ValueError(f"수집 시작일({COLLECTION_START_DATE}) 이전은 수집하지 않습니다: {start}")

    if start > end:
        raise ValueError(f"시작일이 종료일보다 늦습니다: {start} > {end}")

    targets: list[date] = []
    current = start
    while current <= end:
        is_weekday = current.weekday() < WEEKEND_START_WEEKDAY
        if is_weekday and current not in collected and current not in holidays:
            targets.append(current)
        current += timedelta(days=1)

    return targets
