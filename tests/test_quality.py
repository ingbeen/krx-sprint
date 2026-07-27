"""quality 테스트

1단 스냅샷 품질 검사 규칙을 고정한다 (스펙 §8).
저장 시점 인라인 검증이 잡지 못하는 일자 간·종목 간 문제가 대상이다.
"""

from datetime import date

import pandas as pd
import pytest

from krx_sprint.collect.quality import (
    Severity,
    TickerTracker,
    check_coverage,
    check_daily_snapshot,
)
from krx_sprint.common_constants import SNAPSHOT_COLUMNS

TARGET = date(2019, 1, 2)


def _snapshot(rows: list[list[object]]) -> pd.DataFrame:
    """스냅샷 스키마의 DataFrame을 만든다."""
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def _row(
    ticker: str = "005930",
    market: str = "KOSPI",
    open_: int = 1000,
    high: int = 1100,
    low: int = 900,
    close: int = 1050,
    volume: int = 500,
    value: int = 525000,
    change_rate: float = 1.5,
    market_cap: int = 10500000,
    shares: int = 10000,
) -> list[object]:
    """정상 스냅샷 한 행을 만든다 (시총 = 종가 × 상장주식수)."""
    return [ticker, market, open_, high, low, close, volume, value, change_rate, market_cap, shares]


def _categories(issues: tuple[object, ...]) -> set[str]:
    """이슈 목록에서 카테고리 집합을 뽑는다."""
    return {issue.category for issue in issues}  # type: ignore[attr-defined]


class TestCheckCoverage:
    """영업일 커버리지 검사 계약을 고정한다."""

    def test_no_issue_when_all_weekdays_accounted(self):
        """
        목적: 평일이 모두 수집 또는 휴장으로 설명되면 이슈가 없다.

        Given: 2019-01-02(수)~01-04(금) 전부 수집됨
        When: check_coverage 호출
        Then: 이슈가 없다
        """
        # Given
        collected = {date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4)}

        # When
        issues = check_coverage(date(2019, 1, 2), date(2019, 1, 4), collected, holidays=set())

        # Then
        assert issues == ()

    def test_detects_missing_weekday(self):
        """
        목적: 수집도 휴장도 아닌 평일을 결손으로 보고한다.

        Given: 가운데 일자가 어디에도 없음
        When: check_coverage 호출
        Then: 오류 이슈 1건이 나온다
        """
        # Given
        collected = {date(2019, 1, 2), date(2019, 1, 4)}

        # When
        issues = check_coverage(date(2019, 1, 2), date(2019, 1, 4), collected, holidays=set())

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR
        assert "2019-01-03" in issues[0].target

    def test_holiday_is_not_missing(self):
        """
        목적: 휴장으로 기록된 평일은 결손이 아니다.

        Given: 가운데 일자가 휴장으로 기록됨
        When: check_coverage 호출
        Then: 이슈가 없다
        """
        # Given
        collected = {date(2019, 1, 2), date(2019, 1, 4)}

        # When
        issues = check_coverage(date(2019, 1, 2), date(2019, 1, 4), collected, holidays={date(2019, 1, 3)})

        # Then
        assert issues == ()

    def test_ignores_weekend(self):
        """
        목적: 주말은 결손 대상이 아니다 (경계 조건).

        Given: 토·일이 포함된 구간에서 평일만 수집됨
        When: check_coverage 호출
        Then: 이슈가 없다
        """
        # Given
        collected = {date(2019, 1, 4), date(2019, 1, 7)}

        # When
        issues = check_coverage(date(2019, 1, 4), date(2019, 1, 7), collected, holidays=set())

        # Then
        assert issues == ()


