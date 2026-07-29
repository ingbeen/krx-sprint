"""adjusted_quality 테스트

2단 정합성 판정 규칙을 고정한다 (스펙 §8 "2단 정합성").

핵심 계약은 두 가지다.
- 최신 일자의 수정 종가는 1단 원본 종가와 같아야 한다(수정주가는 최신일 기준으로 과거를 조정한다)
- 상장주식수가 급변한 일자에 수정주가가 가짜 갭을 만들지 않아야 한다
"""

from datetime import date

import pandas as pd
import pytest

from krx_sprint.collect.adjusted_quality import (
    ActionObservation,
    SharesJumpTracker,
    check_action_continuity,
    check_adjusted_series,
    check_collection_coverage,
    check_latest_close,
    check_listing_boundary,
    is_action_unadjusted,
    summarize_adjusted,
)
from krx_sprint.collect.quality import Severity
from krx_sprint.common_constants import ADJUSTED_COLUMNS, COL_CHANGE_RATE, COL_CLOSE, COL_SHARES, COL_TICKER

TICKER = "005930"

# 검사 기준이 되는 1단 수집 일자
SNAPSHOT_DATES = {date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4)}


def _adjusted(
    dates: list[str] | None = None,
    closes: list[float] | None = None,
    volumes: list[int] | None = None,
) -> pd.DataFrame:
    """저장 스키마의 2단 시계열을 만든다."""
    dates = dates if dates is not None else ["2019-01-02", "2019-01-03", "2019-01-04"]
    closes = closes if closes is not None else [1000.0, 1010.0, 1020.0]
    volumes = volumes if volumes is not None else [500] * len(dates)

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": closes,
            "high": [close * 1.05 for close in closes],
            "low": [close * 0.95 for close in closes],
            "close": closes,
            "volume": volumes,
        }
    )
    return frame[ADJUSTED_COLUMNS]


def _snapshot(
    tickers: list[str],
    shares: list[int],
    closes: list[int] | None = None,
    change_rates: list[float] | None = None,
) -> pd.DataFrame:
    """상장주식수 추적에 필요한 최소 스냅샷을 만든다."""
    return pd.DataFrame(
        {
            COL_TICKER: tickers,
            COL_SHARES: shares,
            COL_CLOSE: closes if closes is not None else [1000] * len(tickers),
            COL_CHANGE_RATE: change_rates if change_rates is not None else [0.0] * len(tickers),
        }
    )


class TestCheckCollectionCoverage:
    """1단 유니버스 대비 2단 수집 결손을 고정한다."""

    def test_reports_missing_tickers(self):
        """
        목적: 1단에 있는데 2단 파일이 없는 종목을 오류로 보고한다.

        Given: 유니버스 2종목 중 1종목만 수집된 상태
        When: check_collection_coverage 호출
        Then: 결손 1건이 오류로 보고된다
        """
        # Given / When
        issues = check_collection_coverage({"005930", "000660"}, {"005930"})

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR
        assert issues[0].target == "000660"

    def test_returns_empty_when_complete(self):
        """
        목적: 결손이 없으면 이슈가 없다.

        Given: 유니버스 전체가 수집된 상태
        When: check_collection_coverage 호출
        Then: 이슈가 비어 있다
        """
        # Given / When
        issues = check_collection_coverage({"005930"}, {"005930", "000660"})

        # Then
        assert issues == ()


