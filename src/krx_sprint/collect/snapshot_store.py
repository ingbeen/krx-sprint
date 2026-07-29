"""스냅샷 parquet 저장소

일자별 스냅샷 파일의 경로 규칙과 저장을 담당한다 (스펙 §7.2).

불변 파일 계약(스펙 §7.1): 한 번 저장한 일자 파일은 다시 쓰지 않는다.
관례가 아니라 저장 함수에서 예외로 강제한다 — 재실행이 잦은 백필에서 과거 파일이
덮이면 git 히스토리가 오염되기 때문이다.
"""

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from krx_sprint.common_constants import COL_CHANGE_RATE, COL_TICKER, SNAPSHOT_COLUMNS, SNAPSHOTS_DIR

# 파일명 일자 형식
DATE_FORMAT = "%Y%m%d"

# 등락률 저장 소수점 자릿수 (백분율)
CHANGE_RATE_DECIMALS = 2


def snapshot_path(target: date, base_dir: Path = SNAPSHOTS_DIR) -> Path:
    """일자별 스냅샷 파일 경로를 만든다.

    Args:
        target: 대상 일자
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        `{base_dir}/{YYYY}/{YYYYMMDD}.parquet` 경로
    """
    return base_dir / f"{target.year:04d}" / f"{target.strftime(DATE_FORMAT)}.parquet"


def list_collected_dates(base_dir: Path = SNAPSHOTS_DIR) -> set[date]:
    """저장된 스냅샷의 일자 집합을 반환한다.

    파일 존재 여부가 곧 수집 완료 체크포인트다 (스펙 §7.3).

    Args:
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        수집 완료된 일자 집합 (디렉토리가 없으면 빈 집합)

    Raises:
        ValueError: 파일명이 일자 규칙에 맞지 않는 경우
    """
    if not base_dir.exists():
        return set()

    collected: set[date] = set()
    for path in sorted(base_dir.glob("*/*.parquet")):
        try:
            collected.add(datetime.strptime(path.stem, DATE_FORMAT).date())
        except ValueError as error:
            raise ValueError(f"스냅샷 파일명이 일자 규칙({DATE_FORMAT})에 맞지 않습니다: {path}") from error

    return collected


def list_all_tickers(base_dir: Path = SNAPSHOTS_DIR) -> set[str]:
    """저장된 모든 스냅샷의 티커 합집합을 반환한다.

    일별 스냅샷의 합집합이 곧 유니버스다 — 그날 거래된 종목이 자동으로 포함되므로
    이후 폐지된 종목도 폐지 전 데이터가 보존된다 (스펙 §3.2 생존편향 방지).

    Args:
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        등장한 적 있는 모든 티커 (스냅샷이 없으면 빈 집합)

    Raises:
        ValueError: 저장된 컬럼이 스키마와 다른 경우
    """
    tickers: set[str] = set()
    for target in sorted(list_collected_dates(base_dir=base_dir)):
        snapshot = load_snapshot(target, base_dir=base_dir)
        tickers.update(str(ticker) for ticker in snapshot[COL_TICKER])

    return tickers


def list_first_seen_dates(base_dir: Path = SNAPSHOTS_DIR) -> dict[str, date]:
    """티커별 1단 최초 등장일을 반환한다.

    2단 시계열은 1단이 제외한 시장(코넥스)의 이전상장 이력까지 담고 있고 그 구간은
    가격 축이 다르다. 유니버스는 1단 스냅샷의 합집합으로 정의되므로(스펙 §3.2),
    이 일자가 곧 **2단을 소비할 때 절단할 기준**이다.

    Args:
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        티커 → 최초 등장 일자 (스냅샷이 없으면 빈 dict)

    Raises:
        ValueError: 저장된 컬럼이 스키마와 다른 경우
    """
    first_seen: dict[str, date] = {}
    for target in sorted(list_collected_dates(base_dir=base_dir)):
        snapshot = load_snapshot(target, base_dir=base_dir)
        for ticker in snapshot[COL_TICKER]:
            first_seen.setdefault(str(ticker), target)

    return first_seen


def load_snapshot(target: date, base_dir: Path = SNAPSHOTS_DIR) -> pd.DataFrame:
    """저장된 일자별 스냅샷을 읽는다.

    Args:
        target: 대상 일자
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        스냅샷 DataFrame

    Raises:
        FileNotFoundError: 해당 일자 파일이 없는 경우
        ValueError: 저장된 컬럼이 스키마와 다른 경우
    """
    path = snapshot_path(target, base_dir)
    if not path.exists():
        raise FileNotFoundError(f"스냅샷 파일이 없습니다: {path}")

    snapshot = pd.read_parquet(path)
    if list(snapshot.columns) != SNAPSHOT_COLUMNS:
        raise ValueError(f"저장된 스냅샷 컬럼이 스키마와 다릅니다 ({path}): {list(snapshot.columns)}")

    return snapshot


def save_snapshot(snapshot: pd.DataFrame, target: date, base_dir: Path = SNAPSHOTS_DIR) -> Path:
    """스냅샷을 일자별 parquet으로 저장한다.

    Args:
        snapshot: 저장할 스냅샷 (`SNAPSHOT_COLUMNS` 스키마)
        target: 대상 일자
        base_dir: 스냅샷 루트 디렉토리

    Returns:
        저장된 파일 경로

    Raises:
        ValueError: 스냅샷이 비었거나 컬럼이 스키마와 다른 경우
        FileExistsError: 해당 일자 파일이 이미 존재하는 경우 (불변 파일 계약)
    """
    # 1. 저장 대상 검증
    if snapshot.empty:
        raise ValueError("스냅샷이 비어 있어 저장하지 않습니다")

    if list(snapshot.columns) != SNAPSHOT_COLUMNS:
        raise ValueError(f"스냅샷 컬럼이 스키마와 다릅니다: {list(snapshot.columns)}")

    # 2. 불변 파일 계약
    path = snapshot_path(target, base_dir)
    if path.exists():
        raise FileExistsError(f"이미 저장된 일자입니다 (불변 파일 계약): {path}")

    # 3. 저장 직전 반올림 후 기록
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.round({COL_CHANGE_RATE: CHANGE_RATE_DECIMALS}).to_parquet(path, index=False)

    return path
