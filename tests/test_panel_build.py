"""panel.build 테스트

통합 패널의 스키마 계약과 처리 규칙 플래그를 고정한다 (백테스트 설계 §2.3·§3).

핵심 계약은 세 가지다.
- 패널의 행 집합은 **1단 스냅샷과 정확히 같다**. 2단은 1단의 상위집합이므로 결손이 없고,
  1단에 없는 이전상장 구간은 조인 과정에서 절단된다
- **2단 거래량 컬럼은 존재하지 않는다**. 조정된 거래량을 거래대금 계산에 쓰면 신호가 오염된다
- 수집 단계에서 확정된 처리 규칙이 플래그 컬럼으로 패널에 박혀 있다

입력 데이터와 기대값은 `conftest.panel_sources` 픽스처가 제공한다.
"""

from datetime import date

import pandas as pd
import pytest

from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_CLOSE,
    COL_DATE,
    COL_TICKER,
    PANEL_COLUMNS,
    PANEL_FLAG_COLUMNS,
)
from krx_sprint.panel.build import build_panel
from krx_sprint.panel.loader import load_panel


def _build(sources):
    """픽스처 데이터로 패널을 빌드한다."""
    return build_panel(
        panel_dir=sources.panel_dir,
        snapshot_dir=sources.snapshot_dir,
        adjusted_dir=sources.adjusted_dir,
    )


def _panel(sources) -> pd.DataFrame:
    """빌드 후 전체 패널을 읽는다."""
    _build(sources)
    return load_panel(
        panel_dir=sources.panel_dir,
        snapshot_dir=sources.snapshot_dir,
        adjusted_dir=sources.adjusted_dir,
    )


def _flag_of(panel: pd.DataFrame, ticker: str, target: date, column: str) -> bool:
    """특정 (티커, 일자) 행의 플래그 값을 꺼낸다."""
    row = panel[(panel[COL_TICKER] == ticker) & (panel[COL_DATE] == pd.Timestamp(target))]
    assert len(row) == 1
    return bool(row.iloc[0][column])


class TestPanelSchema:
    """패널 스키마 계약을 고정한다."""

    def test_columns_match_contract(self, panel_sources):
        """
        목적: 저장 컬럼 순서가 PANEL_COLUMNS 계약과 정확히 일치한다.

        Given: 최소 1·2단 데이터
        When: 패널 빌드 후 로드
        Then: 컬럼 목록이 계약과 같다
        """
        # Given / When
        panel = _panel(panel_sources)

        # Then
        assert list(panel.columns) == PANEL_COLUMNS

    def test_adjusted_volume_is_absent(self, panel_sources):
        """
        목적: 2단 거래량은 조정돼 있어 패널에 싣지 않는다 (설계 §2.3).

        Given: 거래량이 든 2단 파일
        When: 패널 빌드 후 로드
        Then: 수정주가 축의 거래량 컬럼이 하나도 없다
        """
        # Given / When
        panel = _panel(panel_sources)

        # Then
        assert not [column for column in panel.columns if column.startswith("adj_") and "volume" in column]

    def test_flags_are_boolean(self, panel_sources):
        """
        목적: 플래그는 bool dtype이어야 한다 (정수나 object면 필터가 조용히 어긋난다).

        Given: 최소 1·2단 데이터
        When: 패널 빌드 후 로드
        Then: 플래그 컬럼이 모두 bool
        """
        # Given / When
        panel = _panel(panel_sources)

        # Then
        for column in PANEL_FLAG_COLUMNS:
            assert panel[column].dtype == bool, column


