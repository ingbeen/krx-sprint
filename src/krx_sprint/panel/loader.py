"""통합 패널 캐시 접근

연도별 parquet으로 저장된 패널을 읽고, 캐시가 원천 데이터와 어긋나지 않았는지 확인한다.

**캐시가 낡았으면 조용히 재생성하지 않고 예외를 던진다.** 자동 재생성은 "지금 보는 결과가
어느 데이터에서 나왔는가"를 흐린다. 빌드는 사용자가 명시적으로 실행한다 (docs/COMMANDS.md).

빌드 지문(`_build_meta.json`)의 읽기·쓰기를 여기서 함께 담당한다 — 쓰는 쪽(`build`)과
읽는 쪽이 같은 정의를 공유해야 지문이 의미를 갖기 때문이다.
"""

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from krx_sprint.collect.adjusted_store import list_collected_tickers
from krx_sprint.collect.snapshot_store import list_collected_dates
from krx_sprint.common_constants import (
    ADJUSTED_DIR,
    COL_DATE,
    COL_TICKER,
    PANEL_COLUMNS,
    PANEL_DIR,
    SNAPSHOTS_DIR,
)

# 패널 스키마 버전. 컬럼 구성이 바뀌면 올린다 — 낡은 캐시가 새 코드에 읽히는 것을 막는다
SCHEMA_VERSION = 1

# 빌드 지문 파일명
BUILD_META_FILENAME = "_build_meta.json"

# 연도별 파일 형식
YEAR_FILE_SUFFIX = ".parquet"

# 빌드 지문 JSON 키
KEY_SCHEMA_VERSION = "schema_version"
KEY_SNAPSHOT_COUNT = "snapshot_count"
KEY_SNAPSHOT_LAST_DATE = "snapshot_last_date"
KEY_ADJUSTED_COUNT = "adjusted_count"
KEY_ROW_COUNT = "row_count"
KEY_TICKER_COUNT = "ticker_count"
KEY_BUILT_AT = "built_at"

# 재빌드 안내 (예외 메시지에 실행 방법을 함께 담는다)
REBUILD_HINT = "scripts/data/build_panel.py 로 패널을 다시 빌드하십시오"


@dataclass(frozen=True)
class PanelFingerprint:
    """캐시가 어떤 원천 데이터에서 나왔는지 식별하는 지문

    파일 내용 해시가 아니라 **개수와 최종 일자**로 판정한다. 1단은 불변 파일 계약이 있어
    개수와 마지막 일자만으로 충분하고, 2단은 종목 수가 유니버스와 묶여 있다.

    Attributes:
        schema_version: 패널 스키마 버전
        snapshot_count: 1단 스냅샷 파일 수
        snapshot_last_date: 1단 최종 수집 일자
        adjusted_count: 2단 수정주가 파일 수
    """

    schema_version: int
    snapshot_count: int
    snapshot_last_date: date
    adjusted_count: int


@dataclass(frozen=True)
class PanelBuildMeta:
    """빌드 지문과 산출물 규모

    Attributes:
        fingerprint: 원천 데이터 지문
        row_count: 패널 행 수
        ticker_count: 패널 종목 수
        built_at: 빌드 시각 (ISO 8601)
    """

    fingerprint: PanelFingerprint
    row_count: int
    ticker_count: int
    built_at: str

    @property
    def snapshot_last_date(self) -> date:
        """1단 최종 수집 일자."""
        return self.fingerprint.snapshot_last_date


def build_meta_path(panel_dir: Path = PANEL_DIR) -> Path:
    """빌드 지문 파일 경로를 만든다.

    Args:
        panel_dir: 패널 캐시 루트

    Returns:
        지문 파일 경로
    """
    return panel_dir / BUILD_META_FILENAME


def panel_year_path(year: int, panel_dir: Path = PANEL_DIR) -> Path:
    """연도별 패널 파일 경로를 만든다.

    Args:
        year: 대상 연도
        panel_dir: 패널 캐시 루트

    Returns:
        `{panel_dir}/{YYYY}.parquet` 경로
    """
    return panel_dir / f"{year:04d}{YEAR_FILE_SUFFIX}"


