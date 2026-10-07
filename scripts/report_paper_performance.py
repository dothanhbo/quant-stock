from __future__ import annotations

import argparse
import math
from pathlib import Path

from dotenv import load_dotenv

from config.paper_store import resolve_active_paper_store

from analysis.paper_performance import (
    calculate_paper_performance,
    load_closed_trades_frame,
    load_daily_equity_curve,
)


def money(
    value: float,
) -> str:
    return f"{value:+,.0f} đ"


def pct(
    value: float,
) -> str:
    return f"{value:+.2f}%"


def ratio(
    value: float,
) -> str:
    if math.isinf(
        value
    ):
        return "∞"

    return f"{value:.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Tạo báo cáo hiệu suất "
            "paper trading từ SQLite."
        )
    )

    parser.add_argument(
        "--database",
        default=None,
        help=(
            "Đường dẫn paper database; mặc định là active store."
        ),
    )

    parser.add_argument(
        "--risk-free-rate",
        type=float,
        default=0.0,
        help=(
            "Lãi suất phi rủi ro theo năm, "
            "đơn vị phần trăm."
        ),
    )

    parser.add_argument(
        "--export-dir",
        default=None,
        help=(
            "Thư mục xuất JSON, trade history "
            "và equity curve."
        ),
    )

    parser.add_argument(
        "--qualified-only",
        action="store_true",
        help=(
            "R3: chỉ tính các giao dịch có provenance dữ liệu thị trường "
            "đã được xác minh (loại trade bị quarantine, legacy, chưa bind). "
            "Chỉ số equity cấp tài khoản không được lọc."
        ),
    )
    parser.add_argument(
        "--label-provenance",
        action="store_true",
        help=(
            "R3: thêm cột provenance_qualification vào trade history "
            "(mô tả; không lọc)."
        ),
    )

    return parser


def print_report(
    report,
) -> None:
    print(
        "\n"
        + "=" * 68
    )
    print(
        "📈 PAPER TRADING PERFORMANCE"
    )
    print(
        "=" * 68
    )

    print(
        f"Provenance scope  : "
        f"{getattr(report, 'provenance_scope', 'UNQUALIFIED_DESCRIPTIVE')}"
    )
    print(
        f"Vốn ban đầu       : "
        f"{report.initial_equity:,.0f} đ"
    )
    print(
        f"Tổng tài sản      : "
        f"{report.current_equity:,.0f} đ"
    )
    print(
        f"Lợi nhuận tổng    : "
        f"{money(report.total_return_value)} "
        f"({pct(report.total_return_pct)})"
    )

    print(
        "\n"
        + "-" * 68
    )
    print(
        "GIAO DỊCH"
    )
    print(
        "-" * 68
    )
    print(
        f"Tổng số lệnh      : "
        f"{report.total_trades}"
    )
    print(
        f"Thắng / Thua / Hòa: "
        f"{report.winning_trades} / "
        f"{report.losing_trades} / "
        f"{report.breakeven_trades}"
    )
    print(
        f"Win Rate          : "
        f"{report.win_rate_pct:.2f}%"
    )
    print(
        f"Net Realized PnL  : "
        f"{money(report.net_realized_pnl)}"
    )
    print(
        f"Profit Factor     : "
        f"{ratio(report.profit_factor)}"
    )
    print(
        f"Expectancy        : "
        f"{money(report.expectancy_amount)} "
        f"({pct(report.expectancy_pct)})"
    )
    print(
        f"Average Win       : "
        f"{money(report.average_win_amount)} "
        f"({pct(report.average_win_pct)})"
    )
    print(
        f"Average Loss      : "
        f"{money(report.average_loss_amount)} "
        f"({pct(report.average_loss_pct)})"
    )
    print(
        f"Payoff Ratio      : "
        f"{report.payoff_ratio:.2f}"
    )
    print(
        f"Largest Win       : "
        f"{money(report.largest_win_amount)}"
    )
    print(
        f"Largest Loss      : "
        f"{money(report.largest_loss_amount)}"
    )
    print(
        f"Thời gian giữ TB  : "
        f"{report.average_holding_days:.1f} ngày"
    )

    print(
        "\n"
        + "-" * 68
    )
    print(
        "RỦI RO DANH MỤC"
    )
    print(
        "-" * 68
    )
    print(
        f"Max Drawdown      : "
        f"{report.max_drawdown_pct:.2f}%"
    )
    print(
        f"CAGR              : "
        f"{report.cagr_pct:.2f}%"
    )
    print(
        f"Volatility năm hóa: "
        f"{report.annualized_volatility_pct:.2f}%"
    )
    print(
        f"Sharpe Ratio      : "
        f"{report.sharpe_ratio:.2f}"
    )
    print(
        f"Sortino Ratio     : "
        f"{report.sortino_ratio:.2f}"
    )
    print(
        f"Calmar Ratio      : "
        f"{report.calmar_ratio:.2f}"
    )
    print(
        f"Số ngày snapshot  : "
        f"{report.snapshot_days}"
    )

    if report.total_trades < 20:
        print(
            "\n⚠️ Chưa đủ 20 giao dịch đóng; "
            "các chỉ số trade còn ít ý nghĩa."
        )

    if report.snapshot_days < 20:
        print(
            "⚠️ Chưa đủ 20 ngày snapshot; "
            "Sharpe, CAGR và drawdown chưa ổn định."
        )


def export_report(
    *,
    database_path: Path,
    export_dir: Path,
    report,
    qualifier=None,
    qualification_required: bool = False,
) -> None:
    export_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        export_dir
        / "paper_performance.json"
    ).write_text(
        report.to_json(),
        encoding="utf-8",
    )

    trades = load_closed_trades_frame(
        database_path,
        qualifier=qualifier,
        qualification_required=qualification_required,
    )
    trades.to_csv(
        export_dir
        / "paper_closed_trades.csv",
        index=False,
        encoding="utf-8-sig",
    )

    equity = load_daily_equity_curve(
        database_path,
        initial_equity=(
            report.initial_equity
        ),
    )
    equity.to_csv(
        export_dir
        / "paper_equity_curve.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print(
        "\n✅ Đã xuất báo cáo vào: "
        f"{export_dir}"
    )


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    database_path = (
        Path(args.database)
        if args.database is not None
        else resolve_active_paper_store().database_path
    )

    qualifier = None
    if args.qualified_only or args.label_provenance:
        from core.evidence_market_binding import EvidenceQualifier

        qualifier = EvidenceQualifier()

    report = calculate_paper_performance(
        database_path,
        risk_free_rate_pct=(
            args.risk_free_rate
        ),
        qualifier=qualifier,
        qualification_required=args.qualified_only,
    )

    print_report(
        report
    )

    if args.export_dir:
        export_report(
            database_path=database_path,
            export_dir=Path(
                args.export_dir
            ),
            report=report,
            qualifier=qualifier,
            qualification_required=args.qualified_only,
        )


if __name__ == "__main__":
    main()
