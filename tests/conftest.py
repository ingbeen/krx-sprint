"""공통 픽스처

통합 패널 테스트가 쓰는 최소 1·2단 데이터를 tmp_path에 만든다.
실제 `storage/`에는 접근하지 않는다 (tests/CLAUDE.md 파일 격리 규칙).
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from krx_sprint.collect.adjusted_store import save_adjusted
from krx_sprint.collect.snapshot_store import save_snapshot
from krx_sprint.common_constants import ADJUSTED_COLUMNS, SNAPSHOT_COLUMNS

# 픽스처가 만드는 거래일
D1 = date(2019, 1, 2)
D2 = date(2019, 1, 3)
D3 = date(2019, 1, 4)
D4 = date(2019, 1, 7)

# 2단에만 있는 이전상장 구간 (1단 최초 등장 이전 → 패널에서 절단돼야 한다)
PRE_LISTING_DATE = date(2018, 12, 28)

TICKER_NORMAL = "000001"  # 정상 + 액션 흡수된 액면분할(D4)
TICKER_EVENTS = "000002"  # 정규장 미형성(D2) · 거래정지(D3) · 상한가 마감(D4)
TICKER_DELISTED = "000003"  # 하한가 마감(D2) 후 사라짐
TICKER_UNADJUSTED = "000004"  # 수정 미반영 감자(D4)


@dataclass(frozen=True)
class PanelSources:
    """패널 빌드 입력과 기대값

    테스트가 `conftest`를 직접 import 하지 않도록 필요한 일자·티커를 모두 여기에 노출한다.

    Attributes:
        snapshot_dir: 1단 스냅샷 루트
        adjusted_dir: 2단 수정주가 루트
        panel_dir: 패널 캐시 출력 루트
        dates: 1단 거래일 (오름차순)
        tickers: 1단 유니버스 (오름차순)
        pre_listing_date: 2단에만 있는 이전상장 일자
        ticker_normal: 액션이 흡수된 액면분할 종목
        ticker_events: 정규장 미형성·거래정지·상한가 마감 종목
        ticker_delisted: 하한가 마감 후 사라지는 종목
        ticker_unadjusted: 수정 미반영 감자 종목
        row_count: 1단 전체 행 수 = 패널 기대 행 수
        flag_counts: 플래그 컬럼별 기대 True 건수
    """

    snapshot_dir: Path
    adjusted_dir: Path
    panel_dir: Path
    dates: list[date]
    tickers: list[str]
    pre_listing_date: date
    ticker_normal: str
    ticker_events: str
    ticker_delisted: str
    ticker_unadjusted: str
    row_count: int
    flag_counts: dict[str, int]


def _row(
    ticker: str,
    market: str,
    prices: tuple[int, int, int, int],
    volume: int,
    change_rate: float,
    shares: int,
) -> dict[str, object]:
    """스냅샷 한 행을 만든다. 거래대금은 종가 × 거래량, 시총은 종가 × 상장주식수로 검산 가능하게 둔다."""
    open_price, high, low, close = prices
    return {
        "ticker": ticker,
        "market": market,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "value": close * volume,
        "change_rate": change_rate,
        "market_cap": close * shares,
        "shares": shares,
    }


def _snapshot_rows() -> dict[date, list[dict[str, object]]]:
    """일자별 스냅샷 행을 만든다."""
    return {
        D1: [
            _row(TICKER_NORMAL, "KOSPI", (5000, 5100, 4900, 5000), 1000, 0.0, 1000),
            _row(TICKER_EVENTS, "KOSDAQ", (2000, 2050, 1900, 2000), 500, 0.0, 2000),
            _row(TICKER_DELISTED, "KOSPI", (3000, 3050, 2950, 3000), 700, 0.0, 3000),
            _row(TICKER_UNADJUSTED, "KOSDAQ", (100, 105, 95, 100), 900, 0.0, 10000),
        ],
        D2: [
            _row(TICKER_NORMAL, "KOSPI", (5000, 5100, 4900, 5000), 1000, 0.0, 1000),
            # 정규장 미형성 — 거래는 있으나 시가·고가·저가가 0 (스펙 §8)
            _row(TICKER_EVENTS, "KOSDAQ", (0, 0, 0, 2000), 100, 0.0, 2000),
            # 하한가 마감 후 폐지
            _row(TICKER_DELISTED, "KOSPI", (2900, 2900, 2100, 2100), 700, -29.95, 3000),
            _row(TICKER_UNADJUSTED, "KOSDAQ", (100, 105, 95, 100), 900, 0.0, 10000),
        ],
        D3: [
            _row(TICKER_NORMAL, "KOSPI", (5000, 5100, 4900, 5000), 1000, 0.0, 1000),
            # 거래정지 — 거래량 0이면 가격 0이 정상이다 (스펙 §8)
            _row(TICKER_EVENTS, "KOSDAQ", (0, 0, 0, 0), 0, 0.0, 2000),
            _row(TICKER_UNADJUSTED, "KOSDAQ", (100, 105, 95, 100), 900, 0.0, 10000),
        ],
        D4: [
            # 5:1 액면분할 — 상장주식수 5배, 기준가 조정으로 공시 등락률은 0
            _row(TICKER_NORMAL, "KOSPI", (1000, 1050, 980, 1000), 5000, 0.0, 5000),
            # 상한가 마감 — 종가 = 고가
            _row(TICKER_EVENTS, "KOSDAQ", (2100, 2600, 2050, 2600), 800, 29.90, 2000),
            # 10:1 감자 후 거래재개 — 공시 등락률 자체가 왜곡된다 (스펙 §8.1 `052670` 유형)
            _row(TICKER_UNADJUSTED, "KOSDAQ", (1000, 1100, 950, 1000), 900, 900.0, 1000),
        ],
    }


def _adjusted_series() -> dict[str, tuple[list[date], list[float]]]:
    """티커별 2단 수정 종가를 만든다.

    `TICKER_NORMAL`은 분할이 반영돼 과거가 1/5로 조정된 상태(가짜 갭 없음),
    `TICKER_UNADJUSTED`는 수정계수가 적용되지 않아 감자 갭이 그대로 남은 상태다.
    """
    return {
        # 이전상장 구간(PRE_LISTING_DATE)은 1단에 없으므로 패널에서 절단돼야 한다
        TICKER_NORMAL: ([PRE_LISTING_DATE, D1, D2, D3, D4], [999.0, 1000.0, 1000.0, 1000.0, 1000.0]),
        TICKER_EVENTS: ([D1, D2, D3, D4], [2000.0, 2000.0, 0.0, 2600.0]),
        TICKER_DELISTED: ([D1, D2], [3000.0, 2100.0]),
        TICKER_UNADJUSTED: ([D1, D2, D3, D4], [100.0, 100.0, 100.0, 1000.0]),
    }


@pytest.fixture
def panel_sources(tmp_path: Path) -> PanelSources:
    """최소 1·2단 데이터를 만들어 패널 빌드 입력으로 제공한다."""
    snapshot_dir = tmp_path / "snapshots"
    adjusted_dir = tmp_path / "adjusted"
    panel_dir = tmp_path / "panel"

    rows_by_date = _snapshot_rows()
    for target, rows in rows_by_date.items():
        save_snapshot(pd.DataFrame(rows)[SNAPSHOT_COLUMNS], target, base_dir=snapshot_dir)

    for ticker, (dates, closes) in _adjusted_series().items():
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(dates),
                "open": closes,
                "high": [close * 1.05 for close in closes],
                "low": [close * 0.95 for close in closes],
                "close": closes,
                "volume": [100] * len(closes),
            }
        )
        save_adjusted(frame[ADJUSTED_COLUMNS], ticker, base_dir=adjusted_dir)

    return PanelSources(
        snapshot_dir=snapshot_dir,
        adjusted_dir=adjusted_dir,
        panel_dir=panel_dir,
        dates=[D1, D2, D3, D4],
        tickers=[TICKER_NORMAL, TICKER_EVENTS, TICKER_DELISTED, TICKER_UNADJUSTED],
        pre_listing_date=PRE_LISTING_DATE,
        ticker_normal=TICKER_NORMAL,
        ticker_events=TICKER_EVENTS,
        ticker_delisted=TICKER_DELISTED,
        ticker_unadjusted=TICKER_UNADJUSTED,
        row_count=sum(len(rows) for rows in rows_by_date.values()),
        flag_counts={
            "is_halted": 1,
            "no_regular_session": 1,
            "is_shares_jump": 2,
            "is_unadjusted_action": 1,
            "is_limit_up_close": 1,
            "is_limit_down_close": 1,
            "is_last_seen": 4,
        },
    )
