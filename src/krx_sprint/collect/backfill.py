"""일자별 스냅샷 백필 루프

누락 일자만 순차 수집하고, 결과를 성공/휴장/실패로 분기해 기록한다 (스펙 §7.3·§9).

조회 함수는 인자로 주입받는다. pykrx 의존을 이 모듈에 두지 않아야
테스트에서 네트워크 없이 루프 전체를 검증할 수 있다.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from krx_sprint.collect.meta_store import record_failure, record_holiday, remove_failure
from krx_sprint.collect.snapshot import build_snapshot, is_market_closed, validate_snapshot
from krx_sprint.collect.snapshot_store import list_collected_dates, save_snapshot
from krx_sprint.common_constants import (
    FAILURES_JSON_PATH,
    HOLIDAYS_JSON_PATH,
    MARKETS,
    MAX_ATTEMPT_COUNT,
    REQUEST_DELAY_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    SNAPSHOTS_DIR,
)
from krx_sprint.utils.logger import get_logger

logger = get_logger()

# (조회 일자 YYYYMMDD, 시장) → 조회 결과
SnapshotFetcher = Callable[[str, str], pd.DataFrame]

# 대기 함수 (테스트에서 실제 대기를 제거하기 위해 주입받는다)
SleepFunction = Callable[[float], None]

# pykrx 조회 인자의 일자 형식
DATE_FORMAT = "%Y%m%d"


@dataclass(frozen=True)
class BackfillPaths:
    """백필이 사용하는 저장 경로 묶음

    Attributes:
        snapshots_dir: 스냅샷 parquet 루트 디렉토리
        holidays_path: 휴장 일자 기록 파일
        failures_path: 실패 일자 기록 파일
    """

    snapshots_dir: Path
    holidays_path: Path
    failures_path: Path

    @classmethod
    def default(cls) -> "BackfillPaths":
        """프로젝트 표준 저장 경로를 사용하는 인스턴스를 만든다."""
        return cls(
            snapshots_dir=SNAPSHOTS_DIR,
            holidays_path=HOLIDAYS_JSON_PATH,
            failures_path=FAILURES_JSON_PATH,
        )


@dataclass(frozen=True)
class BackfillResult:
    """백필 실행 결과

    Attributes:
        collected: 새로 수집해 저장한 일자
        holidays: 휴장으로 판정한 일자
        failures: 재시도를 소진하고 실패한 일자
    """

    collected: tuple[date, ...]
    holidays: tuple[date, ...]
    failures: tuple[date, ...]


def _fetch_with_retry(
    fetch: SnapshotFetcher,
    date_str: str,
    market: str,
    max_attempts: int,
    sleep: SleepFunction,
) -> pd.DataFrame:
    """지수 백오프로 재시도하며 조회한다 (스펙 §9).

    Args:
        fetch: 조회 함수
        date_str: 조회 일자 (YYYYMMDD)
        market: 조회 시장
        max_attempts: 최대 시도 횟수 (최초 1회 포함)
        sleep: 대기 함수

    Returns:
        조회 결과 DataFrame

    Raises:
        RuntimeError: 재시도 루프가 값을 반환하지도 예외를 던지지도 못한 경우
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch(date_str, market)
        except Exception as error:
            if attempt >= max_attempts:
                raise
            wait_seconds = RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            logger.warning("조회 실패 (%s %s, %d/%d): %s", date_str, market, attempt, max_attempts, error)
            sleep(wait_seconds)

    raise RuntimeError(f"내부 불변조건 위반: 재시도 루프가 종료되지 않았습니다 (max_attempts={max_attempts})")


