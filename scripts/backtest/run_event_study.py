#!/usr/bin/env python3
"""신호 예측력 이벤트 스터디 실행

매매 규칙 없이 신호가 미래 수익률을 예측하는지만 측정한다.
진입가·손절·익절·자금배분·비용이 들어가지 않으므로 성과가 나빠도 원인이 신호 하나로 좁혀진다.

KRX에 요청하지 않고 저장된 parquet만 읽는다. 실행 방법은 docs/COMMANDS.md 참고.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from krx_sprint.backtest.event_study import (
    DEFAULT_HORIZONS,
    SignalLayer,
    add_excess_returns,
    compute_forward_returns,
    extract_signal_layers,
    summarize_layers,
)
from krx_sprint.backtest.params import StrategyParams
from krx_sprint.common_constants import BACKTEST_DIR, COL_DATE, KST
from krx_sprint.panel.loader import load_panel
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "event_study"

# 지표 계산에 필요한 과거 구간 여유 (달력일). 상관 창·이동평균이 시작일 이전 데이터를 요구한다
WARMUP_DAYS = 400

SUMMARY_TABLE_COLUMNS = [
    ("계층", 16, Align.LEFT),
    ("구간", 6, Align.RIGHT),
    ("일자수", 8, Align.RIGHT),
    ("종목수", 9, Align.RIGHT),
    ("초과수익%", 11, Align.RIGHT),
    ("표준오차", 10, Align.RIGHT),
    ("t값", 8, Align.RIGHT),
]


def _parse_date(value: str) -> date:
    """YYYY-MM-DD 문자열을 일자로 바꾼다.

    Args:
        value: 일자 문자열

    Returns:
        일자

    Raises:
        argparse.ArgumentTypeError: 형식이 맞지 않는 경우
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"일자는 YYYY-MM-DD 형식이어야 합니다: {value}") from error


def _parse_args() -> argparse.Namespace:
    """명령행 인자를 해석한다.

    구간 인자는 시장 국면별 분해를 위해 둔다 (scripts/CLAUDE.md 인자 최소화 원칙의 예외).

    Returns:
        해석된 인자
    """
    parser = argparse.ArgumentParser(description="신호 예측력 이벤트 스터디")
    parser.add_argument("--start", type=_parse_date, default=None, help="측정 시작 일자 (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, default=None, help="측정 종료 일자 (YYYY-MM-DD)")
    parser.add_argument("--label", default=None, help="결과 폴더에 붙일 이름")

    return parser.parse_args()


def _output_dir(label: str | None) -> Path:
    """결과 저장 폴더를 만든다.

    Args:
        label: 폴더에 붙일 이름

    Returns:
        생성된 폴더 경로
    """
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{label}" if label else f"{stamp}_event-study"
    path = BACKTEST_DIR / name
    path.mkdir(parents=True, exist_ok=True)

    return path


@cli_exception_handler
def main() -> int:
    """이벤트 스터디를 실행하고 결과를 저장한다.

    Returns:
        종료 코드 (0=성공)
    """
    args = _parse_args()
    params = StrategyParams()

    # 1. 패널 적재 — 지표 계산에 필요한 과거 구간까지 함께 읽는다
    load_start = None if args.start is None else args.start - timedelta(days=WARMUP_DAYS)
    panel = load_panel(start=load_start, end=args.end)
    logger.debug("패널 %s행 적재", f"{len(panel):,}")

    # 2. forward return → 신호 계층 → 초과수익 순으로 쌓는다.
    #    기준선은 유니버스 게이트 통과 종목이다 — 신호 종목이 그 부분집합이라야 비교가 공정하다
    frame = compute_forward_returns(panel, DEFAULT_HORIZONS)
    layers = extract_signal_layers(panel, params)
    for layer in SignalLayer:
        frame[layer.name.lower()] = layers[layer.name.lower()].to_numpy()

    frame = add_excess_returns(frame, DEFAULT_HORIZONS, baseline_mask=frame[SignalLayer.UNIVERSE.name.lower()])

    # 3. 워밍업 구간은 측정에서 뺀다 (기준선은 이미 일자별로 계산돼 영향을 받지 않는다)
    if args.start is not None:
        frame = frame[frame[COL_DATE].dt.date >= args.start]

    summary = summarize_layers(frame, horizons=DEFAULT_HORIZONS)

    # 4. 저장
    output = _output_dir(args.label)
    summary.to_csv(output / "layer_summary.csv", index=False, encoding="utf-8")
    payload = {
        "period": {
            "start": None if args.start is None else args.start.isoformat(),
            "end": None if args.end is None else args.end.isoformat(),
        },
        "horizons": list(DEFAULT_HORIZONS),
        "test_count": len(summary),
        "strategy_params": {"min_cluster_size": params.min_cluster_size, "co_move_rate": params.co_move_rate},
        "layer_summary": summary.to_dict(orient="records"),
    }
    (output / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(summary)
    logger.debug("결과 저장: %s", output)

    save_metadata(
        KEY_META_TYPE,
        {
            "start": None if args.start is None else args.start.isoformat(),
            "end": None if args.end is None else args.end.isoformat(),
            "output": output.name,
            "test_count": len(summary),
        },
    )

    return 0


def _print_summary(summary: pd.DataFrame) -> None:
    """신호일 종가 기준 결과를 표로 출력한다.

    Args:
        summary: 계층 집계표
    """
    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_header("신호 계층별 초과수익 (신호일 종가 기준, 일자 단위 검정)")

    for row in summary[summary["basis"] == "종가"].itertuples():
        table.print_row(
            [
                row.layer,
                f"{row.horizon}일",
                f"{row.day_count:,}",
                f"{row.ticker_count:,}",
                f"{row.day_mean_pct:+.3f}",
                f"{row.day_stderr_pct:.3f}",
                f"{row.day_t_stat:+.2f}",
            ]
        )

    table.print_footer()


if __name__ == "__main__":
    sys.exit(main())