class TestRowSet:
    """패널의 행 집합이 1단과 같음을 고정한다."""

    def test_row_count_equals_snapshot_rows(self, panel_sources):
        """
        목적: 조인으로 행이 늘거나 줄지 않는다.

        Given: 1단 전체 행 수를 아는 픽스처
        When: 패널 빌드
        Then: 패널 행 수 = 1단 행 수
        """
        # Given / When
        summary = _build(panel_sources)

        # Then
        assert summary.row_count == panel_sources.row_count

    def test_pre_listing_rows_are_truncated(self, panel_sources):
        """
        목적: 1단에 없는 이전상장 구간은 유니버스 밖이므로 절단된다 (스펙 §8.2).

        Given: 2단에만 존재하는 이전상장 일자
        When: 패널 빌드 후 로드
        Then: 그 일자의 행이 없다
        """
        # Given / When
        panel = _panel(panel_sources)

        # Then
        assert (panel[COL_DATE] == pd.Timestamp(panel_sources.pre_listing_date)).sum() == 0

    def test_adjusted_close_has_no_gap(self, panel_sources):
        """
        목적: 2단은 1단의 상위집합이므로 조인 후 수정주가 결측이 없어야 한다.

        Given: 최소 1·2단 데이터
        When: 패널 빌드 후 로드
        Then: adj_close 결측 0건
        """
        # Given / When
        panel = _panel(panel_sources)

        # Then
        assert panel[COL_ADJ_CLOSE].isna().sum() == 0

    def test_missing_adjusted_file_raises(self, panel_sources):
        """
        목적: 2단 파일이 없는 종목을 조용히 건너뛰지 않는다 (결측 은폐 금지).

        Given: 2단 파일 하나를 삭제한 상태
        When: 패널 빌드
        Then: ValueError
        """
        # Given
        (panel_sources.adjusted_dir / f"{panel_sources.ticker_delisted}.parquet").unlink()

        # When / Then
        with pytest.raises(ValueError, match="2단"):
            _build(panel_sources)