def _collect_markets(
    target: date,
    fetch_ohlcv: SnapshotFetcher,
    fetch_cap: SnapshotFetcher,
    markets: Sequence[str],
    max_attempts: int,
    delay_seconds: float,
    sleep: SleepFunction,
) -> list[pd.DataFrame]:
    """한 일자의 모든 시장을 조회해 스냅샷 조각을 만든다.

    Args:
        target: 대상 일자
        fetch_ohlcv: OHLCV 조회 함수
        fetch_cap: 시가총액 조회 함수
        markets: 조회 시장 목록
        max_attempts: 최대 시도 횟수
        delay_seconds: 요청 간 지연 (초)
        sleep: 대기 함수

    Returns:
        시장별 스냅샷 조각 목록 (모든 시장이 비면 빈 목록)
    """
    date_str = target.strftime(DATE_FORMAT)
    frames: list[pd.DataFrame] = []

    for market in markets:
        ohlcv = _fetch_with_retry(fetch_ohlcv, date_str, market, max_attempts, sleep)
        sleep(delay_seconds)
        cap = _fetch_with_retry(fetch_cap, date_str, market, max_attempts, sleep)
        sleep(delay_seconds)

        # 휴장일 응답이면 이 시장은 건너뛴다 — 최종 휴장 판정은 모든 시장을 본 뒤에 한다.
        # pykrx는 휴장일에 빈 결과가 아니라 값이 0으로 채워진 행을 반환하므로 값으로 판정해야 한다
        if is_market_closed(ohlcv):
            continue

        frames.append(build_snapshot(ohlcv, cap, market))

    return frames


def backfill(
    targets: Sequence[date],
    fetch_ohlcv: SnapshotFetcher,
    fetch_cap: SnapshotFetcher,
    paths: BackfillPaths,
    *,
    markets: Sequence[str] = MARKETS,
    max_attempts: int = MAX_ATTEMPT_COUNT,
    delay_seconds: float = REQUEST_DELAY_SECONDS,
    sleep: SleepFunction = time.sleep,
) -> BackfillResult:
    """대상 일자를 순차 수집해 저장한다.

    이미 저장된 일자는 조회하지 않고 건너뛴다(체크포인트). 한 일자의 실패가
    나머지 일자를 막지 않도록 일자 단위로 격리한다.

    Args:
        targets: 수집 대상 일자 (오름차순 권장)
        fetch_ohlcv: OHLCV 조회 함수
        fetch_cap: 시가총액 조회 함수
        paths: 저장 경로 묶음
        markets: 조회 시장 목록
        max_attempts: 조회 최대 시도 횟수
        delay_seconds: 요청 간 지연 (초)
        sleep: 대기 함수

    Returns:
        수집·휴장·실패 일자를 담은 결과

    Raises:
        ValueError: 시도 횟수가 1 미만이거나 시장 목록이 빈 경우
    """
    # 1. 파라미터 검증
    if max_attempts < 1:
        raise ValueError(f"시도 횟수는 1 이상이어야 합니다: {max_attempts}")

    if not markets:
        raise ValueError("수집 대상 시장이 비어 있습니다")

    already_collected = list_collected_dates(base_dir=paths.snapshots_dir)
    collected: list[date] = []
    holidays: list[date] = []
    failures: list[date] = []

    # 2. 일자별 수집
    for target in targets:
        if target in already_collected:
            logger.debug("이미 수집된 일자 건너뜀: %s", target)
            continue

        started_at = time.monotonic()
        try:
            frames = _collect_markets(target, fetch_ohlcv, fetch_cap, markets, max_attempts, delay_seconds, sleep)

            # 모든 시장이 비었을 때만 휴장으로 판정한다 (재시도를 모두 소진한 뒤이므로 안전)
            if not frames:
                record_holiday(paths.holidays_path, target)
                holidays.append(target)
                logger.debug("휴장 판정: %s", target)
                continue

            snapshot = pd.concat(frames, ignore_index=True)
            for warning in validate_snapshot(snapshot):
                logger.warning("%s %s", target, warning)

            save_snapshot(snapshot, target, base_dir=paths.snapshots_dir)
            remove_failure(paths.failures_path, target)
            collected.append(target)
            logger.debug("수집 완료: %s (%d종목, %.1f초)", target, len(snapshot), time.monotonic() - started_at)

        except Exception as error:
            # 일자 단위로 격리해 나머지 일자를 계속 수집한다 (스펙 §9)
            record_failure(paths.failures_path, target, f"{type(error).__name__}: {error}")
            failures.append(target)
            logger.warning("수집 실패: %s (%s)", target, error)

    return BackfillResult(tuple(collected), tuple(holidays), tuple(failures))
