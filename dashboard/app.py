from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

# Allow `py -m streamlit run dashboard/app.py`
# from the project root.
PROJECT_ROOT = Path(
    __file__
).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

from dashboard.charts import (
    allocation,
    drawdown_chart,
    equity_curve,
    monthly_returns,
)
from dashboard.data import (
    DashboardPaths,
    compute_overview,
    compute_performance,
    load_closed_trades,
    load_fills,
    load_latest_signals,
    load_market_health,
    load_positions,
    load_snapshots,
    load_vnindex,
)


st.set_page_config(
    page_title="Quant Stock Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_css() -> None:
    css_path = Path(
        __file__
    ).with_name(
        "styles.css"
    )

    st.markdown(
        (
            "<style>"
            + css_path.read_text(
                encoding="utf-8"
            )
            + "</style>"
        ),
        unsafe_allow_html=True,
    )


def money(
    value: float,
) -> str:
    return f"{value:,.0f} đ"


def signed_money(
    value: float,
) -> str:
    return f"{value:+,.0f} đ"


def signed_pct(
    value: float,
) -> str:
    return f"{value:+.2f}%"


def metric_card(
    label: str,
    value: str,
    delta: str,
    icon: str,
    tone: str = "neutral",
) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">
            <span class="metric-icon">{icon}</span>
            {label}
          </div>
          <div class="metric-value">{value}</div>
          <div class="metric-delta {tone}">
            {delta}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def panel_title(
    title: str,
    subtitle: str = "",
) -> None:
    st.markdown(
        (
            f'<div class="panel-title">{title}</div>'
            f'<div class="panel-subtitle">{subtitle}</div>'
        ),
        unsafe_allow_html=True,
    )


def sidebar() -> str:
    st.sidebar.markdown(
        """
        <div class="brand">
          <div class="brand-logo">Q</div>
          <div>
            <div class="brand-title">QUANT STOCK</div>
            <div class="brand-subtitle">DASHBOARD</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    page = st.sidebar.radio(
        "Điều hướng",
        [
            "Dashboard",
            "Portfolio",
            "Trades",
            "Analytics",
            "Reports",
            "Data Health",
            "Settings",
        ],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")

    st.sidebar.markdown(
        """
        <div class="panel" style="padding:14px;">
          <div style="
              color:#94a3b8;
              font-size:10px;
              letter-spacing:.08em;
          ">
            PAPER TRADING
          </div>
          <div style="
              font-weight:700;
              margin-top:8px;
          ">
            Do Thanh Bo
          </div>
          <div
              style="margin-top:14px;"
              class="status-pill"
          >
            <span class="status-dot"></span>
            System Online
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return page


def header(
    title: str,
    subtitle: str,
) -> None:
    left, right = st.columns(
        [4, 2]
    )

    with left:
        st.markdown(
            (
                f'<h1 class="page-title">{title}</h1>'
                f'<div class="page-subtitle">{subtitle}</div>'
            ),
            unsafe_allow_html=True,
        )

    with right:
        date_column, refresh_column = st.columns(
            [1, 1]
        )

        date_column.markdown(
            f"**{datetime.now():%d/%m/%Y}**"
        )

        if refresh_column.button(
            "↻ Refresh",
            width="stretch",
            key=f"refresh_{title.lower()}",
        ):
            st.cache_data.clear()
            st.rerun()


@st.cache_data(
    ttl=30
)
def cached_data(
    market_db: str,
    paper_db: str,
) -> dict:
    paths = DashboardPaths(
        Path(market_db),
        Path(paper_db),
    )

    return {
        "paths": paths,
        "overview": compute_overview(
            paths
        ),
        "performance": compute_performance(
            paths
        ),
        "snapshots": load_snapshots(
            paths
        ),
        "positions": load_positions(
            paths
        ),
        "fills": load_fills(
            paths
        ),
        "closed": load_closed_trades(
            paths
        ),
        "health": load_market_health(
            paths
        ),
        "signals": load_latest_signals(
            paths
        ),
        "vnindex": load_vnindex(
            paths
        ),
    }


def dashboard_page(
    data: dict,
) -> None:
    overview = data["overview"]
    performance = data["performance"]
    snapshots = data["snapshots"]
    positions = data["positions"]
    fills = data["fills"]
    vnindex = data["vnindex"]

    header(
        "Dashboard",
        "Tổng quan hiệu suất hệ thống",
    )

    columns = st.columns(
        6
    )

    cards = [
        (
            "Total Equity",
            money(
                overview.equity
            ),
            (
                signed_pct(
                    overview.total_return_pct
                )
                + " all time"
            ),
            "↗",
            (
                "positive"
                if overview.total_return_value >= 0
                else "negative"
            ),
        ),
        (
            "Cash Balance",
            money(
                overview.cash
            ),
            (
                f"{100 - overview.exposure_pct:.1f}% "
                "danh mục"
            ),
            "▣",
            "neutral",
        ),
        (
            "Total Return",
            signed_pct(
                overview.total_return_pct
            ),
            signed_money(
                overview.total_return_value
            ),
            "⌁",
            (
                "positive"
                if overview.total_return_value >= 0
                else "negative"
            ),
        ),
        (
            "Open Positions",
            str(
                overview.open_positions
            ),
            (
                f"{overview.exposure_pct:.1f}% "
                "exposure"
            ),
            "▢",
            "neutral",
        ),
        (
            "Win Rate",
            (
                f"{overview.win_rate_pct:.1f}%"
            ),
            (
                f"{int(performance['total_trades'])} "
                "closed trades"
            ),
            "◎",
            "positive",
        ),
        (
            "Max Drawdown",
            (
                f"{overview.max_drawdown_pct:.2f}%"
            ),
            "all time",
            "↘",
            (
                "negative"
                if overview.max_drawdown_pct < 0
                else "neutral"
            ),
        ),
    ]

    for column, card in zip(
        columns,
        cards,
    ):
        with column:
            metric_card(
                *card
            )

    st.write("")

    left, middle, right = st.columns(
        [2.2, 1.2, 1.25]
    )

    benchmark = pd.DataFrame()

    if (
        not vnindex.empty
        and not snapshots.empty
    ):
        benchmark = vnindex.copy()

        first_close = (
            benchmark["close"]
            .iloc[0]
        )

        benchmark["normalized"] = (
            benchmark["close"]
            / first_close
            * overview.initial_cash
            if first_close
            else overview.initial_cash
        )

    with left:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Equity Curve",
            (
                "Portfolio equity and "
                "VNINDEX benchmark"
            ),
        )

        st.plotly_chart(
            equity_curve(
                snapshots,
                benchmark,
            ),
            width="stretch",
            config={
                "displayModeBar": False,
            },
            key="dashboard_equity_curve",
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with middle:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Asset Allocation"
        )

        positions_value = (
            float(
                positions[
                    "market_value"
                ].sum()
            )
            if not positions.empty
            else 0.0
        )

        st.plotly_chart(
            allocation(
                overview.cash,
                positions_value,
            ),
            width="stretch",
            config={
                "displayModeBar": False,
            },
            key="dashboard_asset_allocation",
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "PnL Summary"
        )

        values = [
            (
                "Realized",
                overview.realized_pnl,
            ),
            (
                "Unrealized",
                overview.unrealized_pnl,
            ),
            (
                "Total",
                (
                    overview.realized_pnl
                    + overview.unrealized_pnl
                ),
            ),
            (
                "Return",
                overview.total_return_value,
            ),
        ]

        for label, value in values:
            tone = (
                "positive"
                if value >= 0
                else "negative"
            )

            st.markdown(
                (
                    '<div class="summary-row">'
                    f"<span>{label}</span>"
                    f'<strong class="{tone}">'
                    f"{signed_money(value)}"
                    "</strong>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    left, right = st.columns(
        [1.65, 1.35]
    )

    with left:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Open Positions",
            (
                "Current paper-trading "
                "portfolio"
            ),
        )

        if positions.empty:
            st.info(
                "Chưa có vị thế đang mở."
            )
        else:
            table = positions[
                [
                    "symbol",
                    "quantity",
                    "average_price",
                    "market_price",
                    "unrealized_pnl_pct",
                    "unrealized_pnl",
                    "stop_price",
                    "take_profit_price",
                    "days_held",
                ]
            ].copy()

            table.columns = [
                "Symbol",
                "Qty",
                "Avg Price",
                "Last Price",
                "PnL (%)",
                "PnL (đ)",
                "Stop Loss",
                "Target",
                "Days Held",
            ]

            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                height=235,
                column_config={
                    "Avg Price": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                    "Last Price": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                    "PnL (%)": (
                        st.column_config
                        .NumberColumn(
                            format="%.2f%%"
                        )
                    ),
                    "PnL (đ)": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                    "Stop Loss": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                    "Target": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                },
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Trade History (Recent)",
            "Latest paper fills",
        )

        if fills.empty:
            st.info(
                "Chưa có giao dịch."
            )
        else:
            table = fills.head(
                8
            ).copy()

            table["Time"] = (
                table["created_at"]
                .dt.tz_convert(
                    "Asia/Ho_Chi_Minh"
                )
                .dt.strftime(
                    "%d/%m/%Y %H:%M"
                )
            )

            table = table[
                [
                    "Time",
                    "symbol",
                    "side",
                    "quantity",
                    "price",
                    "commission",
                ]
            ]

            table.columns = [
                "Time",
                "Symbol",
                "Action",
                "Qty",
                "Price",
                "Fee",
            ]

            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                height=235,
                column_config={
                    "Price": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                    "Fee": (
                        st.column_config
                        .NumberColumn(
                            format="%,.0f"
                        )
                    ),
                },
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    first, second, third = st.columns(
        [1.15, 1.25, 1.35]
    )

    with first:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Performance Overview"
        )

        performance_rows = [
            (
                "Total Trades",
                f"{int(performance['total_trades'])}",
            ),
            (
                "Win Rate",
                f"{performance['win_rate']:.1f}%",
            ),
            (
                "Profit Factor",
                (
                    f"{performance['profit_factor']:.2f}"
                    if performance[
                        "profit_factor"
                    ] < 900
                    else "∞"
                ),
            ),
            (
                "Expectancy",
                (
                    f"{performance['expectancy']:.2f}%"
                ),
            ),
            (
                "Avg Win",
                (
                    f"{performance['avg_win']:+.2f}%"
                ),
            ),
            (
                "Avg Loss",
                (
                    f"{performance['avg_loss']:+.2f}%"
                ),
            ),
        ]

        for label, value in performance_rows:
            st.markdown(
                (
                    '<div class="summary-row">'
                    f"<span>{label}</span>"
                    f"<strong>{value}</strong>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with second:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Equity vs. Benchmark"
        )

        st.plotly_chart(
            equity_curve(
                snapshots,
                benchmark,
            ),
            width="stretch",
            config={
                "displayModeBar": False,
            },
            key="dashboard_equity_vs_benchmark",
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    with third:
        st.markdown(
            '<div class="panel">',
            unsafe_allow_html=True,
        )

        panel_title(
            "Data & Pipeline Status"
        )

        health = data["health"]

        latest_date = (
            health[
                "latest_date"
            ].mode().iloc[0]
            if not health.empty
            else "N/A"
        )

        statuses = [
            (
                "Market Data",
                latest_date,
                (
                    "Success"
                    if not health.empty
                    else "Missing"
                ),
            ),
            (
                "Paper Database",
                (
                    f"{len(snapshots)} "
                    "snapshots"
                ),
                "Success",
            ),
            (
                "Open Positions",
                str(
                    overview.open_positions
                ),
                "Success",
            ),
            (
                "Trade Records",
                str(
                    len(fills)
                ),
                "Success",
            ),
            (
                "Dashboard Refresh",
                datetime.now().strftime(
                    "%H:%M:%S"
                ),
                "Success",
            ),
        ]

        for (
            label,
            stamp,
            status,
        ) in statuses:
            st.markdown(
                (
                    '<div class="summary-row">'
                    f"<span>{label}<br>"
                    '<small style="color:#7f8da3">'
                    f"{stamp}"
                    "</small></span>"
                    f"<strong>{status}</strong>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )


def portfolio_page(
    data: dict,
) -> None:
    header(
        "Portfolio",
        "Chi tiết vị thế và phân bổ tài sản",
    )

    positions = data["positions"]
    overview = data["overview"]

    first, second, third = st.columns(
        3
    )

    with first:
        market_value = (
            float(
                positions[
                    "market_value"
                ].sum()
            )
            if not positions.empty
            else 0.0
        )

        metric_card(
            "Market Value",
            money(
                market_value
            ),
            (
                f"{overview.exposure_pct:.2f}% "
                "exposure"
            ),
            "◫",
        )

    with second:
        metric_card(
            "Unrealized PnL",
            signed_money(
                overview.unrealized_pnl
            ),
            signed_pct(
                overview.unrealized_pnl
                / max(
                    overview.equity,
                    1,
                )
                * 100
            ),
            "↗",
            (
                "positive"
                if overview.unrealized_pnl >= 0
                else "negative"
            ),
        )

    with third:
        metric_card(
            "Cash",
            money(
                overview.cash
            ),
            (
                f"{100 - overview.exposure_pct:.2f}% "
                "portfolio"
            ),
            "▣",
        )

    st.write("")

    if positions.empty:
        st.info(
            "Chưa có vị thế đang mở."
        )
    else:
        st.dataframe(
            positions,
            hide_index=True,
            width="stretch",
            height=430,
        )


def trades_page(
    data: dict,
) -> None:
    header(
        "Trades",
        (
            "Lịch sử khớp lệnh và "
            "giao dịch đã đóng"
        ),
    )

    fills = data["fills"]
    closed = data["closed"]

    fills_tab, closed_tab = st.tabs(
        [
            "All Fills",
            "Closed Trades",
        ]
    )

    with fills_tab:
        st.dataframe(
            fills,
            hide_index=True,
            width="stretch",
            height=500,
        )

    with closed_tab:
        st.dataframe(
            closed,
            hide_index=True,
            width="stretch",
            height=500,
        )


def analytics_page(
    data: dict,
) -> None:
    header(
        "Analytics",
        (
            "Performance, drawdown và "
            "hiệu suất theo thời gian"
        ),
    )

    snapshots = data["snapshots"]
    performance = data["performance"]

    columns = st.columns(
        4
    )

    analytics_cards = [
        (
            "Total Trades",
            str(
                int(
                    performance[
                        "total_trades"
                    ]
                )
            ),
            "#",
        ),
        (
            "Win Rate",
            (
                f"{performance['win_rate']:.1f}%"
            ),
            "◎",
        ),
        (
            "Profit Factor",
            (
                f"{performance['profit_factor']:.2f}"
            ),
            "↗",
        ),
        (
            "Expectancy",
            (
                f"{performance['expectancy']:+.2f}%"
            ),
            "⌁",
        ),
    ]

    for (
        column,
        (
            label,
            value,
            icon,
        ),
    ) in zip(
        columns,
        analytics_cards,
    ):
        with column:
            metric_card(
                label,
                value,
                "closed trades",
                icon,
            )

    st.write("")

    left, right = st.columns(
        2
    )

    with left:
        panel_title(
            "Drawdown"
        )

        st.plotly_chart(
            drawdown_chart(
                snapshots
            ),
            width="stretch",
            config={
                "displayModeBar": False,
            },
            key="analytics_drawdown_chart",
        )

    with right:
        panel_title(
            "Monthly Returns"
        )

        st.plotly_chart(
            monthly_returns(
                snapshots
            ),
            width="stretch",
            config={
                "displayModeBar": False,
            },
            key="analytics_monthly_returns",
        )


def reports_page(
    data: dict,
) -> None:
    header(
        "Reports",
        "Xuất dữ liệu danh mục và giao dịch",
    )

    datasets = {
        "positions.csv": data["positions"],
        "fills.csv": data["fills"],
        "closed_trades.csv": data["closed"],
        "portfolio_snapshots.csv": (
            data["snapshots"]
        ),
        "market_health.csv": data["health"],
    }

    for filename, frame in datasets.items():
        st.download_button(
            label=(
                f"Download {filename}"
            ),
            data=(
                frame.to_csv(
                    index=False
                ).encode(
                    "utf-8-sig"
                )
            ),
            file_name=filename,
            mime="text/csv",
            width="stretch",
            key=f"download_{filename}",
        )


def data_health_page(
    data: dict,
) -> None:
    header(
        "Data Health",
        (
            "Độ đầy đủ và độ mới "
            "của market data"
        ),
    )

    health = data["health"]

    if health.empty:
        st.error(
            "Không đọc được bảng prices."
        )
        return

    reference = (
        health[
            "latest_date"
        ].mode().iloc[0]
    )

    health = health.copy()

    health["status"] = (
        health[
            "latest_date"
        ].apply(
            lambda value: (
                "Healthy"
                if value == reference
                else "Stale"
            )
        )
    )

    first, second, third = st.columns(
        3
    )

    with first:
        metric_card(
            "Symbols",
            str(
                len(health)
            ),
            "in database",
            "#",
        )

    with second:
        metric_card(
            "Latest Date",
            str(
                reference
            ),
            "market reference",
            "◷",
        )

    with third:
        stale_count = int(
            (
                health["status"]
                == "Stale"
            ).sum()
        )

        metric_card(
            "Stale Symbols",
            str(
                stale_count
            ),
            "need review",
            "!",
            "negative",
        )

    st.write("")

    st.dataframe(
        health,
        hide_index=True,
        width="stretch",
        height=520,
    )


def settings_page(
    paths: DashboardPaths,
) -> None:
    header(
        "Settings",
        (
            "Cấu hình đường dẫn dữ liệu "
            "và giao diện"
        ),
    )

    st.code(
        (
            f"MARKET_DATABASE_PATH={paths.market_db}\n"
            f"PAPER_DATABASE_PATH={paths.paper_db}"
        ),
        language="text",
    )

    st.info(
        (
            "Dashboard đang ở chế độ chỉ đọc. "
            "Mọi lệnh giao dịch vẫn do trading "
            "engine xử lý."
        )
    )


def main() -> None:
    load_css()

    page = sidebar()

    market_db = st.sidebar.text_input(
        "Market DB",
        "data/market.db",
    )

    paper_db = st.sidebar.text_input(
        "Paper DB",
        "data/paper_trading.db",
    )

    try:
        data = cached_data(
            market_db,
            paper_db,
        )
    except Exception as error:
        st.error(
            (
                "Không thể tải dữ liệu dashboard: "
                f"{type(error).__name__}: {error}"
            )
        )
        st.stop()

    if page == "Dashboard":
        dashboard_page(
            data
        )
    elif page == "Portfolio":
        portfolio_page(
            data
        )
    elif page == "Trades":
        trades_page(
            data
        )
    elif page == "Analytics":
        analytics_page(
            data
        )
    elif page == "Reports":
        reports_page(
            data
        )
    elif page == "Data Health":
        data_health_page(
            data
        )
    else:
        settings_page(
            data["paths"]
        )


if __name__ == "__main__":
    main()
