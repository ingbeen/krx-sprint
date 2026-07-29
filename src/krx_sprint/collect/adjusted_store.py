"""2단 수정주가 parquet 저장소

종목별 시계열 파일의 경로 규칙과 저장을 담당한다 (스펙 §7.2).

**1단 스냅샷과 달리 가변(mutable) 파일이다.** 분할·증자가 발생하면 KRX가 과거 전체를 다시
계산하므로 같은 티커 파일을 새 값으로 덮어써야 한다. 따라서 1단의 불변 파일 계약(저장 시
기존 파일이 있으면 예외)을 적용하지 않는다. 다만 재수집 조작 모델은 1단과 같게 유지한다 —
파일 존재가 곧 수집 완료 체크포인트이며, 다시 받으려면 파일을 지운 뒤 수집기를 실행한다.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from krx_sprint.common_constants import (
    ADJUSTED_COLUMNS,
    ADJUSTED_DIR,
    ADJUSTED_PRICE_COLUMNS,
    COL_DATE,
    TICKER_PATTERN,
)

# 저장 파일 확장자
FILE_SUFFIX = ".parquet"

# 수정주가 저장 소수점 자릿수 (루트 CLAUDE.md 반올림 규칙의 "수정주가 등 소수 가능 가격")
ADJUSTED_PRICE_DECIMALS = 6


def _validate_ticker(ticker: str) -> None:
    """티커 형식을 검증한다.

    Args:
        ticker: 검증할 티커

    Raises:
        ValueError: 6자리 영숫자(숫자·대문자 영문)가 아닌 경우
    """
    if TICKER_PATTERN.fullmatch(ticker) is None:
        raise ValueError(f"티커는 6자리 영숫자(숫자·대문자 영문)여야 합니다: {ticker!r}")


def adjusted_path(ticker: str, base_dir: Path = ADJUSTED_DIR) -> Path:
    """종목별 시계열 파일 경로를 만든다.

    Args:
        ticker: 대상 티커
        base_dir: 수정주가 루트 디렉토리

    Returns:
        `{base_dir}/{ticker}.parquet` 경로

    Raises:
        ValueError: 티커 형식이 규칙에 맞지 않는 경우
    """
    _validate_ticker(ticker)
    return base_dir / f"{ticker}{FILE_SUFFIX}"


def list_collected_tickers(base_dir: Path = ADJUSTED_DIR) -> set[str]:
    """저장된 시계열의 티커 집합을 반환한다.

    파일 존재 여부가 곧 수집 완료 체크포인트다.

    Args:
        base_dir: 수정주가 루트 디렉토리

    Returns:
        수집 완료된 티커 집합 (디렉토리가 없으면 빈 집합)

    Raises:
        ValueError: 파일명이 티커 규칙에 맞지 않는 경우
    """
    if not base_dir.exists():
        return set()

    collected: set[str] = set()
    for path in sorted(base_dir.glob(f"*{FILE_SUFFIX}")):
        if TICKER_PATTERN.fullmatch(path.stem) is None:
            raise ValueError(f"수정주가 파일명이 티커 규칙(6자리 영숫자)에 맞지 않습니다: {path}")
        collected.add(path.stem)

    return collected


def load_adjusted(ticker: str, base_dir: Path = ADJUSTED_DIR, *, since: date | None = None) -> pd.DataFrame:
    """저장된 종목별 시계열을 읽는다.

    `since`를 주면 그 일자 이전 행을 잘라낸다. 2단은 1단이 제외한 시장의 이전상장 이력까지
    담고 있고 그 구간은 가격 축이 달라 경계에서 가짜 갭이 생기므로, 유니버스 밖 구간은
    **소비 시점에** 거른다(스펙 §8.2). 저장 데이터는 KRX 원본 그대로 유지한다.

    기본값은 절단하지 않는다 — 품질 검사처럼 원본 그대로가 필요한 호출자가 있기 때문이다.

    Args:
        ticker: 대상 티커
        base_dir: 수정주가 루트 디렉토리
        since: 이 일자부터 남긴다 (`None`이면 전량 반환). 보통 1단 최초 등장일

    Returns:
        시계열 DataFrame (절단 시 인덱스 초기화)

    Raises:
        FileNotFoundError: 해당 티커 파일이 없는 경우
        ValueError: 저장된 컬럼이 스키마와 다른 경우
    """
    path = adjusted_path(ticker, base_dir)
    if not path.exists():
        raise FileNotFoundError(f"수정주가 파일이 없습니다: {path}")

    frame = pd.read_parquet(path)
    if list(frame.columns) != ADJUSTED_COLUMNS:
        raise ValueError(f"저장된 시계열 컬럼이 스키마와 다릅니다 ({path}): {list(frame.columns)}")

    if since is None:
        return frame

    return frame[frame[COL_DATE] >= pd.Timestamp(since)].reset_index(drop=True)


def save_adjusted(frame: pd.DataFrame, ticker: str, base_dir: Path = ADJUSTED_DIR) -> Path:
    """종목별 시계열을 parquet으로 저장한다.

    같은 티커 파일이 이미 있으면 덮어쓴다 (수정주가는 액션 발생 시 과거가 재계산된다).

    Args:
        frame: 저장할 시계열 (`ADJUSTED_COLUMNS` 스키마)
        ticker: 대상 티커
        base_dir: 수정주가 루트 디렉토리

    Returns:
        저장된 파일 경로

    Raises:
        ValueError: 시계열이 비었거나 컬럼이 스키마와 다른 경우
    """
    # 1. 저장 대상 검증
    if frame.empty:
        raise ValueError(f"시계열이 비어 있어 저장하지 않습니다: {ticker}")

    if list(frame.columns) != ADJUSTED_COLUMNS:
        raise ValueError(f"시계열 컬럼이 스키마와 다릅니다 ({ticker}): {list(frame.columns)}")

    # 2. 저장 직전 반올림 후 기록 (round는 복사본을 반환하므로 원본은 변경되지 않는다)
    path = adjusted_path(ticker, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rounded = frame.round({column: ADJUSTED_PRICE_DECIMALS for column in ADJUSTED_PRICE_COLUMNS})
    rounded.to_parquet(path, index=False)

    return path
