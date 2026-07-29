#!/usr/bin/env python3
"""코스피·코스닥 지수 종가 CSV 덤프

KRX 지수 일별 시세를 시장당 1회씩 조회해 일별/월별 종가 CSV를 만든다.
월별은 별도 조회 없이 각 월의 마지막 영업일 종가를 골라 파생한다.

일회성 산출물이므로 도메인 패키지(src/krx_sprint)에 로직을 편입하지 않고 이 파일에서 처리한다.
결과는 파생 캐시 위치에 저장되며 언제든 재생성할 수 있다.

KRX 서버에 실제 요청을 보내므로 사용자만 직접 실행한다.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from krx_sprint.collect.calendar import resolve_last_collectable_date
from krx_sprint.collect.krx_credentials import load_krx_credentials
from krx_sprint.common_constants import CACHE_DIR, COL_CLOSE, COL_DATE, KST
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger

# pykrx는 import 시점에 환경 변수를 읽어 인증 세션을 만든다.
# 자격증명을 먼저 로드해야 첫 조회부터 인증된 세션을 사용한다.
load_krx_credentials()

from pykrx import stock  # noqa: E402

logger = get_logger()

# 결과 저장 위치 (파생 캐시, git 제외)
OUTPUT_DIR = CACHE_DIR / "index"

# 조회 대상 지수: 출력 파일 접두사 → KRX 지수 티커
INDEX_TICKERS = {
    "kospi": "1001",
    "kosdaq": "2001",
}

# 조회 시작일. KRX가 보유한 만큼만 돌려주므로 각 지수의 산출 개시일보다 앞선 날짜를 준다
QUERY_START_DATE = date(1980, 1, 1)

# pykrx 지수 응답의 종가 컬럼명
SOURCE_COL_CLOSE = "종가"

# 지수 종가 저장 자릿수. KRX가 소수 2자리로 산출하므로 반올림해도 정밀도 손실이 없다
INDEX_CLOSE_DECIMALS = 2

# 결과 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("파일", 22, Align.LEFT),
    ("행 수", 10, Align.LEFT),
    ("시작일", 14, Align.LEFT),
    ("종료일", 14, Align.LEFT),
]


def _fetch_daily_close(ticker: str, end_date: date) -> pd.DataFrame:
    """지수의 일별 종가를 조회한다.

    Args:
        ticker: KRX 지수 티커
        end_date: 조회 종료 일자

    Returns:
        date·close 두 컬럼을 가진 일자 오름차순 DataFrame

    Raises:
        ValueError: 응답이 비었거나 종가 컬럼이 없는 경우
    """
    raw = stock.get_index_ohlcv(
        QUERY_START_DATE.strftime("%Y%m%d"),
        end_date.strftime("%Y%m%d"),
        ticker,
    )

    if raw.empty:
        raise ValueError(f"지수 조회 결과가 비었습니다 (ticker={ticker})")
    if SOURCE_COL_CLOSE not in raw.columns:
        raise ValueError(f"응답에 종가 컬럼이 없습니다 (ticker={ticker}, columns={list(raw.columns)})")

    daily = pd.DataFrame(
        {
            COL_DATE: pd.to_datetime(raw.index),
            COL_CLOSE: raw[SOURCE_COL_CLOSE].to_numpy(),
        }
    )
    return daily.sort_values(COL_DATE).reset_index(drop=True)


def _drop_unpublished(daily: pd.DataFrame, label: str) -> pd.DataFrame:
    """종가가 0 이하인 미산출 구간을 제외한다.

    KRX는 지수가 아직 산출되지 않은 일자를 0으로 채워 돌려준다.
    값을 만들어 채우지 않고 해당 행을 제외하며, 제외 사실은 경고로 남긴다.

    Args:
        daily: 일별 종가 DataFrame
        label: 로그에 표시할 지수 이름

    Returns:
        종가가 양수인 행만 남긴 DataFrame

    Raises:
        ValueError: 남는 행이 없는 경우
    """
    published = daily[daily[COL_CLOSE] > 0].reset_index(drop=True)

    dropped = len(daily) - len(published)
    if dropped > 0:
        logger.warning("%s: 종가 0 이하 %d행을 제외했습니다 (지수 미산출 구간)", label, dropped)

    if published.empty:
        raise ValueError(f"양수 종가가 하나도 없습니다 ({label})")

    return published


def _to_monthly(daily: pd.DataFrame, end_date: date) -> pd.DataFrame:
    """각 월의 마지막 영업일 종가만 남긴다.

    진행 중인 달은 그 달의 종가가 아직 정해지지 않았으므로 제외한다.

    Args:
        daily: 일별 종가 DataFrame
        end_date: 조회 종료 일자

    Returns:
        완결된 월의 종가 DataFrame (날짜는 실제 영업일)
    """
    month = daily[COL_DATE].dt.to_period("M")
    last_rows = daily.groupby(month)[COL_DATE].idxmax()
    monthly = daily.loc[last_rows].reset_index(drop=True)

    month_end = monthly[COL_DATE].dt.to_period("M").dt.end_time.dt.date
    return monthly[month_end <= end_date].reset_index(drop=True)


def _save_csv(frame: pd.DataFrame, path: Path) -> None:
    """종가 DataFrame을 CSV로 저장한다.

    Args:
        frame: date·close 컬럼을 가진 DataFrame
        path: 저장 경로
    """
    output = frame.round({COL_CLOSE: INDEX_CLOSE_DECIMALS})
    output[COL_DATE] = output[COL_DATE].dt.strftime("%Y-%m-%d")

    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False, encoding="utf-8")


def _summary_row(name: str, frame: pd.DataFrame) -> list[str]:
    """요약 테이블 한 행을 만든다.

    Args:
        name: 출력 파일명
        frame: 저장한 DataFrame

    Returns:
        파일명·행 수·시작일·종료일 문자열 리스트
    """
    return [
        name,
        f"{len(frame):,}",
        frame[COL_DATE].iloc[0].strftime("%Y-%m-%d"),
        frame[COL_DATE].iloc[-1].strftime("%Y-%m-%d"),
    ]


@cli_exception_handler
def main() -> int:
    """지수별 일별·월별 종가 CSV를 생성한다.

    Returns:
        종료 코드 (0=성공)
    """
    # 확정 시각 이전에는 당일 종가가 장중 미확정 값이므로 전날까지만 대상으로 삼는다
    end_date = resolve_last_collectable_date(datetime.now(KST))
    rows: list[list[str]] = []

    for prefix, ticker in INDEX_TICKERS.items():
        # 1. 일별 종가 조회 (시장당 KRX 요청 1회)
        daily = _drop_unpublished(_fetch_daily_close(ticker, end_date), prefix)

        # 2. 월별 종가 파생 (추가 조회 없음)
        monthly = _to_monthly(daily, end_date)

        # 3. 저장
        for suffix, frame in (("daily", daily), ("monthly", monthly)):
            filename = f"{prefix}_{suffix}.csv"
            _save_csv(frame, OUTPUT_DIR / filename)
            rows.append(_summary_row(filename, frame))

    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(rows, title="지수 종가 CSV 덤프 결과")
    logger.debug("저장 위치: %s", OUTPUT_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())