class TestSummarizeAdjusted:
    """최신일 대조에 쓰는 요약 계약을 고정한다."""

    def test_summarizes_series(self):
        """
        목적: 시계열의 구간과 마지막 종가를 요약한다.

        Given: 3행짜리 시계열
        When: summarize_adjusted 호출
        Then: 첫·마지막 일자와 마지막 종가가 담긴다
        """
        # Given / When
        summary = summarize_adjusted(_adjusted(), TICKER)

        # Then
        assert summary.ticker == TICKER
        assert summary.first_date == date(2019, 1, 2)
        assert summary.last_date == date(2019, 1, 4)
        assert summary.last_close == pytest.approx(1020.0)
        assert summary.row_count == 3

    def test_rejects_empty_series(self):
        """
        목적: 빈 시계열을 요약 대상으로 받지 않는다 (경계 조건).

        Given: 행이 없는 시계열
        When: summarize_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="비어"):
            summarize_adjusted(_adjusted().iloc[0:0], TICKER)


class TestCheckLatestClose:
    """최신 일자 수정 종가 = 1단 원본 종가 계약을 고정한다."""

    def test_passes_when_equal(self):
        """
        목적: 최신일 종가가 원본과 같으면 이슈가 없다.

        Given: 마지막 종가 1020인 시계열
        When: 1단 원본 종가 1020과 대조
        Then: 이슈가 비어 있다
        """
        # Given
        summary = summarize_adjusted(_adjusted(), TICKER)

        # When
        issues = check_latest_close(summary, 1020)

        # Then
        assert issues == ()

    def test_reports_mismatch(self):
        """
        목적: 최신일 종가가 어긋나면 오류로 보고한다 (수정주가 전제가 깨진 상태).

        Given: 마지막 종가 1020인 시계열
        When: 1단 원본 종가 1030과 대조
        Then: 오류 1건이 보고된다
        """
        # Given
        summary = summarize_adjusted(_adjusted(), TICKER)

        # When
        issues = check_latest_close(summary, 1030)

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR

    def test_reports_missing_snapshot_row(self):
        """
        목적: 1단에 해당 종목이 없으면 대조 불가를 오류로 남긴다 (경계 조건).

        Given: 마지막 일자의 1단 종가가 없는 상태
        When: check_latest_close 호출
        Then: 오류 1건이 보고된다
        """
        # Given
        summary = summarize_adjusted(_adjusted(), TICKER)

        # When
        issues = check_latest_close(summary, None)

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR


class TestCheckAdjustedSeries:
    """시계열 구조·가격 정합성 판정을 고정한다."""

    def test_passes_normal_series(self):
        """
        목적: 정상 시계열에는 이슈가 없다.

        Given: 1단 수집 일자에 포함된 정상 시계열
        When: check_adjusted_series 호출
        Then: 이슈가 비어 있다
        """
        # Given / When
        issues = check_adjusted_series(_adjusted(), TICKER, SNAPSHOT_DATES)

        # Then
        assert issues == ()

    def test_reports_empty_series(self):
        """
        목적: 빈 시계열 파일을 오류로 보고한다 (경계 조건).

        Given: 행이 없는 시계열
        When: check_adjusted_series 호출
        Then: 오류 1건이 보고된다
        """
        # Given / When
        issues = check_adjusted_series(_adjusted().iloc[0:0], TICKER, SNAPSHOT_DATES)

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR

    def test_reports_date_outside_snapshots(self):
        """
        목적: 1단에 없는 거래일이 2단에 있으면 오류다 (대조 불가 구간).

        Given: 1단 수집 일자에 없는 날짜를 포함한 시계열
        When: check_adjusted_series 호출
        Then: 오류가 보고된다
        """
        # Given
        frame = _adjusted(dates=["2019-01-02", "2019-01-03", "2019-01-07"])

        # When
        issues = check_adjusted_series(frame, TICKER, SNAPSHOT_DATES)

        # Then
        assert any(issue.severity is Severity.ERROR for issue in issues)

    def test_reports_unsorted_dates(self):
        """
        목적: 정렬되지 않은 일자를 오류로 보고한다 (이후 계산이 정렬을 전제한다).

        Given: 일자가 역순인 시계열
        When: check_adjusted_series 호출
        Then: 오류가 보고된다
        """
        # Given
        frame = _adjusted(dates=["2019-01-04", "2019-01-03", "2019-01-02"])

        # When
        issues = check_adjusted_series(frame, TICKER, SNAPSHOT_DATES)

        # Then
        assert any(issue.severity is Severity.ERROR for issue in issues)

    def test_reports_negative_price(self):
        """
        목적: 음수 가격을 오류로 보고한다.

        Given: 저가가 음수인 시계열
        When: check_adjusted_series 호출
        Then: 오류가 보고된다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, "low"] = -1.0

        # When
        issues = check_adjusted_series(frame, TICKER, SNAPSHOT_DATES)

        # Then
        assert any(issue.severity is Severity.ERROR for issue in issues)

    def test_reports_zero_close_when_traded(self):
        """
        목적: 거래가 있는데 종가가 0인 행을 오류로 보고한다 (1단과 동일 정책).

        Given: 거래량이 있는데 종가가 0인 행
        When: check_adjusted_series 호출
        Then: 오류가 보고된다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, "close"] = 0.0

        # When
        issues = check_adjusted_series(frame, TICKER, SNAPSHOT_DATES)

        # Then
        assert any(issue.severity is Severity.ERROR for issue in issues)

    def test_allows_halted_day(self):
        """
        목적: 거래정지일(거래량 0 + 가격 0)은 정상 패턴이다 (스펙 §8 실측).

        Given: 거래량과 가격이 모두 0인 행
        When: check_adjusted_series 호출
        Then: 이슈가 비어 있다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, ["open", "high", "low", "close"]] = 0.0
        frame.loc[0, "volume"] = 0

        # When
        issues = check_adjusted_series(frame, TICKER, SNAPSHOT_DATES)

        # Then
        assert issues == ()