class TestCheckDailySnapshot:
    """일자별 스냅샷 검사 계약을 고정한다."""

    def test_normal_snapshot_has_no_issue(self):
        """
        목적: 정상 스냅샷은 이슈를 만들지 않는다.

        Given: 정상 값 2종목
        When: check_daily_snapshot 호출
        Then: 이슈가 없다
        """
        # Given
        snapshot = _snapshot([_row("005930"), _row("000660", market="KOSPI")])

        # When / Then
        assert check_daily_snapshot(snapshot, TARGET) == ()

    def test_detects_duplicate_ticker(self):
        """
        목적: 같은 일자에 같은 티커가 두 번 나오면 오류로 보고한다.

        저장 시점 인라인 검증은 시장별로 따로 만들어 합치므로 이 중복을 보지 못한다.

        Given: 같은 티커가 KOSPI·KOSDAQ 양쪽에 등장
        When: check_daily_snapshot 호출
        Then: 중복 이슈가 보고된다
        """
        # Given
        snapshot = _snapshot([_row("005930", market="KOSPI"), _row("005930", market="KOSDAQ")])

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "중복 티커" in _categories(issues)

    def test_detects_market_cap_mismatch(self):
        """
        목적: 시총 검산(종가 × 상장주식수)이 어긋나면 오류로 보고한다.

        Given: 시가총액이 계산값과 다른 종목
        When: check_daily_snapshot 호출
        Then: 시총 검산 이슈가 보고된다
        """
        # Given
        snapshot = _snapshot([_row(market_cap=999)])

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "시총 검산" in _categories(issues)

    def test_halted_stock_passes_market_cap_check(self):
        """
        목적: 거래정지 종목(가격 0)이 시총 검산을 깨지 않는다 (경계 조건).

        Given: 거래량 0 + 시가·고가·저가·종가 0 + 시총 0
        When: check_daily_snapshot 호출
        Then: 시총 검산 이슈가 없다
        """
        # Given
        halted = _row(open_=0, high=0, low=0, close=0, volume=0, value=0, change_rate=0.0, market_cap=0)
        snapshot = _snapshot([halted, _row("000660")])

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "시총 검산" not in _categories(issues)

    def test_detects_all_zero_snapshot_as_holiday_misjudgement(self):
        """
        목적: 전 종목 가격이 0인 스냅샷을 휴장 오판으로 보고한다.

        시범 수집에서 실제로 발생했던 결함(2019-01-01 신정)의 회귀 테스트다.

        Given: 모든 종목의 가격이 0인 스냅샷
        When: check_daily_snapshot 호출
        Then: 휴장 오판 이슈가 오류로 보고된다
        """
        # Given
        zero = _row(open_=0, high=0, low=0, close=0, volume=0, value=0, change_rate=0.0, market_cap=0)
        snapshot = _snapshot(
            [zero, _row("000660", open_=0, high=0, low=0, close=0, volume=0, value=0, change_rate=0.0, market_cap=0)]
        )

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "휴장 오판" in _categories(issues)

    def test_warns_on_no_regular_session_trade(self):
        """
        목적: 거래는 있으나 정규장 가격이 형성되지 않은 행을 경고로 보고한다.

        고가·저가가 0이라 스윙·전저점 계산에 그대로 쓰면 가짜 저점이 생긴다.
        실측 사례: 2019-02-11 056730, 2019-05-29 310200·270520.

        Given: 시가·고가·저가 0, 종가 1460, 거래량 60795
        When: check_daily_snapshot 호출
        Then: 정규장 미형성 경고가 나오고 오류는 없다
        """
        # Given
        snapshot = _snapshot(
            [
                _row(
                    open_=0,
                    high=0,
                    low=0,
                    close=1460,
                    volume=60795,
                    value=88760700,
                    change_rate=0.0,
                    market_cap=1460 * 10000,
                )
            ]
        )

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "정규장 미형성" in _categories(issues)
        assert all(issue.severity is not Severity.ERROR for issue in issues)

    def test_detects_high_lower_than_low(self):
        """
        목적: 고가 < 저가인 종목을 오류로 보고한다.

        Given: 고가가 저가보다 낮은 종목
        When: check_daily_snapshot 호출
        Then: 가격 정합성 이슈가 보고된다
        """
        # Given
        snapshot = _snapshot([_row(high=800, low=900)])

        # When
        issues = check_daily_snapshot(snapshot, TARGET)

        # Then
        assert "가격 정합성" in _categories(issues)

    def test_change_rate_beyond_limit_is_warning_not_error(self):
        """
        목적: 가격제한폭 초과 등락률은 경고로만 남긴다 (권리락 등 정상 사례).

        Given: 등락률 -45%인 종목
        When: check_daily_snapshot 호출
        Then: 심각도가 경고다
        """
        # Given
        snapshot = _snapshot([_row(change_rate=-45.0)])

        # When
        issues = check_daily_snapshot(snapshot, TARGET)
        limit_issues = [issue for issue in issues if issue.category == "등락률 초과"]

        # Then
        assert len(limit_issues) == 1
        assert limit_issues[0].severity is Severity.WARNING

    def test_anchor_mismatch_is_reported(self):
        """
        목적: 스펙 §3.1의 알려진 값과 어긋나면 오류로 보고한다 (외부 기준 대조).

        Given: 앵커 일자(2021-01-04)에 삼성전자 종가가 다른 스냅샷
        When: check_daily_snapshot 호출
        Then: 앵커 검산 이슈가 보고된다
        """
        # Given
        snapshot = _snapshot([_row("005930", close=99999, shares=5969782550, market_cap=99999 * 5969782550)])

        # When
        issues = check_daily_snapshot(snapshot, date(2021, 1, 4))

        # Then
        assert "앵커 검산" in _categories(issues)

    def test_rejects_missing_columns(self):
        """
        목적: 스키마가 다른 입력은 검사하지 않고 즉시 예외를 발생시킨다.

        Given: 컬럼이 빠진 DataFrame
        When: check_daily_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        broken = _snapshot([_row()]).drop(columns=["shares"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            check_daily_snapshot(broken, TARGET)


class TestTickerTracker:
    """티커 등장 이력·상장주식수 변화 추적 계약을 고정한다 (스펙 §10.4·§10.5)."""

    def _frame(self, entries: list[tuple[str, int]]) -> pd.DataFrame:
        """(티커, 상장주식수) 목록으로 최소 스냅샷을 만든다."""
        return _snapshot([_row(ticker=ticker, shares=shares, market_cap=1050 * shares) for ticker, shares in entries])

    def test_detects_ticker_reuse_after_long_gap(self):
        """
        목적: 장기 공백 후 재등장한 티커를 재사용 의심으로 보고한다.

        Given: 2019-01-02 등장 후 1년 뒤 재등장
        When: 순차 관측
        Then: 재사용 의심 이슈가 나온다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2019, 1, 2), self._frame([("005930", 10000)]))
        issues = tracker.observe(date(2020, 1, 2), self._frame([("005930", 10000)]))

        # Then
        assert len(issues) == 1
        assert issues[0].category == "티커 재사용"

    def test_continuous_ticker_has_no_issue(self):
        """
        목적: 연속으로 등장하고 상장주식수가 그대로면 이슈가 없다.

        Given: 인접 영업일에 같은 상장주식수로 등장
        When: 순차 관측
        Then: 이슈가 없다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2019, 1, 2), self._frame([("005930", 10000)]))
        issues = tracker.observe(date(2019, 1, 3), self._frame([("005930", 10000)]))

        # Then
        assert issues == ()

    def test_detects_shares_surge(self):
        """
        목적: 상장주식수가 급증한 날을 보고한다 (유상·무상증자, 액면분할).

        해당 일자 등락률은 원본가 기준이라 왜곡되므로 기준봉 판정에서 제외해야 한다.

        Given: 상장주식수가 두 배로 늘어난 다음 날
        When: 순차 관측
        Then: 상장주식수 급변 경고가 나온다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2019, 1, 2), self._frame([("005930", 10000)]))
        issues = tracker.observe(date(2019, 1, 3), self._frame([("005930", 20000)]))

        # Then
        assert len(issues) == 1
        assert issues[0].category == "상장주식수 급변"
        assert issues[0].severity is Severity.WARNING

    def test_detects_shares_reduction(self):
        """
        목적: 상장주식수가 급감한 날도 보고한다 (감자·주식병합).

        실측 사례: 052670이 2026-02-09에 29,129,064 → 19,419(1,500:1 감자)로
        바뀌면서 등락률이 +29,948%로 기록됐다.

        Given: 상장주식수가 1/1500로 줄어든 다음 날
        When: 순차 관측
        Then: 상장주식수 급변 경고가 나온다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2026, 2, 6), self._frame([("052670", 29129064)]))
        issues = tracker.observe(date(2026, 2, 9), self._frame([("052670", 19419)]))

        # Then
        assert len(issues) == 1
        assert issues[0].category == "상장주식수 급변"

    def test_ignores_small_shares_change(self):
        """
        목적: 임계 이하의 소폭 변동은 보고하지 않는다 (경계 조건).

        스톡옵션 행사 등으로 상장주식수는 조금씩 자주 바뀐다.

        Given: 상장주식수가 1% 늘어난 다음 날
        When: 순차 관측
        Then: 이슈가 없다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2019, 1, 2), self._frame([("005930", 10000)]))
        issues = tracker.observe(date(2019, 1, 3), self._frame([("005930", 10100)]))

        # Then
        assert issues == ()

    def test_reused_ticker_skips_shares_comparison(self):
        """
        목적: 재사용 의심 티커는 상장주식수를 비교하지 않는다 (다른 회사일 수 있음).

        Given: 장기 공백 후 상장주식수가 완전히 다른 상태로 재등장
        When: 순차 관측
        Then: 재사용 이슈만 나오고 상장주식수 급변은 보고되지 않는다
        """
        # Given
        tracker = TickerTracker()

        # When
        tracker.observe(date(2019, 1, 2), self._frame([("005930", 10000)]))
        issues = tracker.observe(date(2020, 1, 2), self._frame([("005930", 99999999)]))

        # Then
        assert _categories(issues) == {"티커 재사용"}

    def test_finalize_reports_delisted_tickers(self):
        """
        목적: 마지막 수집일보다 한참 전에 사라진 티커를 폐지로 보고한다.

        Given: 초반에만 등장하고 사라진 티커
        When: finalize 호출
        Then: 폐지 종목으로 보고된다 (생존편향 방지가 동작한 증거)
        """
        # Given
        tracker = TickerTracker()
        tracker.observe(date(2019, 1, 2), self._frame([("117930", 10000), ("005930", 10000)]))
        tracker.observe(date(2020, 6, 1), self._frame([("005930", 10000)]))

        # When
        issues = tracker.finalize(date(2020, 6, 1))

        # Then
        delisted = [issue for issue in issues if issue.target == "117930"]
        assert len(delisted) == 1
        assert delisted[0].severity is Severity.INFO

    def test_finalize_keeps_active_tickers_out(self):
        """
        목적: 마지막 일자까지 살아있는 티커는 폐지로 보고하지 않는다 (경계 조건).

        Given: 마지막 일자에도 등장한 티커
        When: finalize 호출
        Then: 해당 티커는 보고되지 않는다
        """
        # Given
        tracker = TickerTracker()
        tracker.observe(date(2020, 6, 1), self._frame([("005930", 10000)]))

        # When
        issues = tracker.finalize(date(2020, 6, 1))

        # Then
        assert issues == ()
