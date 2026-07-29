"""2단 수정주가 시계열 변환·검증

pykrx 종목별 조회 결과를 스펙 §7.2 저장 스키마로 변환하고, 저장 전 무결성을 검증한다(§8).
이 모듈은 pykrx를 import 하지 않으며 조회 결과만 입력으로 받는다.

원본가(1단)와 혼용하면 신호가 오염되므로 컬럼 목록과 저장 폴더를 모두 분리한다(스펙 §10.3).
이상치 판정은 1단과 같은 정책을 쓴다 — 반드시 있어야 하는 값은 **종가**이며,
시가·고가·저가가 0인 것은 거래정지·정규장 미형성의 정상 패턴이다(스펙 §8 실측).
"""

import pandas as pd

from krx_sprint.common_constants import (
    ADJUSTED_COLUMNS,
    ADJUSTED_PRICE_COLUMNS,
    COL_CLOSE,
    COL_DATE,
    COL_HIGH,
    COL_LOW,
    COL_OPEN,
    COL_VOLUME,
    TICKER_PATTERN,
)

# pykrx 반환 한글 컬럼 (스펙 §6)
SRC_OPEN = "시가"
SRC_HIGH = "고가"
SRC_LOW = "저가"
SRC_CLOSE = "종가"
SRC_VOLUME = "거래량"

# 조회 결과에 반드시 존재해야 하는 컬럼
SERIES_SOURCE_COLUMNS = (SRC_OPEN, SRC_HIGH, SRC_LOW, SRC_CLOSE, SRC_VOLUME)

# 한글 → 영문 컬럼 변환 (스펙 §7.2 컬럼 표준화 원칙)
SERIES_RENAME = {
    SRC_OPEN: COL_OPEN,
    SRC_HIGH: COL_HIGH,
    SRC_LOW: COL_LOW,
    SRC_CLOSE: COL_CLOSE,
    SRC_VOLUME: COL_VOLUME,
}

# 예외·경고 메시지에 포함할 위반 샘플 최대 개수
SAMPLE_LIMIT = 5


def _validate_ticker(ticker: str) -> None:
    """티커 형식을 검증한다.

    Args:
        ticker: 검증할 티커

    Raises:
        ValueError: 6자리 영숫자(숫자·대문자 영문)가 아닌 경우
    """
    if TICKER_PATTERN.fullmatch(ticker) is None:
        raise ValueError(f"티커는 6자리 영숫자(숫자·대문자 영문)여야 합니다: {ticker!r}")


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...] | list[str], label: str) -> None:
    """필요한 컬럼이 모두 있는지 확인한다.

    Args:
        frame: 확인할 DataFrame
        required: 필수 컬럼 목록
        label: 예외 메시지에 사용할 이름

    Raises:
        ValueError: 필수 컬럼이 빠진 경우
    """
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}에 필요한 컬럼이 없습니다: {missing}")