class TestIsActionUnadjusted:
    """수정 미반영 판별식 자체를 고정한다.

    이 순수 함수는 품질 리포트(`check_action_continuity`)와 통합 패널 빌더가 **함께 쓴다**.
    두 경로가 갈라지면 리포트와 백테스트가 서로 다른 데이터를 보게 되므로 여기서 계약을 고정한다.
    """

    def test_absorbed_action_is_not_unadjusted(self):
        """
        목적: 변동이 가격제한폭 이내면 수정계수가 흡수된 정상이다.

        Given: 수정 종가 변동 1%
        When: is_action_unadjusted 호출
        Then: False
        """
        assert is_action_unadjusted(0.01, 0.01) is False

    def test_price_limit_overlap_is_not_unadjusted(self):
        """
        목적: 상한가·하한가가 액션일과 겹친 정상 사례를 오탐하지 않는다.

        Given: 변동이 가격제한폭을 살짝 넘지만 공시 등락률과 일치
        When: is_action_unadjusted 호출
        Then: False
        """
        assert is_action_unadjusted(0.3001, 0.30) is False

    def test_mismatch_beyond_limit_is_unadjusted(self):
        """
        목적: 변동이 가격제한폭을 넘고 공시 등락률과도 다르면 미반영이다.

        Given: 수정 종가 변동 1.0, 공시 등락률 0.05
        When: is_action_unadjusted 호출
        Then: True
        """
        assert is_action_unadjusted(1.0, 0.05) is True

    def test_distorted_disclosed_rate_is_unadjusted(self):
        """
        목적: 공시 등락률 자체가 왜곡된 경우(감자 후 거래재개)는 일치 판정을 적용하지 않는다.

        Given: 수정 종가 변동 9.0, 공시 등락률 9.0 (둘 다 가격제한폭 초과)
        When: is_action_unadjusted 호출
        Then: True — 값이 같아도 공시 등락률을 신뢰할 수 없다
        """
        assert is_action_unadjusted(9.0, 9.0) is True

    def test_agrees_with_check_action_continuity(self):
        """
        목적: 추출한 판별식과 리포트 경로의 판정이 일치한다 (규칙 단일화 계약).

        Given: 미반영 사례(2단 변동 1.0, 공시 0.05)
        When: 판별식과 check_action_continuity를 각각 호출
        Then: 둘 다 미반영으로 본다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 2000.0, 2010.0])
        observation = ActionObservation(target=date(2019, 1, 3), disclosed_rate=0.05, raw_rate=1.0)

        # When
        issues = check_action_continuity(frame, TICKER, [observation])

        # Then
        assert is_action_unadjusted(1.0, 0.05) is True
        assert len(issues) == 1


class TestCheckActionContinuity:
    """분할·증자·감자일의 수정계수 미반영 판정을 고정한다.

    판별식은 "2단 변동이 **공시 등락률**과 일치하는가"다. 일치하면 수정계수가 적용된
    정상이고, 어긋나면 원본가가 그대로 남은 미반영이다. 가격제한폭 초과 여부로만 보면
    상한가·하한가가 액션일과 겹친 정상 사례를 오탐한다(실측 33건 중 17건).
    """

    def test_passes_when_adjusted(self):
        """
        목적: 수정계수가 적용돼 변동이 가격제한폭 이내면 이슈가 없다.

        Given: 급변일 수정 종가 변동이 1%인 시계열
        When: check_action_continuity 호출
        Then: 이슈가 비어 있다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 1010.0, 1020.0])
        observation = ActionObservation(target=date(2019, 1, 3), disclosed_rate=0.01, raw_rate=1.0)

        # When
        issues = check_action_continuity(frame, TICKER, [observation])

        # Then
        assert issues == ()

    def test_passes_when_price_limit_hit_on_action_day(self):
        """
        목적: 상한가·하한가가 액션일과 겹친 정상 사례를 오탐하지 않는다 (실측 17건).

        Given: 수정 종가 변동이 가격제한폭을 살짝 넘지만 공시 등락률과 일치하는 시계열
        When: check_action_continuity 호출
        Then: 이슈가 비어 있다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 1301.0, 1310.0])
        observation = ActionObservation(target=date(2019, 1, 3), disclosed_rate=0.30, raw_rate=1.60)

        # When
        issues = check_action_continuity(frame, TICKER, [observation])

        # Then
        assert issues == ()

    def test_warns_when_adjustment_missing(self):
        """
        목적: 2단 변동이 공시 등락률과 어긋나면 수정 미반영으로 경고한다 (가짜 갭).

        Given: 기준가가 조정돼 공시 등락률은 0%인데 수정 종가는 2배가 된 시계열
        When: check_action_continuity 호출
        Then: 경고 1건이 보고된다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 2000.0, 2020.0])
        observation = ActionObservation(target=date(2019, 1, 3), disclosed_rate=0.0, raw_rate=1.0)

        # When
        issues = check_action_continuity(frame, TICKER, [observation])

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.WARNING

    def test_warns_when_disclosed_rate_itself_distorted(self):
        """
        목적: 공시 등락률 자체가 왜곡된 경우를 정상으로 오분류하지 않는다.

        감자 후 거래재개(`052670` 2026-02-09 +29,948%)에서는 KRX가 기준가를 조정하지 않아
        공시 등락률과 원본 변동이 같아진다. 등락률 일치만으로 판정하면 놓친다.

        Given: 공시 등락률이 가격제한폭을 크게 넘고 2단 변동도 같은 시계열
        When: check_action_continuity 호출
        Then: 경고 1건이 보고된다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 300000.0, 300100.0])
        observation = ActionObservation(target=date(2019, 1, 3), disclosed_rate=299.0, raw_rate=299.0)

        # When
        issues = check_action_continuity(frame, TICKER, [observation])

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.WARNING

    def test_skips_first_row(self):
        """
        목적: 시계열 첫 행은 전 거래일 종가가 없어 판정하지 않는다 (경계 조건).

        Given: 급변일이 시계열의 첫 일자인 상태
        When: check_action_continuity 호출
        Then: 이슈가 비어 있다
        """
        # Given
        observation = ActionObservation(target=date(2019, 1, 2), disclosed_rate=0.0, raw_rate=0.0)

        # When
        issues = check_action_continuity(_adjusted(), TICKER, [observation])

        # Then
        assert issues == ()

    def test_skips_dates_outside_series(self):
        """
        목적: 시계열에 없는 급변일은 판정 대상이 아니다 (경계 조건).

        Given: 시계열 구간 밖의 급변일
        When: check_action_continuity 호출
        Then: 이슈가 비어 있다
        """
        # Given
        observation = ActionObservation(target=date(2020, 5, 5), disclosed_rate=5.0, raw_rate=5.0)

        # When
        issues = check_action_continuity(_adjusted(), TICKER, [observation])

        # Then
        assert issues == ()


class TestCheckListingBoundary:
    """이전상장 경계의 가격 축 불일치 판정을 고정한다.

    1단은 코넥스를 제외하지만 2단은 티커 단위 조회라 이전상장 전 이력까지 반환한다.
    그 구간은 상장 이후와 가격 축이 달라 경계에서 불연속이 생긴다(실측 43종목 중 9건이 ±30% 초과).
    1단 상장주식수 급변일을 트리거로 쓰는 액션 연속성 검사로는 원리적으로 잡히지 않는다.
    """

    def test_warns_on_discontinuity(self):
        """
        목적: 1단 최초 등장일에 가격제한폭을 넘는 점프가 있으면 경고한다.

        Given: 최초 등장일에 수정 종가가 반토막 난 시계열
        When: check_listing_boundary 호출
        Then: 경고 1건이 보고된다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 500.0, 510.0])

        # When
        issues = check_listing_boundary(frame, TICKER, date(2019, 1, 3))

        # Then
        assert len(issues) == 1
        assert issues[0].severity is Severity.WARNING

    def test_passes_when_continuous(self):
        """
        목적: 경계 변동이 가격제한폭 이내면 이슈가 없다.

        Given: 최초 등장일 변동이 1%인 시계열
        When: check_listing_boundary 호출
        Then: 이슈가 비어 있다
        """
        # Given / When
        issues = check_listing_boundary(_adjusted(), TICKER, date(2019, 1, 3))

        # Then
        assert issues == ()

    def test_skips_when_boundary_is_first_row(self):
        """
        목적: 최초 등장일이 시계열 첫 행이면 이전 구간이 없어 판정하지 않는다 (경계 조건).

        이 경우가 정상이다 — 이전상장 이력이 없는 종목은 2단 첫 행이 곧 1단 최초 등장일이다.

        Given: 최초 등장일이 시계열의 첫 일자인 상태
        When: check_listing_boundary 호출
        Then: 이슈가 비어 있다
        """
        # Given
        frame = _adjusted(closes=[1000.0, 500.0, 510.0])

        # When
        issues = check_listing_boundary(frame, TICKER, date(2019, 1, 2))

        # Then
        assert issues == ()

    def test_skips_when_first_seen_missing(self):
        """
        목적: 1단 최초 등장일을 알 수 없으면 판정하지 않는다 (경계 조건).

        Given: 최초 등장일이 None
        When: check_listing_boundary 호출
        Then: 이슈가 비어 있다
        """
        # Given / When
        issues = check_listing_boundary(_adjusted(closes=[1000.0, 500.0, 510.0]), TICKER, None)

        # Then
        assert issues == ()

    def test_skips_when_boundary_outside_series(self):
        """
        목적: 최초 등장일이 시계열에 없으면 판정하지 않는다 (경계 조건).

        Given: 시계열 구간 밖의 최초 등장일
        When: check_listing_boundary 호출
        Then: 이슈가 비어 있다
        """
        # Given / When
        issues = check_listing_boundary(_adjusted(), TICKER, date(2020, 5, 5))

        # Then
        assert issues == ()


