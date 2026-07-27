"""일별 전종목 스냅샷 변환·검증

pykrx 조회 결과를 스펙 §7.2 저장 스키마로 변환하고, 저장 전 무결성을 검증한다(§8).
이 모듈은 pykrx를 import 하지 않으며 조회 결과만 입력으로 받는다.
"""

import pandas as pd

from krx_sprint.common_constants import (
    COL_CHANGE_RATE,
    COL_CLOSE,
    COL_HIGH,
    COL_LOW,
    COL_MARKET,
    COL_MARKET_CAP,
    COL_OPEN,
    COL_SHARES,
    COL_TICKER,
    COL_VALUE,
    COL_VOLUME,
    MARKETS,
    PRICE_LIMIT_RATE,
    SNAPSHOT_COLUMNS,
)

# pykrx 반환 한글 컬럼 (스펙 §6)
SRC_OPEN = "시가"
SRC_HIGH = "고가"
SRC_LOW = "저가"
SRC_CLOSE = "종가"
SRC_VOLUME = "거래량"
SRC_VALUE = "거래대금"
SRC_CHANGE_RATE = "등락률"
SRC_MARKET_CAP = "시가총액"
SRC_SHARES = "상장주식수"

# 각 조회 결과에서 반드시 존재해야 하는 컬럼
OHLCV_SOURCE_COLUMNS = (SRC_OPEN, SRC_HIGH, SRC_LOW, SRC_CLOSE, SRC_VOLUME, SRC_VALUE, SRC_CHANGE_RATE)
CAP_SOURCE_COLUMNS = (SRC_CLOSE, SRC_MARKET_CAP, SRC_SHARES)

# 한글 → 영문 컬럼 변환 (스펙 §7.2 컬럼 표준화 원칙)
OHLCV_RENAME = {
    SRC_OPEN: COL_OPEN,
    SRC_HIGH: COL_HIGH,
    SRC_LOW: COL_LOW,
    SRC_CLOSE: COL_CLOSE,
    SRC_VOLUME: COL_VOLUME,
    SRC_VALUE: COL_VALUE,
    SRC_CHANGE_RATE: COL_CHANGE_RATE,
}

# int64로 저장할 컬럼 (등락률만 float64)
INT_COLUMNS = (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOLUME, COL_VALUE, COL_MARKET_CAP, COL_SHARES)

# OHLC 가격 컬럼 (이상치 판정 대상)
PRICE_COLUMNS = (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE)

# 티커 자릿수 (선행 0 보존, 스펙 §7.2)
TICKER_LENGTH = 6

# 예외 메시지에 포함할 위반 종목 샘플 최대 개수
SAMPLE_LIMIT = 5


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], label: str) -> None:
    """조회 결과에 필요한 컬럼이 모두 있는지 확인한다.

    Args:
        frame: 확인할 DataFrame
        required: 필수 컬럼 목록
        label: 예외 메시지에 사용할 조회 이름

    Raises:
        ValueError: 필수 컬럼이 빠진 경우
    """
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} 조회 결과에 필요한 컬럼이 없습니다: {missing}")


def is_market_closed(ohlcv: pd.DataFrame) -> bool:
    """OHLCV 조회 결과가 휴장일 응답인지 판정한다.

    pykrx는 휴장일에 빈 결과가 아니라 **모든 가격이 0인 행**을 반환한다(실측, 스펙 §6).
    개별 거래정지 종목은 종가가 남으므로, 시가·고가·저가에 더해 종가까지 전부 0인 경우만
    휴장으로 본다. 실제 거래일에 전 종목의 종가가 0인 경우는 없다.

    Args:
        ohlcv: `get_market_ohlcv_by_ticker` 결과

    Returns:
        휴장일 응답이면 True

    Raises:
        ValueError: 가격 컬럼이 없는 경우
    """
    if ohlcv.empty:
        return True

    price_sources = (SRC_OPEN, SRC_HIGH, SRC_LOW, SRC_CLOSE)
    _require_columns(ohlcv, price_sources, "OHLCV")

    return bool((ohlcv[list(price_sources)] == 0).all(axis=None))


