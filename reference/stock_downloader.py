"""주식 데이터 다운로드 및 검증 유틸리티

Yahoo Finance에서 주식 데이터를 다운로드하고, 데이터 유효성을 검증하는 함수를 제공한다.

주요 기능:
1. 주식 데이터 유효성 검증 (결측치, 0값, 음수, 급등락)
2. Yahoo Finance에서 주식 데이터 다운로드 및 CSV 저장
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from qbt.common_constants import COL_CLOSE, COL_DATE, PRICE_COLUMNS, REQUIRED_COLUMNS, STOCK_DIR
from qbt.utils.logger import get_logger

logger = get_logger(__name__)

# 데이터 검증 관련 상수
DEFAULT_PRICE_CHANGE_THRESHOLD = 0.50


def validate_stock_data(df: pd.DataFrame) -> None:
    """
    데이터 유효성을 검증한다.

    다음 항목을 검사하며, 이상 발견 시 즉시 예외를 발생시킨다:
    - 가격 컬럼의 결측치, 0, 음수 값
    - 전일 대비 급등락 (임계값 이상)

    어떠한 형태의 보간도 수행하지 않는다.

    Args:
        df: 검증할 DataFrame

    Raises:
        ValueError: 데이터 이상이 감지되었을 때
    """
    # 1. 결측치 검사
    for col in PRICE_COLUMNS:
        null_mask = df[col].isna()
        if null_mask.any():
            null_indices = df.index[null_mask].tolist()
            null_dates = df.loc[null_mask, COL_DATE].tolist()
            raise ValueError(
                f"결측치 발견 - 컬럼: {col}, 인덱스: {null_indices[:5]}{'...' if len(null_indices) > 5 else ''}, 날짜: {null_dates[:5]}{'...' if len(null_dates) > 5 else ''}"
            )

    # 2. 0 값 검사
    for col in PRICE_COLUMNS:
        zero_mask = df[col] == 0
        if zero_mask.any():
            zero_indices = df.index[zero_mask].tolist()
            zero_dates = df.loc[zero_mask, COL_DATE].tolist()
            raise ValueError(
                f"0 값 발견 - 컬럼: {col}, 인덱스: {zero_indices[:5]}{'...' if len(zero_indices) > 5 else ''}, 날짜: {zero_dates[:5]}{'...' if len(zero_dates) > 5 else ''}"
            )

    # 3. 음수 값 검사
    for col in PRICE_COLUMNS:
        negative_mask = df[col] < 0
        if negative_mask.any():
            negative_indices = df.index[negative_mask].tolist()
            negative_dates = df.loc[negative_mask, COL_DATE].tolist()
            ellipsis = "..." if len(negative_indices) > 5 else ""
            raise ValueError(
                f"음수 값 발견 - 컬럼: {col}, 인덱스: {negative_indices[:5]}{ellipsis}, 날짜: {negative_dates[:5]}{ellipsis}"
            )

    # 4. 전일 대비 급등락 검사 (Close 기준)
    df_copy = df.copy()
    df_copy["pct_change"] = df_copy[COL_CLOSE].pct_change()

    # 첫 번째 행은 NaN이므로 제외
    extreme_mask = df_copy["pct_change"].abs() >= DEFAULT_PRICE_CHANGE_THRESHOLD
    extreme_mask = extreme_mask.fillna(False)

    if extreme_mask.any():
        extreme_rows = df_copy[extreme_mask]
        for _, row in extreme_rows.iterrows():
            pct = row["pct_change"] * 100
            logger.warning(f"급등락 감지 - 날짜: {row[COL_DATE]}, 변동률: {pct:+.2f}%, 종가: {row[COL_CLOSE]:.2f}")

        first_extreme = extreme_rows.iloc[0]
        raise ValueError(
            f"전일 대비 급등락 감지 (임계값: {DEFAULT_PRICE_CHANGE_THRESHOLD * 100:.0f}%) - 날짜: {first_extreme[COL_DATE]}, 변동률: {first_extreme['pct_change'] * 100:+.2f}%"
        )

    logger.debug("데이터 유효성 검증 완료: 이상 없음")


def download_stock_data(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Path:
    """
    주식 데이터를 다운로드하고 CSV로 저장한다.

    Args:
        ticker: 주식 티커 (예: QQQ, SPY)
        start_date: 시작 날짜 (YYYY-MM-DD 형식)
        end_date: 종료 날짜 (YYYY-MM-DD 형식)

    Returns:
        저장된 CSV 파일의 경로
    """
    # 1. 출력 디렉토리 생성
    output_path = STOCK_DIR
    output_path.mkdir(parents=True, exist_ok=True)

    # 2. yfinance Ticker 객체 생성
    yf_ticker = yf.Ticker(ticker)

    # 3. 날짜 조건별 데이터 다운로드 및 파일명 생성
    if start_date and end_date:
        df = yf_ticker.history(start=start_date, end=end_date)
        filename = f"{ticker}_{start_date}_{end_date}.csv"
    elif start_date:
        df = yf_ticker.history(start=start_date)
        filename = f"{ticker}_{start_date}_latest.csv"
    else:
        df = yf_ticker.history(period="max")
        filename = f"{ticker}_max.csv"

    # 4. 데이터 유효성 검증
    if df.empty:
        raise ValueError(f"데이터를 찾을 수 없습니다: {ticker}")

    # 5. 인덱스를 Date 컬럼으로 변환
    df.reset_index(inplace=True)
    df[COL_DATE] = pd.to_datetime(df[COL_DATE]).dt.date

    # 6. 필요한 컬럼만 선택 (Date, Open, High, Low, Close, Volume)
    df = df[REQUIRED_COLUMNS]

    # 7. 최근 데이터 필터링 (오늘 포함 최근 2일 제외)
    cutoff_date = date.today() - timedelta(days=2)
    original_count = len(df)
    df = df[df[COL_DATE] <= cutoff_date]
    filtered_count = original_count - len(df)

    # 8. 가격 컬럼을 소수점 6자리로 라운딩
    df[PRICE_COLUMNS] = df[PRICE_COLUMNS].round(6)

    # 9. 데이터 유효성 검증
    validate_stock_data(df)

    # 10. CSV 파일로 저장 (검증 통과 시에만 실행)
    csv_path = output_path / filename
    df.to_csv(csv_path, index=False)

    # 11. 결과 출력
    logger.debug(f"데이터 저장 완료: {csv_path}")
    logger.debug(f"기간: {df[COL_DATE].min()} ~ {df[COL_DATE].max()}")
    logger.debug(f"행 수: {len(df):,}")
    if filtered_count > 0:
        logger.debug(f"최근 데이터 제외: {filtered_count}행 (오늘 포함 최근 2일)")

    return csv_path