def build_adjusted(series: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """종목별 수정주가 조회 결과를 저장 스키마로 변환한다.

    빈 결과는 예외로 처리한다 — 조회 실패와 "데이터 없음"을 구분하는 것은 호출자(수집 루프)의
    책임이며, 빈 파일을 남기면 체크포인트가 오염된다.

    Args:
        series: `get_market_ohlcv` 결과 (index=일자)
        ticker: 대상 티커

    Returns:
        `ADJUSTED_COLUMNS` 순서의 시계열 (일자 오름차순, index 초기화됨)

    Raises:
        ValueError: 티커 형식·빈 결과·컬럼·인덱스 타입·일자 중복·결측치가 규칙에 맞지 않는 경우
    """
    # 1. 입력 검증
    _validate_ticker(ticker)

    if series.empty:
        raise ValueError(f"조회 결과가 비어 있습니다: {ticker}")

    _require_columns(series, SERIES_SOURCE_COLUMNS, "수정주가 조회 결과")

    # 2. 인덱스 타입 확인 (pykrx 반환 형식이 바뀌면 즉시 인지)
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError(f"시계열 인덱스가 DatetimeIndex가 아닙니다 ({ticker}): {type(series.index).__name__}")

    # 3. 일자 중복 확인 (같은 날이 두 번 오면 이후 계산이 조용히 어긋난다)
    if series.index.has_duplicates:
        duplicated = series.index[series.index.duplicated()]
        sample = [str(value.date()) for value in duplicated[:SAMPLE_LIMIT]]
        raise ValueError(f"일자가 중복된 시계열입니다 ({ticker}, {len(duplicated)}건, 예: {sample})")

    # 4. 결측치 확인 (보간 금지)
    source = series[list(SERIES_SOURCE_COLUMNS)]
    na_columns = [column for column in source.columns if bool(source[column].isna().any())]
    if na_columns:
        raise ValueError(f"조회 결과에 결측치가 있습니다 ({ticker}): {na_columns}")

    # 5. 스키마 조립 (원본 변경 없음)
    result = source.rename(columns=SERIES_RENAME).copy()
    result[COL_DATE] = series.index.to_numpy()
    result = result.reset_index(drop=True)

    # 6. dtype 고정
    for column in ADJUSTED_PRICE_COLUMNS:
        result[column] = result[column].astype("float64")
    result[COL_VOLUME] = result[COL_VOLUME].astype("int64")

    # 7. 일자 오름차순 정규화 (이후 계산이 정렬을 전제한다)
    result = result.sort_values(COL_DATE, kind="stable").reset_index(drop=True)

    return result[ADJUSTED_COLUMNS].copy()


def validate_adjusted(frame: pd.DataFrame) -> tuple[str, ...]:
    """저장 직전 시계열의 무결성을 검증한다 (스펙 §8).

    거래량이 있는데 종가가 없으면 이상치로 본다. 시가·고가·저가만 0인 행은 정규장에서 가격이
    형성되지 않은 날이라 정상이지만, 고가·저가가 0이라 스윙·전저점 계산에 그대로 쓰면
    가짜 저점이 생기므로 경고로 남긴다.

    Args:
        frame: 검증할 시계열 (`ADJUSTED_COLUMNS` 스키마)

    Returns:
        경고 메시지 튜플 (치명적이지 않은 이상 징후)

    Raises:
        ValueError: 빈 시계열·컬럼 누락·음수 가격·거래 중 종가 0 이하·고가 < 저가인 경우
    """
    # 1. 구조 검증
    if frame.empty:
        raise ValueError("시계열이 비어 있습니다")

    _require_columns(frame, ADJUSTED_COLUMNS, "시계열")

    # 2. 음수 가격은 거래 여부와 무관하게 이상치
    negative = frame[ADJUSTED_PRICE_COLUMNS].lt(0).any(axis=1)
    if bool(negative.any()):
        sample = [str(value.date()) for value in frame.loc[negative, COL_DATE].head(SAMPLE_LIMIT)]
        raise ValueError(f"음수 가격이 있는 일자가 있습니다 ({int(negative.sum())}건, 예: {sample})")

    traded = frame[COL_VOLUME] > 0

    # 3. 거래량이 있는데 종가가 없으면 이상치 (시가·고가·저가 0은 정상 패턴이므로 제외)
    invalid_close = traded & (frame[COL_CLOSE] <= 0)
    if bool(invalid_close.any()):
        sample = [str(value.date()) for value in frame.loc[invalid_close, COL_DATE].head(SAMPLE_LIMIT)]
        raise ValueError(f"거래가 있는데 종가가 0 이하인 일자가 있습니다 ({int(invalid_close.sum())}건, 예: {sample})")

    # 4. 고가 < 저가 (둘 다 0인 행은 비교가 성립하지 않으므로 자연히 통과한다)
    high_low_invalid = frame[COL_HIGH] < frame[COL_LOW]
    if bool(high_low_invalid.any()):
        sample = [str(value.date()) for value in frame.loc[high_low_invalid, COL_DATE].head(SAMPLE_LIMIT)]
        raise ValueError(f"고가가 저가보다 낮은 일자가 있습니다 ({int(high_low_invalid.sum())}건, 예: {sample})")

    # 5. 정규장 미형성은 경고 (백테스트에서 고저 계산 제외 대상)
    no_regular_session = traded & (frame[COL_LOW] <= 0)
    return tuple(f"정규장 미형성(고가·저가 0): {value.date()}" for value in frame.loc[no_regular_session, COL_DATE])