def list_panel_years(panel_dir: Path = PANEL_DIR) -> list[int]:
    """저장된 패널의 연도 목록을 반환한다.

    Args:
        panel_dir: 패널 캐시 루트

    Returns:
        연도 오름차순 목록 (디렉토리가 없으면 빈 목록)

    Raises:
        ValueError: 파일명이 연도 규칙에 맞지 않는 경우
    """
    if not panel_dir.exists():
        return []

    years: list[int] = []
    for path in sorted(panel_dir.glob(f"*{YEAR_FILE_SUFFIX}")):
        if not path.stem.isdigit():
            raise ValueError(f"패널 파일명이 연도 규칙에 맞지 않습니다: {path}")
        years.append(int(path.stem))

    return years


def current_fingerprint(
    snapshot_dir: Path = SNAPSHOTS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> PanelFingerprint:
    """현재 `storage/` 상태의 지문을 계산한다.

    Args:
        snapshot_dir: 1단 스냅샷 루트
        adjusted_dir: 2단 수정주가 루트

    Returns:
        원천 데이터 지문

    Raises:
        ValueError: 1단 스냅샷이 하나도 없는 경우
    """
    dates = list_collected_dates(base_dir=snapshot_dir)
    if not dates:
        raise ValueError("수집된 1단 스냅샷이 없습니다")

    return PanelFingerprint(
        schema_version=SCHEMA_VERSION,
        snapshot_count=len(dates),
        snapshot_last_date=max(dates),
        adjusted_count=len(list_collected_tickers(base_dir=adjusted_dir)),
    )


def write_build_meta(meta: PanelBuildMeta, panel_dir: Path = PANEL_DIR) -> Path:
    """빌드 지문을 저장한다.

    Args:
        meta: 저장할 지문
        panel_dir: 패널 캐시 루트

    Returns:
        저장된 파일 경로
    """
    path = build_meta_path(panel_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                KEY_SCHEMA_VERSION: meta.fingerprint.schema_version,
                KEY_SNAPSHOT_COUNT: meta.fingerprint.snapshot_count,
                KEY_SNAPSHOT_LAST_DATE: meta.fingerprint.snapshot_last_date.isoformat(),
                KEY_ADJUSTED_COUNT: meta.fingerprint.adjusted_count,
                KEY_ROW_COUNT: meta.row_count,
                KEY_TICKER_COUNT: meta.ticker_count,
                KEY_BUILT_AT: meta.built_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


def read_build_meta(panel_dir: Path = PANEL_DIR) -> PanelBuildMeta:
    """저장된 빌드 지문을 읽는다.

    Args:
        panel_dir: 패널 캐시 루트

    Returns:
        빌드 지문

    Raises:
        FileNotFoundError: 지문 파일이 없는 경우
        ValueError: 지문 형식이 규칙과 다른 경우
    """
    path = build_meta_path(panel_dir)
    if not path.exists():
        raise FileNotFoundError(f"패널 캐시가 없습니다 ({path}). {REBUILD_HINT}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        key
        for key in (
            KEY_SCHEMA_VERSION,
            KEY_SNAPSHOT_COUNT,
            KEY_SNAPSHOT_LAST_DATE,
            KEY_ADJUSTED_COUNT,
            KEY_ROW_COUNT,
            KEY_TICKER_COUNT,
            KEY_BUILT_AT,
        )
        if key not in payload
    ]
    if missing:
        raise ValueError(f"패널 지문에 필요한 키가 없습니다 ({path}): {missing}. {REBUILD_HINT}")

    return PanelBuildMeta(
        fingerprint=PanelFingerprint(
            schema_version=int(payload[KEY_SCHEMA_VERSION]),
            snapshot_count=int(payload[KEY_SNAPSHOT_COUNT]),
            snapshot_last_date=date.fromisoformat(str(payload[KEY_SNAPSHOT_LAST_DATE])),
            adjusted_count=int(payload[KEY_ADJUSTED_COUNT]),
        ),
        row_count=int(payload[KEY_ROW_COUNT]),
        ticker_count=int(payload[KEY_TICKER_COUNT]),
        built_at=str(payload[KEY_BUILT_AT]),
    )


def verify_cache(
    panel_dir: Path = PANEL_DIR,
    snapshot_dir: Path = SNAPSHOTS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> PanelBuildMeta:
    """캐시가 현재 원천 데이터와 일치하는지 확인한다.

    Args:
        panel_dir: 패널 캐시 루트
        snapshot_dir: 1단 스냅샷 루트
        adjusted_dir: 2단 수정주가 루트

    Returns:
        검증을 통과한 빌드 지문

    Raises:
        FileNotFoundError: 캐시가 없는 경우
        ValueError: 지문이 어긋난 경우
    """
    meta = read_build_meta(panel_dir)
    current = current_fingerprint(snapshot_dir, adjusted_dir)

    if meta.fingerprint != current:
        raise ValueError(f"패널 캐시가 원천 데이터와 어긋납니다. 캐시={meta.fingerprint}, 현재={current}. {REBUILD_HINT}")

    return meta


def load_panel(
    *,
    start: date | None = None,
    end: date | None = None,
    tickers: Collection[str] | None = None,
    panel_dir: Path = PANEL_DIR,
    snapshot_dir: Path = SNAPSHOTS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> pd.DataFrame:
    """통합 패널을 읽는다.

    구간을 지정하면 해당 연도 파일만 읽는다. 반환 순서는 (일자, 티커) 오름차순으로 고정한다 —
    엔진의 일별 루프가 이 순서를 전제한다.

    Args:
        start: 시작 일자 (포함). None이면 처음부터
        end: 종료 일자 (포함). None이면 끝까지
        tickers: 대상 티커. None이면 전종목
        panel_dir: 패널 캐시 루트
        snapshot_dir: 1단 스냅샷 루트 (지문 검증용)
        adjusted_dir: 2단 수정주가 루트 (지문 검증용)

    Returns:
        패널 DataFrame (`PANEL_COLUMNS` 스키마)

    Raises:
        FileNotFoundError: 캐시가 없는 경우
        ValueError: 지문이 어긋나거나 구간이 뒤집혔거나 저장 스키마가 다른 경우
    """
    if start is not None and end is not None and start > end:
        raise ValueError(f"구간이 뒤집혔습니다: {start.isoformat()} ~ {end.isoformat()}")

    verify_cache(panel_dir, snapshot_dir, adjusted_dir)

    years = [
        year
        for year in list_panel_years(panel_dir)
        if (start is None or year >= start.year) and (end is None or year <= end.year)
    ]
    if not years:
        return pd.DataFrame(columns=PANEL_COLUMNS)

    frames = [_read_year(year, panel_dir) for year in years]
    panel = pd.concat(frames, ignore_index=True)

    if start is not None:
        panel = panel[panel[COL_DATE] >= pd.Timestamp(start)]
    if end is not None:
        panel = panel[panel[COL_DATE] <= pd.Timestamp(end)]
    if tickers is not None:
        panel = panel[panel[COL_TICKER].isin(set(tickers))]

    return panel.sort_values([COL_DATE, COL_TICKER]).reset_index(drop=True)


def load_trading_days(
    panel_dir: Path = PANEL_DIR,
    snapshot_dir: Path = SNAPSHOTS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> list[date]:
    """패널에 담긴 거래일을 오름차순으로 반환한다.

    비용 모델의 결제일 환산(설계 §8.2)이 이 캘린더를 쓴다.

    Args:
        panel_dir: 패널 캐시 루트
        snapshot_dir: 1단 스냅샷 루트 (지문 검증용)
        adjusted_dir: 2단 수정주가 루트 (지문 검증용)

    Returns:
        거래일 목록

    Raises:
        FileNotFoundError: 캐시가 없는 경우
        ValueError: 지문이 어긋난 경우
    """
    verify_cache(panel_dir, snapshot_dir, adjusted_dir)

    days: set[date] = set()
    for year in list_panel_years(panel_dir):
        column = pd.read_parquet(panel_year_path(year, panel_dir), columns=[COL_DATE])
        days.update(value.date() for value in column[COL_DATE])

    return sorted(days)


def _read_year(year: int, panel_dir: Path) -> pd.DataFrame:
    """연도별 패널 파일을 읽고 스키마를 확인한다.

    Args:
        year: 대상 연도
        panel_dir: 패널 캐시 루트

    Returns:
        해당 연도 패널

    Raises:
        ValueError: 저장된 컬럼이 스키마와 다른 경우
    """
    path = panel_year_path(year, panel_dir)
    frame = pd.read_parquet(path)

    if list(frame.columns) != PANEL_COLUMNS:
        raise ValueError(f"저장된 패널 컬럼이 스키마와 다릅니다 ({path}): {list(frame.columns)}. {REBUILD_HINT}")

    return frame
