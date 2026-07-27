"""calendar 테스트

수집 대상 일자 판정 규칙을 고정한다 (스펙 §7.3 + 당일 확정 시각 게이트).
"""

from datetime import date, datetime

import pytest

from krx_sprint.collect.calendar import list_target_dates, resolve_last_collectable_date
from krx_sprint.common_constants import KST


def _kst(year: int, month: int, day: int, hour: int) -> datetime:
    """KST 기준 aware datetime을 만든다."""
    return datetime(year, month, day, hour, tzinfo=KST)


class TestResolveLastCollectableDate:
    """당일 확정 시각 게이트 계약을 고정한다."""

    def test_includes_today_after_confirm_hour(self):
        """
        목적: 확정 시각 이후에는 당일까지 수집 대상에 포함한다.

        Given: 확정 시각(17시)을 넘긴 18시
        When: resolve_last_collectable_date 호출
        Then: 오늘 일자가 반환된다
        """
        # Given / When
        result = resolve_last_collectable_date(_kst(2026, 7, 27, 18), confirm_hour=17)

        # Then
        assert result == date(2026, 7, 27)

    def test_excludes_today_before_confirm_hour(self):
        """
        목적: 확정 시각 이전에는 당일을 제외한다 (장중 미확정 값 저장 차단).

        Given: 확정 시각 이전인 10시
        When: resolve_last_collectable_date 호출
        Then: 어제 일자가 반환된다
        """
        # Given / When
        result = resolve_last_collectable_date(_kst(2026, 7, 27, 10), confirm_hour=17)

        # Then
        assert result == date(2026, 7, 26)

    def test_includes_today_exactly_at_confirm_hour(self):
        """
        목적: 확정 시각 정각은 포함으로 판정한다 (경계 조건).

        Given: 정확히 17시
        When: resolve_last_collectable_date 호출
        Then: 오늘 일자가 반환된다
        """
        # Given / When
        result = resolve_last_collectable_date(_kst(2026, 7, 27, 17), confirm_hour=17)

        # Then
        assert result == date(2026, 7, 27)

    def test_rejects_naive_datetime(self):
        """
        목적: 타임존 없는 시각을 KST로 임의 해석하지 않는다.

        Given: naive datetime
        When: resolve_last_collectable_date 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="타임존"):
            resolve_last_collectable_date(datetime(2026, 7, 27, 18), confirm_hour=17)


class TestListTargetDates:
    """수집 대상 일자 산출 계약을 고정한다."""

    def test_excludes_weekends(self):
        """
        목적: 주말은 후보에서 제외한다.

        Given: 2026-07-24(금)~2026-07-27(월) 구간
        When: list_target_dates 호출
        Then: 토·일이 빠지고 금·월만 남는다
        """
        # Given / When
        result = list_target_dates(date(2026, 7, 24), date(2026, 7, 27), collected=set(), holidays=set())

        # Then
        assert result == [date(2026, 7, 24), date(2026, 7, 27)]

    def test_excludes_collected_dates(self):
        """
        목적: 이미 저장된 일자는 다시 수집하지 않는다 (파일 존재 = 체크포인트).

        Given: 첫 일자가 이미 수집된 상태
        When: list_target_dates 호출
        Then: 해당 일자가 빠진다
        """
        # Given / When
        result = list_target_dates(
            date(2026, 7, 27),
            date(2026, 7, 29),
            collected={date(2026, 7, 27)},
            holidays=set(),
        )

        # Then
        assert result == [date(2026, 7, 28), date(2026, 7, 29)]

    def test_excludes_known_holidays(self):
        """
        목적: 휴장으로 기록된 일자는 재조회하지 않는다.

        Given: 가운데 일자가 휴장으로 기록된 상태
        When: list_target_dates 호출
        Then: 해당 일자가 빠진다
        """
        # Given / When
        result = list_target_dates(
            date(2026, 7, 27),
            date(2026, 7, 29),
            collected=set(),
            holidays={date(2026, 7, 28)},
        )

        # Then
        assert result == [date(2026, 7, 27), date(2026, 7, 29)]

    def test_returns_ascending_order(self):
        """
        목적: 결과 순서를 오름차순으로 고정한다 (중단 재개 시 진행 방향 보장).

        Given: 한 주 구간
        When: list_target_dates 호출
        Then: 오름차순으로 정렬돼 있다
        """
        # Given / When
        result = list_target_dates(date(2026, 7, 27), date(2026, 7, 31), collected=set(), holidays=set())

        # Then
        assert result == sorted(result)

    def test_returns_empty_when_all_excluded(self):
        """
        목적: 남는 일자가 없으면 빈 리스트를 반환한다 (경계 조건).

        Given: 주말만으로 이루어진 구간
        When: list_target_dates 호출
        Then: 빈 리스트다
        """
        # Given / When
        result = list_target_dates(date(2026, 7, 25), date(2026, 7, 26), collected=set(), holidays=set())

        # Then
        assert result == []

    def test_rejects_start_before_collection_start(self):
        """
        목적: 수집 시작일 이전 요청을 거부한다 (스펙 §5 수집 기간).

        Given: 2018년 일자
        When: list_target_dates 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="수집 시작일"):
            list_target_dates(date(2018, 12, 31), date(2019, 1, 31), collected=set(), holidays=set())

    def test_rejects_reversed_range(self):
        """
        목적: 시작일이 종료일보다 늦으면 거부한다.

        Given: 뒤집힌 구간
        When: list_target_dates 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="종료일"):
            list_target_dates(date(2026, 7, 29), date(2026, 7, 27), collected=set(), holidays=set())
