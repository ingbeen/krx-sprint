"""종목별 수정주가 백필 루프

미수집 종목만 순차 조회해 저장하고, 결과를 성공/실패로 분기한다 (스펙 §4 2단, §9 견고성).

조회 함수는 인자로 주입받는다. pykrx 의존을 이 모듈에 두지 않아야
테스트에서 네트워크 없이 루프 전체를 검증할 수 있다 (1단 백필과 같은 설계 계약).

1단과 달리 휴장 개념이 없어 실패 기록 파일을 두지 않는다 — 파일이 없다는 것이 곧
미수집이므로 다음 실행에서 자동으로 다시 시도된다.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from krx_sprint.collect.adjusted import build_adjusted, validate_adjusted
from krx_sprint.collect.adjusted_store import list_collected_tickers, save_adjusted
from krx_sprint.common_constants import (
    ADJUSTED_DIR,
    MAX_ATTEMPT_COUNT,
    REQUEST_DELAY_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
)
from krx_sprint.utils.logger import get_logger

logger = get_logger()

# (조회 시작일 YYYYMMDD, 조회 종료일 YYYYMMDD, 티커) → 조회 결과
AdjustedFetcher = Callable[[str, str, str], pd.DataFrame]

# 대기 함수 (테스트에서 실제 대기를 제거하기 위해 주입받는다)
SleepFunction = Callable[[float], None]

# pykrx 조회 인자의 일자 형식
DATE_FORMAT = "%Y%m%d"

# 진행 상황 로그 간격 (종목 수)
PROGRESS_INTERVAL = 100


@dataclass(frozen=True)
class AdjustedBackfillResult:
    """2단 백필 실행 결과

    Attributes:
        collected: 새로 수집해 저장한 티커
        failures: 재시도를 소진하고 실패한 티커
    """

    collected: tuple[str, ...]
    failures: tuple[str, ...]


def _fetch_with_retry(
    fetch: AdjustedFetcher,
    from_date: str,
    to_date: str,
    ticker: str,
    max_attempts: int,
    sleep: SleepFunction,
) -> pd.DataFrame:
    """지수 백오프로 재시도하며 조회한다 (스펙 §9).

    Args:
        fetch: 조회 함수
        from_date: 조회 시작일 (YYYYMMDD)
        to_date: 조회 종료일 (YYYYMMDD)
        ticker: 대상 티커
        max_attempts: 최대 시도 횟수 (최초 1회 포함)
        sleep: 대기 함수

    Returns:
        조회 결과 DataFrame

    Raises:
        RuntimeError: 재시도 루프가 값을 반환하지도 예외를 던지지도 못한 경우
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch(from_date, to_date, ticker)
        except Exception as error:
            if attempt >= max_attempts:
                raise
            wait_seconds = RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            logger.warning("조회 실패 (%s, %d/%d): %s", ticker, attempt, max_attempts, error)
            sleep(wait_seconds)

    raise RuntimeError(f"내부 불변조건 위반: 재시도 루프가 종료되지 않았습니다 (max_attempts={max_attempts})")


def backfill_adjusted(
    tickers: Sequence[str],
    fetch: AdjustedFetcher,
    start: date,
    end: date,
    *,
    base_dir: Path = ADJUSTED_DIR,
    max_attempts: int = MAX_ATTEMPT_COUNT,
    delay_seconds: float = REQUEST_DELAY_SECONDS,
    sleep: SleepFunction = time.sleep,
) -> AdjustedBackfillResult:
    """대상 종목의 수정주가 시계열을 순차 수집해 저장한다.

    이미 저장된 종목은 조회하지 않고 건너뛴다(체크포인트). 한 종목의 실패가
    나머지 종목을 막지 않도록 종목 단위로 격리한다.

    Args:
        tickers: 수집 대상 티커 (오름차순 권장)
        fetch: 수정주가 조회 함수
        start: 조회 시작 일자
        end: 조회 종료 일자 (포함)
        base_dir: 수정주가 루트 디렉토리
        max_attempts: 조회 최대 시도 횟수
        delay_seconds: 요청 간 지연 (초)
        sleep: 대기 함수

    Returns:
        수집·실패 티커를 담은 결과

    Raises:
        ValueError: 시도 횟수가 1 미만이거나 시작일이 종료일보다 늦은 경우
    """
    # 1. 파라미터 검증
    if max_attempts < 1:
        raise ValueError(f"시도 횟수는 1 이상이어야 합니다: {max_attempts}")

    if start > end:
        raise ValueError(f"시작일이 종료일보다 늦습니다: {start} > {end}")

    from_date = start.strftime(DATE_FORMAT)
    to_date = end.strftime(DATE_FORMAT)

    already_collected = list_collected_tickers(base_dir=base_dir)
    collected: list[str] = []
    failures: list[str] = []

    # 2. 종목별 수집
    for index, ticker in enumerate(tickers, start=1):
        if ticker in already_collected:
            logger.debug("이미 수집된 종목 건너뜀: %s", ticker)
            continue

        started_at = time.monotonic()
        try:
            series = _fetch_with_retry(fetch, from_date, to_date, ticker, max_attempts, sleep)
            sleep(delay_seconds)

            frame = build_adjusted(series, ticker)
            for warning in validate_adjusted(frame):
                logger.warning("%s %s", ticker, warning)

            save_adjusted(frame, ticker, base_dir=base_dir)
            collected.append(ticker)
            logger.debug("수집 완료: %s (%d행, %.1f초)", ticker, len(frame), time.monotonic() - started_at)

        except Exception as error:
            # 종목 단위로 격리해 나머지 종목을 계속 수집한다 (스펙 §9)
            failures.append(ticker)
            logger.warning("수집 실패: %s (%s)", ticker, error)

        if index % PROGRESS_INTERVAL == 0:
            logger.debug("수집 진행: %d/%d종목", index, len(tickers))

    return AdjustedBackfillResult(tuple(collected), tuple(failures))