class TestSharesJumpTracker:
    """1단에서 상장주식수 급변일의 관측치를 모으는 계약을 고정한다."""

    def test_collects_observation_on_jump(self):
        """
        목적: 급변일의 공시 등락률과 원본 종가 변동을 함께 모은다 (판정에 둘 다 필요하다).

        Given: 상장주식수가 1/10로 줄고 종가가 10배가 된 이틀치 스냅샷
        When: 순서대로 observe 후 observations_for 조회
        Then: 일자·공시 등락률(비율)·원본 변동이 담긴다
        """
        # Given
        tracker = SharesJumpTracker()

        # When
        tracker.observe(date(2019, 1, 2), _snapshot([TICKER], [1000000], closes=[1000], change_rates=[0.0]))
        tracker.observe(date(2019, 1, 3), _snapshot([TICKER], [100000], closes=[10000], change_rates=[900.0]))

        # Then
        observations = tracker.observations_for(TICKER)
        assert len(observations) == 1
        assert observations[0].target == date(2019, 1, 3)
        assert observations[0].disclosed_rate == pytest.approx(9.0)
        assert observations[0].raw_rate == pytest.approx(9.0)

    def test_ignores_small_change(self):
        """
        목적: 임계 이하의 변동은 급변으로 보지 않는다 (경계 조건).

        Given: 상장주식수가 1% 늘어난 이틀치 스냅샷
        When: 순서대로 observe
        Then: 관측치가 없다
        """
        # Given
        tracker = SharesJumpTracker()

        # When
        tracker.observe(date(2019, 1, 2), _snapshot([TICKER], [1000000]))
        tracker.observe(date(2019, 1, 3), _snapshot([TICKER], [1010000]))

        # Then
        assert tracker.observations_for(TICKER) == ()

    def test_ignores_first_appearance(self):
        """
        목적: 첫 등장일은 비교 대상이 없어 급변으로 보지 않는다 (신규 상장, 경계 조건).

        Given: 한 일자만 관측된 종목
        When: observe 1회
        Then: 관측치가 없다
        """
        # Given
        tracker = SharesJumpTracker()

        # When
        tracker.observe(date(2019, 1, 2), _snapshot([TICKER], [1000000]))

        # Then
        assert tracker.observations_for(TICKER) == ()

    def test_returns_empty_for_unknown_ticker(self):
        """
        목적: 관측된 적 없는 티커는 빈 결과를 반환한다 (경계 조건).

        Given: 비어 있는 추적기
        When: observations_for 조회
        Then: 빈 튜플이다
        """
        # Given
        tracker = SharesJumpTracker()

        # When / Then
        assert tracker.observations_for(TICKER) == ()

    def test_rejects_missing_columns(self):
        """
        목적: 판정에 필요한 컬럼이 없으면 즉시 예외를 낸다 (경계 조건).

        Given: 종가 컬럼이 없는 스냅샷
        When: observe 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = _snapshot([TICKER], [1000000]).drop(columns=[COL_CLOSE])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            SharesJumpTracker().observe(date(2019, 1, 2), snapshot)