def build_snapshot(ohlcv: pd.DataFrame, cap: pd.DataFrame, market: str) -> pd.DataFrame:
    """OHLCV와 시가총액 조회 결과를 조인해 저장 스키마로 변환한다.

    빈 결과는 예외로 처리한다. 휴장 판정은 호출자(백필 루프)가 담당한다.

    Args:
        ohlcv: `get_market_ohlcv_by_ticker` 결과 (index=티커)
        cap: `get_market_cap_by_ticker` 결과 (index=티커)
        market: 시장 라벨 (KOSPI/KOSDAQ)

    Returns:
        `SNAPSHOT_COLUMNS` 순서의 스냅샷 DataFrame (index 초기화됨)

    Raises:
        ValueError: 시장 라벨·컬럼·티커 집합·종가·결측치·티커 형식이 규칙에 맞지 않는 경우
    """
    # 1. 입력 검증
    if market not in MARKETS:
        raise ValueError(f"수집 대상 시장이 아닙니다: {market!r} (허용: {MARKETS})")

    if ohlcv.empty or cap.empty:
        raise ValueError("조회 결과가 비어 있습니다 (휴장 판정은 호출자가 담당합니다)")

    _require_columns(ohlcv, OHLCV_SOURCE_COLUMNS, "OHLCV")
    _require_columns(cap, CAP_SOURCE_COLUMNS, "시가총액")

    # 2. 티커 인덱스 형식 확인 (정수 인덱스는 선행 0이 손실된 상태)
    if ohlcv.index.inferred_type != "string":
        raise ValueError(f"티커 인덱스가 문자열이 아닙니다: {ohlcv.index.inferred_type}")

    bad_tickers = [str(ticker) for ticker in ohlcv.index if len(str(ticker)) != TICKER_LENGTH]
    if bad_tickers:
        raise ValueError(f"티커는 {TICKER_LENGTH}자리여야 합니다: {bad_tickers[:SAMPLE_LIMIT]}")

    # 3. 두 조회 결과의 티커 집합 일치 확인 (교집합으로 조용히 축소하지 않는다)
    ohlcv_tickers = set(ohlcv.index)
    cap_tickers = set(cap.index)
    if ohlcv_tickers != cap_tickers:
        only_ohlcv = sorted(ohlcv_tickers - cap_tickers)[:SAMPLE_LIMIT]
        only_cap = sorted(cap_tickers - ohlcv_tickers)[:SAMPLE_LIMIT]
        raise ValueError(f"두 조회 결과의 티커 집합이 다릅니다 (OHLCV 전용 예: {only_ohlcv} / 시총 전용 예: {only_cap})")

    # 4. 종가 검산 — 두 조회의 시점이 어긋나면 값이 달라진다
    aligned_cap = cap.loc[ohlcv.index]
    close_mismatch = ohlcv[SRC_CLOSE].to_numpy() != aligned_cap[SRC_CLOSE].to_numpy()
    if bool(close_mismatch.any()):
        sample = [str(ticker) for ticker in ohlcv.index[close_mismatch][:SAMPLE_LIMIT]]
        raise ValueError(f"두 조회 결과의 종가가 일치하지 않습니다 ({int(close_mismatch.sum())}건, 예: {sample})")

    # 5. 결측치 확인 (보간 금지)
    source = pd.concat(
        [ohlcv[list(OHLCV_SOURCE_COLUMNS)], aligned_cap[[SRC_MARKET_CAP, SRC_SHARES]]],
        axis=1,
    )
    na_columns = [column for column in source.columns if bool(source[column].isna().any())]
    if na_columns:
        raise ValueError(f"조회 결과에 결측치가 있습니다: {na_columns}")

    # 6. 스키마 조립 (원본 변경 없음)
    result = ohlcv[list(OHLCV_SOURCE_COLUMNS)].rename(columns=OHLCV_RENAME).copy()
    result[COL_MARKET_CAP] = aligned_cap[SRC_MARKET_CAP]
    result[COL_SHARES] = aligned_cap[SRC_SHARES]
    result[COL_MARKET] = market
    result[COL_TICKER] = [str(ticker) for ticker in result.index]
    result = result.reset_index(drop=True)

    # 7. dtype 고정
    for column in INT_COLUMNS:
        result[column] = result[column].astype("int64")
    result[COL_CHANGE_RATE] = result[COL_CHANGE_RATE].astype("float64")

    return result[SNAPSHOT_COLUMNS].copy()


