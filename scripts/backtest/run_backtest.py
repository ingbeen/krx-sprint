#!/usr/bin/env python3
"""주도테마 대장주 눌림목 백테스트 실행

통합 패널 위에서 전략을 실행하고 트레이드 원장·자산 곡선·성과 요약을 저장한다.
KRX에 요청하지 않고 저장된 parquet만 읽는다.

실행 방법은 docs/COMMANDS.md 참고.
"""

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from krx_sprint.backtest.engine import run_backtest
from krx_sprint.backtest.metrics import equity_frame, summarize, summary_payload, trades_frame
from krx_sprint.backtest.params import EntryPriceKind, ExecutionParams, StopLossKind, StrategyParams
from krx_sprint.common_constants import BACKTEST_DIR, KST
from krx_sprint.panel.loader import load_panel, load_trading_days
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "backtest_run"

# 지표 계산에 필요한 과거 구간 여유 (달력일).
# 상관 창·이동평균이 매매 시작일 이전 데이터를 요구하므로 미리 넉넉히 읽는다
WARMUP_DAYS = 400

# 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("지표", 24, Align.LEFT),
    ("값", 18, Align.RIGHT),
]

DIAGNOSTIC_TABLE_COLUMNS = [
    ("진단 항목", 24, Align.LEFT),
    ("건수", 14, Align.RIGHT),
]

# 진입·손절 방식 비교 실행용 이름 (설계 v2 — 같은 종목 선정 위에서 한 축만 바꿔 대조한다).
# 진입 방식마다 알맞은 손절선이 다를 수 있어 두 축을 따로 고를 수 있게 둔다
ENTRY_CHOICES = {
    "band": EntryPriceKind.MA_BAND_SPLIT,
    "reclaim": EntryPriceKind.MA_RECLAIM,
    "close-discount": EntryPriceKind.CLOSE_DISCOUNT,
}

STOP_CHOICES = {
    "band-floor": StopLossKind.BAND_FLOOR,
    "fixed": StopLossKind.FIXED,
    "moving-average": StopLossKind.MOVING_AVERAGE,
    "swing-low": StopLossKind.SWING_LOW,
}


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

    구간 인자를 두는 이유는 in-sample/out-of-sample 분할 실행과 민감도 스윕 때문이다
    (scripts/CLAUDE.md 인자 최소화 원칙의 예외).

    Returns:
        해석된 인자
    """
    parser = argparse.ArgumentParser(description="주도테마 대장주 눌림목 백테스트")
    parser.add_argument("--start", type=_parse_date, default=None, help="매매 시작 일자 (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, default=None, help="매매 종료 일자 (YYYY-MM-DD)")
    parser.add_argument("--label", default=None, help="결과 폴더에 붙일 이름")
    parser.add_argument("--no-cost", action="store_true", help="비용 0으로 실행 (새니티 체크용)")
    parser.add_argument(
        "--entry",
        choices=sorted(ENTRY_CHOICES),
        default=None,
        help="진입 방식 (생략하면 params.py 기본값). band=이탈 밴드 분할, reclaim=회복 확인",
    )
    parser.add_argument(
        "--stop",
        choices=sorted(STOP_CHOICES),
        default=None,
        help="손절 방식 (생략하면 params.py 기본값). band-floor=밴드 하한선, fixed=평균단가 대비 고정 비율",
    )

    return parser.parse_args()


def _output_dir(label: str | None) -> Path:
    """결과 저장 폴더를 만든다.

    Args:
        label: 폴더에 붙일 이름

    Returns:
        생성된 폴더 경로
    """
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{label}" if label else stamp
    path = BACKTEST_DIR / name
    path.mkdir(parents=True, exist_ok=True)

    return path


@cli_exception_handler
def main() -> int:
    """백테스트를 실행하고 결과를 저장한다.

    Returns:
        종료 코드 (0=성공)
    """
    args = _parse_args()

    params = StrategyParams()
    if args.entry is not None:
        params = replace(params, entry_price_kind=ENTRY_CHOICES[args.entry])
    if args.stop is not None:
        params = replace(params, stop_loss_kind=STOP_CHOICES[args.stop])

    execution_params = (
        ExecutionParams(fee_rate=0.0, include_tax=False, stop_loss_slippage_ticks=0)
        if args.no_cost
        else ExecutionParams()
    )

    # 1. 패널 적재 — 지표 계산에 필요한 과거 구간까지 함께 읽는다
    load_start = None if args.start is None else args.start - timedelta(days=WARMUP_DAYS)
    panel = load_panel(start=load_start, end=args.end)
    trading_days = load_trading_days()
    logger.debug("패널 %s행 / 거래일 %d일 적재", f"{len(panel):,}", len(trading_days))

    # 2. 실행
    result = run_backtest(panel, trading_days, params, execution_params, start=args.start, end=args.end)
    performance = summarize(result, params.initial_equity)

    # 3. 결과 저장
    output = _output_dir(args.label)
    trades_frame(result).to_csv(output / "trades.csv", index=False, encoding="utf-8")
    equity_frame(result).to_csv(output / "equity.csv", index=False, encoding="utf-8")
    payload = summary_payload(performance, result, asdict(params), asdict(execution_params))
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # 4. 요약 출력 — 비용 전후를 나란히 낸다
    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(
        [
            ["구간", f"{result.equity[0][0]} ~ {result.equity[-1][0]}"],
            ["초기 자본", f"{performance.initial_equity:,}"],
            ["종료 자본", f"{performance.final_equity:,}"],
            ["누적 수익률", f"{performance.total_return_pct:.2f}%"],
            ["CAGR", f"{performance.cagr_pct:.2f}%"],
            ["MDD", f"{performance.max_drawdown_pct:.2f}%"],
            ["트레이드 수", f"{performance.trade_count:,}"],
            ["승률", f"{performance.win_rate_pct:.2f}%"],
            ["실현 손익비", f"{performance.payoff_ratio:.2f}"],
            ["트레이드당 기대값", f"{performance.expectancy:,}"],
            ["평균 보유일", f"{performance.avg_holding_days:.2f}"],
            ["최대 연속 손실", f"{performance.max_consecutive_losses:,}"],
            ["노출도", f"{performance.exposure_pct:.2f}%"],
            ["비용 전 손익", f"{performance.gross_pnl:,}"],
            ["비용 후 손익", f"{performance.net_pnl:,}"],
            ["수수료 합계", f"{performance.total_fee:,}"],
            ["증권거래세 합계", f"{performance.total_tax:,}"],
            ["비용/총손익", f"{performance.cost_to_gross_ratio:.4f}"],
        ],
        title=f"백테스트 결과 ({output.name})",
    )

    diagnostics = TableLogger(DIAGNOSTIC_TABLE_COLUMNS, logger)
    diagnostics.print_table(
        [[name, f"{count:,}"] for name, count in result.diagnostics.as_dict().items()],
        title="진단 (가정 사용량)",
    )

    logger.debug("결과 저장: %s", output)

    # 5. 실행 이력 저장
    save_metadata(
        KEY_META_TYPE,
        {
            "start": str(args.start) if args.start else "",
            "end": str(args.end) if args.end else "",
            "label": args.label or "",
            "trade_count": performance.trade_count,
            "total_return_pct": performance.total_return_pct,
            "max_drawdown_pct": performance.max_drawdown_pct,
            "net_pnl": performance.net_pnl,
            "output": str(output),
        },
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