class TestFlags:
    """처리 규칙 플래그를 케이스별로 고정한다."""

    def test_flag_counts_match_expected(self, panel_sources):
        """
        목적: 플래그별 발생 건수가 픽스처 설계와 정확히 일치한다.

        Given: 규칙별 케이스를 하나씩 심은 픽스처
        When: 패널 빌드
        Then: 요약의 플래그 건수가 기대값과 같다
        """
        # Given / When
        summary = _build(panel_sources)

        # Then
        assert summary.flag_counts == panel_sources.flag_counts

    def test_halted_row_is_flagged(self, panel_sources):
        """
        목적: 거래량 0인 날은 거래정지로 표시돼 매매 불가 판정에 쓰인다.

        Given: 거래량 0인 행
        When: 패널 빌드
        Then: is_halted True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_events, panel_sources.dates[2], "is_halted")

    def test_no_regular_session_needs_volume(self, panel_sources):
        """
        목적: 정규장 미형성은 `거래량 > 0 AND 저가 = 0`이다. 거래정지일(거래량 0)은 해당하지 않는다.

        Given: 저가 0 + 거래량 있음과 저가 0 + 거래량 0
        When: 패널 빌드
        Then: 전자만 True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_events, panel_sources.dates[1], "no_regular_session")
        assert not _flag_of(panel, panel_sources.ticker_events, panel_sources.dates[2], "no_regular_session")

    def test_shares_jump_is_flagged(self, panel_sources):
        """
        목적: 상장주식수 급변일을 표시해 기준봉 판정에서 제외할 수 있게 한다 (스펙 §10.5).

        Given: 5:1 액면분할이 있은 마지막 거래일
        When: 패널 빌드
        Then: is_shares_jump True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_normal, panel_sources.dates[3], "is_shares_jump")

    def test_absorbed_action_is_not_unadjusted(self, panel_sources):
        """
        목적: 수정계수가 반영된 액션은 미반영으로 잡히면 안 된다 (오탐 방지).

        Given: 과거가 1/5로 조정돼 가짜 갭이 없는 분할
        When: 패널 빌드
        Then: is_shares_jump는 True지만 is_unadjusted_action은 False
        """
        panel = _panel(panel_sources)
        assert not _flag_of(panel, panel_sources.ticker_normal, panel_sources.dates[3], "is_unadjusted_action")

    def test_unadjusted_action_is_flagged(self, panel_sources):
        """
        목적: 수정계수가 적용되지 않아 가짜 갭이 남은 액션을 표시한다 (스펙 §8.2).

        Given: 감자 후 2단 종가가 10배로 뛴 마지막 거래일
        When: 패널 빌드
        Then: is_unadjusted_action True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_unadjusted, panel_sources.dates[3], "is_unadjusted_action")

    def test_limit_close_flags(self, panel_sources):
        """
        목적: 상한가·하한가 마감을 구분해 미체결 판정에 쓴다 (설계 §7.2).

        Given: 상한가 마감과 하한가 마감
        When: 패널 빌드
        Then: 각각 해당 플래그만 True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_events, panel_sources.dates[3], "is_limit_up_close")
        assert _flag_of(panel, panel_sources.ticker_delisted, panel_sources.dates[1], "is_limit_down_close")
        assert not _flag_of(panel, panel_sources.ticker_events, panel_sources.dates[3], "is_limit_down_close")

    def test_last_seen_marks_final_row_per_ticker(self, panel_sources):
        """
        목적: 티커별 최종 등장일을 표시해 폐지 청산 트리거로 쓴다.

        Given: 중간에 사라지는 종목과 끝까지 남는 종목
        When: 패널 빌드
        Then: 각 티커의 마지막 행에만 True
        """
        panel = _panel(panel_sources)
        assert _flag_of(panel, panel_sources.ticker_delisted, panel_sources.dates[1], "is_last_seen")
        assert not _flag_of(panel, panel_sources.ticker_delisted, panel_sources.dates[0], "is_last_seen")
        assert _flag_of(panel, panel_sources.ticker_normal, panel_sources.dates[3], "is_last_seen")


class TestBuildSummary:
    """빌드 요약값을 고정한다."""

    def test_summary_reports_range_and_universe(self, panel_sources):
        """
        목적: 요약이 구간·종목 수를 정확히 보고한다 (CLI 출력과 실행 이력의 근거).

        Given: 4거래일 4종목 픽스처
        When: 패널 빌드
        Then: 구간과 종목 수가 일치한다
        """
        # Given / When
        summary = _build(panel_sources)

        # Then
        assert summary.first_date == panel_sources.dates[0]
        assert summary.last_date == panel_sources.dates[-1]
        assert summary.ticker_count == len(panel_sources.tickers)

    def test_rebuild_replaces_previous_output(self, panel_sources):
        """
        목적: 재빌드가 이전 산출물을 남기지 않는다 (낡은 연도 파일 잔존 금지).

        Given: 한 번 빌드한 뒤 가짜 연도 파일을 심은 상태
        When: 다시 빌드
        Then: 가짜 파일이 사라진다
        """
        # Given
        _build(panel_sources)
        stale = panel_sources.panel_dir / "1999.parquet"
        stale.write_bytes(b"")

        # When
        _build(panel_sources)

        # Then
        assert not stale.exists()


class TestPriceAxes:
    """원본가와 수정주가가 서로 다른 축임을 고정한다."""

    def test_raw_and_adjusted_differ_after_split(self, panel_sources):
        """
        목적: 분할 이전 구간에서 원본가와 수정주가가 다르다 — 혼용하면 신호가 오염된다.

        Given: 5:1 분할 전 구간의 첫 거래일
        When: 패널 빌드 후 로드
        Then: close(원본 5000)와 adj_close(조정 1000)가 다르다
        """
        # Given / When
        panel = _panel(panel_sources)
        target = pd.Timestamp(panel_sources.dates[0])
        row = panel[(panel[COL_TICKER] == panel_sources.ticker_normal) & (panel[COL_DATE] == target)].iloc[0]

        # Then
        assert row[COL_CLOSE] == 5000
        assert row[COL_ADJ_CLOSE] == pytest.approx(1000.0)
