"""티커 → 종목명 매핑 저장소

수집 시점의 종목명을 `names.csv`에 누적한다 (스펙 §7.2·§10.4).
git diff로 변경 이력을 볼 수 있도록 parquet이 아니라 CSV로 저장한다.

과거 시점 종목명은 담기지 않는다 — 최신 일자 기준 매핑만 병합하므로,
이후 사명이 바뀌면 새 이름으로 갱신된다.
"""

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from krx_sprint.common_constants import COL_TICKER, NAMES_CSV_PATH

# names.csv 컬럼
COL_NAME = "name"
NAMES_COLUMNS = [COL_TICKER, COL_NAME]


def load_names(path: Path = NAMES_CSV_PATH) -> dict[str, str]:
    """저장된 티커 → 종목명 매핑을 읽는다.

    Args:
        path: `names.csv` 경로

    Returns:
        티커 → 종목명 (파일이 없으면 빈 dict)

    Raises:
        ValueError: 컬럼 구성이 규칙과 다른 경우
    """
    if not path.exists():
        return {}

    # dtype=str: 티커 선행 0 보존 (int 변환 금지)
    frame = pd.read_csv(path, dtype=str)

    missing = [column for column in NAMES_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"names.csv에 필요한 컬럼이 없습니다: {missing}")

    return dict(zip(frame[COL_TICKER], frame[COL_NAME], strict=True))


def merge_names(names: Mapping[str, str], path: Path = NAMES_CSV_PATH) -> Path:
    """티커 → 종목명 매핑을 기존 파일에 병합해 저장한다.

    같은 티커가 이미 있으면 새 이름으로 갱신한다 (사명 변경 반영).

    Args:
        names: 병합할 티커 → 종목명
        path: `names.csv` 경로

    Returns:
        저장된 파일 경로

    Raises:
        ValueError: 병합할 매핑이 비어 있는 경우
    """
    if not names:
        raise ValueError("병합할 종목명이 없습니다")

    merged = load_names(path)
    merged.update(names)

    frame = pd.DataFrame(
        {
            COL_TICKER: sorted(merged),
            COL_NAME: [merged[ticker] for ticker in sorted(merged)],
        }
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")

    return path