def validate_snapshot(snapshot: pd.DataFrame) -> tuple[str, ...]:
    """저장 직전 스냅샷의 무결성을 검증한다 (스펙 §8).

    시가·고가·저가가 0인 것은 이상치가 아니다. 거래정지일뿐 아니라 **정규장에서 가격이
    형성되지 않고 시간외 거래 등으로만 체결된 날**에도 KRX는 시가·고가·저가를 0으로 주면서
    종가·거래량·거래대금은 정상 값으로 반환한다(실측, 스펙 §8).

    따라서 반드시 있어야 하는 값은 **종가**다. 거래량이 있는데 종가가 없으면 이상치로 본다.
    등락률이 가격제한폭을 벗어난 경우는 권리락 등 특이일일 수 있으므로 경고로 남긴다.

    Args:
        snapshot: 검증할 스냅샷

    Returns:
        경고 메시지 튜플 (치명적이지 않은 이상 징후)

    Raises:
        ValueError: 컬럼 누락·음수 가격·거래 중 종가 0 이하·고가 < 저가인 경우
    """
    # 1. 구조 검증
    if snapshot.empty:
        raise ValueError("스냅샷이 비어 있습니다")

    missing = [column for column in SNAPSHOT_COLUMNS if column not in snapshot.columns]
    if missing:
        raise ValueError(f"스냅샷에 필요한 컬럼이 없습니다: {missing}")

    prices = snapshot[list(PRICE_COLUMNS)]

    # 2. 음수 가격은 거래정지 여부와 무관하게 이상치
    negative = prices.lt(0).any(axis=1)
    if bool(negative.any()):
        sample = snapshot.loc[negative, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        raise ValueError(f"음수 가격이 있는 종목이 있습니다 ({int(negative.sum())}건, 예: {sample})")

    # 3. 거래량이 있는데 종가가 없으면 이상치 (시가·고가·저가 0은 정상 패턴이므로 제외)
    invalid_close = (snapshot[COL_VOLUME] > 0) & (snapshot[COL_CLOSE] <= 0)
    if bool(invalid_close.any()):
        sample = snapshot.loc[invalid_close, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        raise ValueError(f"거래가 있는데 종가가 0 이하인 종목이 있습니다 ({int(invalid_close.sum())}건, 예: {sample})")

    # 4. 고가 < 저가 (둘 다 0인 행은 비교가 성립하지 않으므로 자연히 통과한다)
    high_low_invalid = snapshot[COL_HIGH] < snapshot[COL_LOW]
    if bool(high_low_invalid.any()):
        sample = snapshot.loc[high_low_invalid, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        raise ValueError(f"고가가 저가보다 낮은 종목이 있습니다 ({int(high_low_invalid.sum())}건, 예: {sample})")

    # 6. 가격제한폭 초과 등락률은 경고 (등락률은 % 단위이므로 비율을 % 로 변환해 비교)
    limit_pct = PRICE_LIMIT_RATE * 100
    beyond_limit = snapshot[COL_CHANGE_RATE].abs() > limit_pct
    return tuple(
        f"등락률이 가격제한폭({limit_pct:.0f}%)을 벗어났습니다: {ticker} {rate:.2f}%"
        for ticker, rate in zip(
            snapshot.loc[beyond_limit, COL_TICKER],
            snapshot.loc[beyond_limit, COL_CHANGE_RATE],
            strict=True,
        )
    )
