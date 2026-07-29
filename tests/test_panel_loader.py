"""panel.loader 테스트

패널 캐시 로더의 계약을 고정한다 (백테스트 설계 §2.4·§2.5).

핵심 계약은 두 가지다.
- 캐시가 원천 데이터와 어긋나면 **예외로 막는다**. 조용히 재생성하면 "이 결과가 어느 데이터에서
  나왔는가"를 알 수 없게 된다
- 구간·티커 부분 로드가 전체 로드의 부분집합과 정확히 같다
"""

from datetime import date

import pandas as pd
import pytest

from krx_sprint.collect.snapshot_store import save_snapshot
from krx_sprint.common_constants import COL_DATE, COL_TICKER, SNAPSHOT_COLUMNS
from krx_sprint.panel.build import build_panel
from krx_sprint.panel.loader import load_panel, load_trading_days, read_build_meta


def _build(sources):
    """픽스처 데이터로 패널을 빌드한다."""
    return build_panel(
        panel_dir=sources.panel_dir,
        snapshot_dir=sources.snapshot_dir,
        adjusted_dir=sources.adjusted_dir,
    )


def _load(sources, **kwargs) -> pd.DataFrame:
    """픽스처 경로로 패널을 읽는다."""
    return load_panel(
        panel_dir=sources.panel_dir,
        snapshot_dir=sources.snapshot_dir,
        adjusted_dir=sources.adjusted_dir,
        **kwargs,
    )


class TestFingerprint:
    """캐시 유효성 검사를 고정한다."""

    def test_load_succeeds_right_after_build(self, panel_sources):
        """
        목적: 빌드 직후에는 지문이 일치해 그대로 읽힌다.

        Given: 방금 빌드한 캐시
        When: load_panel 호출
        Then: 1단 행 수만큼 반환된다
        """
        # Given
        _build(panel_sources)

        # When
        panel = _load(panel_sources)

        # Then
        assert len(panel) == panel_sources.row_count

    def test_stale_cache_raises_when_snapshot_added(self, panel_sources):
        """
        목적: 1단이 늘었는데 캐시가 그대로면 낡은 결과를 쓰지 않도록 막는다.

        Given: 빌드 후 1단 스냅샷을 한 일자 추가한 상태
        When: load_panel 호출
        Then: ValueError (재빌드 안내 포함)
        """
        # Given
        _build(panel_sources)
        extra = pd.DataFrame(
            [
                {
                    "ticker": panel_sources.ticker_normal,
                    "market": "KOSPI",
                    "open": 1000,
                    "high": 1050,
                    "low": 980,
                    "close": 1000,
                    "volume": 100,
                    "value": 100_000,
                    "change_rate": 0.0,
                    "market_cap": 5_000_000,
                    "shares": 5000,
                }
            ]
        )[SNAPSHOT_COLUMNS]
        save_snapshot(extra, date(2019, 1, 8), base_dir=panel_sources.snapshot_dir)

        # When / Then
        with pytest.raises(ValueError, match="build_panel"):
            _load(panel_sources)

    def test_stale_cache_raises_when_adjusted_removed(self, panel_sources):
        """
        목적: 2단 종목 수가 바뀌어도 낡은 캐시를 막는다.

        Given: 빌드 후 2단 파일을 하나 삭제한 상태
        When: load_panel 호출
        Then: ValueError
        """
        # Given
        _build(panel_sources)
        (panel_sources.adjusted_dir / f"{panel_sources.ticker_delisted}.parquet").unlink()

        # When / Then
        with pytest.raises(ValueError, match="build_panel"):
            _load(panel_sources)

    def test_missing_cache_raises(self, panel_sources):
        """
        목적: 캐시가 없으면 자동 생성하지 않고 명확히 알린다.

        Given: 빌드하지 않은 상태
        When: load_panel 호출
        Then: FileNotFoundError
        """
        # When / Then
        with pytest.raises(FileNotFoundError, match="패널"):
            _load(panel_sources)

    def test_build_meta_records_range(self, panel_sources):
        """
        목적: 빌드 지문에 구간·행수가 기록돼 결과 추적이 가능하다.

        Given: 방금 빌드한 캐시
        When: read_build_meta 호출
        Then: 행 수와 마지막 일자가 일치한다
        """
        # Given
        _build(panel_sources)

        # When
        meta = read_build_meta(panel_dir=panel_sources.panel_dir)

        # Then
        assert meta.row_count == panel_sources.row_count
        assert meta.snapshot_last_date == panel_sources.dates[-1]


class TestPartialLoad:
    """부분 로드를 고정한다."""

    def test_date_range_filter(self, panel_sources):
        """
        목적: 구간 필터가 경계를 포함해 정확히 자른다.

        Given: 4거래일 패널
        When: 두 번째~세 번째 거래일만 로드
        Then: 그 두 일자만 담긴다
        """
        # Given
        _build(panel_sources)

        # When
        panel = _load(panel_sources, start=panel_sources.dates[1], end=panel_sources.dates[2])

        # Then
        assert set(panel[COL_DATE].dt.date) == {panel_sources.dates[1], panel_sources.dates[2]}

    def test_ticker_filter(self, panel_sources):
        """
        목적: 티커 필터가 지정 종목만 남긴다.

        Given: 4종목 패널
        When: 한 종목만 지정해 로드
        Then: 그 종목 행만 남는다
        """
        # Given
        _build(panel_sources)

        # When
        panel = _load(panel_sources, tickers=[panel_sources.ticker_events])

        # Then
        assert set(panel[COL_TICKER]) == {panel_sources.ticker_events}

    def test_rows_are_sorted_by_date_then_ticker(self, panel_sources):
        """
        목적: 정렬 순서를 계약으로 고정한다 (엔진의 일별 루프가 이 순서를 전제한다).

        Given: 4거래일 패널
        When: 전체 로드
        Then: (일자, 티커) 오름차순이다
        """
        # Given
        _build(panel_sources)

        # When
        panel = _load(panel_sources)

        # Then
        expected = panel.sort_values([COL_DATE, COL_TICKER]).reset_index(drop=True)
        assert panel[COL_DATE].equals(expected[COL_DATE])
        assert list(panel[COL_TICKER]) == list(expected[COL_TICKER])

    def test_rejects_inverted_range(self, panel_sources):
        """
        목적: 시작일이 종료일보다 늦으면 빈 결과 대신 예외를 낸다.

        Given: 뒤집힌 구간
        When: load_panel 호출
        Then: ValueError
        """
        # Given
        _build(panel_sources)

        # When / Then
        with pytest.raises(ValueError, match="구간"):
            _load(panel_sources, start=panel_sources.dates[3], end=panel_sources.dates[0])


class TestTradingDays:
    """거래일 캘린더 제공을 고정한다 — 비용 모델의 결제일 환산이 이 값을 쓴다."""

    def test_returns_sorted_trading_days(self, panel_sources):
        """
        목적: 패널에 담긴 거래일을 오름차순으로 돌려준다.

        Given: 4거래일 패널
        When: load_trading_days 호출
        Then: 픽스처 일자와 같다
        """
        # Given
        _build(panel_sources)

        # When
        days = load_trading_days(
            panel_dir=panel_sources.panel_dir,
            snapshot_dir=panel_sources.snapshot_dir,
            adjusted_dir=panel_sources.adjusted_dir,
        )

        # Then
        assert days == panel_sources.dates
