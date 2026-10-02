from __future__ import annotations

from html import escape


MANAGER_CSS = """
<style>
:root {
  --qm-ink: #172338;
  --qm-muted: #64748b;
  --qm-line: #d9e0e7;
  --qm-surface: #ffffff;
  --qm-soft: #f5f7fa;
  --qm-navy: #142338;
  --qm-blue: #2d5f8b;
  --qm-green: #287a72;
  --qm-amber: #8a6515;
  --qm-red: #9a3636;
}

[data-testid="stAppViewContainer"] { background: #f5f7fa; color: var(--qm-ink); }
[data-testid="stHeader"] { background: rgba(245, 247, 250, .94); height: 2rem; }
[data-testid="stMainBlockContainer"] {
  max-width: 1220px;
  padding: 1rem 2rem 3.5rem;
}
[data-testid="stMainBlockContainer"] > div { gap: .75rem; }

h1 { font-size: 1.66rem !important; line-height: 1.15 !important; letter-spacing: -.03em; margin: 0 0 .08rem !important; }
h2 { font-size: 1.18rem !important; line-height: 1.25 !important; letter-spacing: -.015em; margin: 1.45rem 0 .55rem !important; }
h3 { font-size: .98rem !important; line-height: 1.3 !important; letter-spacing: -.005em; margin: 1rem 0 .4rem !important; }
h4 { font-size: .84rem !important; text-transform: uppercase; letter-spacing: .055em; color: var(--qm-muted); }
p, li, [data-testid="stMarkdownContainer"] { line-height: 1.48; }
[data-testid="stCaptionContainer"] { color: var(--qm-muted); font-size: .78rem; }
code { font-size: .78rem; overflow-wrap: anywhere; }
hr { border-color: var(--qm-line); margin: 1rem 0; }

[data-testid="stSidebar"] { background: var(--qm-navy); border-right: 1px solid #25364e; }
[data-testid="stSidebar"] * { color: #dbe4ed; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #93a4b8; }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 1.35rem; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] { gap: .22rem; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] {
  border-radius: 5px;
  padding: .42rem .55rem;
  border-left: 2px solid transparent;
  transition: background .12s ease;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background: #20334d; }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {
  background: #263b57;
  border-left-color: #87aeca;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display: none; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] p { font-size: .8rem; font-weight: 540; letter-spacing: .015em; }
[data-testid="stSidebar"] button { border-color: #53677e; background: transparent; }
.qm-sidebar-meta { margin: .25rem 0 1.25rem; padding: .7rem .75rem; border: 1px solid #354a64; border-radius: 5px; background: #1b2b42; }
.qm-sidebar-meta__label { color: #8fa2b8; font-size: .65rem; text-transform: uppercase; letter-spacing: .09em; }
.qm-sidebar-meta__value { color: #f2f5f8; font: 500 .76rem/1.35 ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; margin: .08rem 0 .55rem; }
.qm-sidebar-meta__value:last-child { margin-bottom: 0; }
.qm-nav-label { color: #8fa2b8; font-size: .68rem; text-transform: uppercase; letter-spacing: .11em; margin: 0 0 .35rem; }

.qm-console-mode { border-left: 3px solid #567894; background: #eaf0f5; color: #34485b; padding: .4rem .68rem; font-size: .74rem; margin: .25rem 0 .65rem; }

[data-testid="stMetric"] {
  background: var(--qm-surface);
  border: 1px solid var(--qm-line);
  border-radius: 6px;
  padding: .78rem .85rem;
  min-height: 92px;
  box-shadow: 0 1px 1px rgba(17, 31, 46, .025);
}
[data-testid="stMetricLabel"] { color: var(--qm-muted); font-size: .69rem; text-transform: uppercase; letter-spacing: .055em; }
[data-testid="stMetricLabel"] > div, [data-testid="stMetricLabel"] p {
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  line-height: 1.25 !important;
}
[data-testid="stMetricValue"] { font-size: 1.08rem; line-height: 1.25; color: var(--qm-ink); }
[data-testid="stMetricValue"] > div, [data-testid="stMetricValue"] p {
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  overflow-wrap: anywhere;
}
[data-testid="stMetricDelta"] { font-size: .72rem; }

.qm-badge-row { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin: .25rem 0 .65rem; }
.qm-badge { display: inline-flex; align-items: center; gap: .34rem; border: 1px solid; border-radius: 999px; padding: .18rem .48rem; font-size: .68rem; font-weight: 650; letter-spacing: .045em; text-transform: uppercase; white-space: nowrap; }
.qm-badge::before { content: ""; width: .38rem; height: .38rem; border-radius: 50%; background: currentColor; }
.qm-badge--success { color: var(--qm-green); border-color: #a8cbbd; background: #edf6f2; }
.qm-badge--warning { color: var(--qm-amber); border-color: #d9c08d; background: #faf5e9; }
.qm-badge--danger { color: var(--qm-red); border-color: #d9adad; background: #faeeee; }
.qm-badge--neutral { color: #506071; border-color: #c7d0d9; background: #f3f5f7; }
.qm-badge__key { color: #6d7885; font-weight: 500; }

.qm-section-kicker { color: var(--qm-muted); font-size: .68rem; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; margin-bottom: .25rem; }
.qm-decision-panel { border: 1px solid #a9c9c4; border-left: 4px solid #287a72; border-radius: 6px; background: #f2f8f7; padding: .9rem 1rem; min-height: 118px; }
.qm-decision-panel__label { color: #4d6765; font-size: .68rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.qm-decision-panel__value { color: var(--qm-ink); font-size: 1.3rem; font-weight: 700; line-height: 1.25; margin: .22rem 0; overflow-wrap: anywhere; }
.qm-decision-panel__detail { color: var(--qm-muted); font-size: .78rem; line-height: 1.4; }
.qm-summary-panel { border: 1px solid var(--qm-line); border-radius: 6px; background: var(--qm-surface); padding: .75rem .85rem; min-height: 112px; }
.qm-summary-panel__label { color: var(--qm-muted); font-size: .68rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }
.qm-summary-panel__value { color: var(--qm-ink); font-size: 1rem; font-weight: 650; line-height: 1.35; margin: .25rem 0; overflow-wrap: anywhere; }
.qm-summary-panel__detail { color: var(--qm-muted); font-size: .74rem; line-height: 1.38; }
.qm-progress { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .5rem; margin: .4rem 0 .9rem; }
.qm-progress__step { border: 1px solid var(--qm-line); border-top: 3px solid #7791aa; border-radius: 5px; background: var(--qm-surface); padding: .58rem .62rem; min-width: 0; }
.qm-progress__name { color: var(--qm-ink); font-size: .75rem; font-weight: 650; line-height: 1.3; }
.qm-progress__state { color: var(--qm-green); font-size: .65rem; font-weight: 700; letter-spacing: .04em; margin-top: .3rem; }
.qm-progress__date { color: var(--qm-muted); font-size: .64rem; margin-top: .18rem; overflow-wrap: anywhere; }
@media (max-width: 900px) { .qm-progress { grid-template-columns: 1fr 1fr; } }

.qm-research-stage { display: grid; grid-template-columns: minmax(145px, 1.15fr) auto minmax(100px, .8fr) minmax(220px, 2fr); gap: .65rem; align-items: center; padding: .62rem .72rem; border: 1px solid var(--qm-line); border-radius: 5px; background: var(--qm-surface); margin: .32rem 0; }
.qm-research-stage__name { font-size: .79rem; font-weight: 650; color: var(--qm-ink); }
.qm-research-stage__date { font: 500 .72rem/1.35 ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--qm-muted); }
.qm-research-stage__detail { font-size: .74rem; color: var(--qm-muted); line-height: 1.35; }
@media (max-width: 800px) { .qm-research-stage { grid-template-columns: 1fr auto; } .qm-research-stage__detail { grid-column: 1 / -1; } }

[data-testid="stAlert"] { border-radius: 5px; padding: .65rem .75rem; }
[data-testid="stExpander"] { border-color: var(--qm-line); border-radius: 5px; background: var(--qm-surface); }
[data-testid="stDataFrame"] { border: 1px solid var(--qm-line); border-radius: 5px; overflow: hidden; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--qm-line) !important; border-radius: 6px !important; background: var(--qm-surface); }
.stButton button { border-radius: 4px; font-weight: 600; min-height: 2.35rem; }
.stButton button[kind="primary"] { background: #7d3030; border-color: #7d3030; }
.stButton button[kind="primary"]:hover { background: #672727; border-color: #672727; }
[data-testid="stAppDeployButton"], #MainMenu { display: none; }
</style>
"""


_COMPACT_LABELS = {
    "ENGINEERING_CLOSED_EVIDENCE_PENDING": "EVIDENCE PENDING",
    "NONE_APPROVED": "NO REPLACEMENT",
    "STALE_RUNNING": "STALE RUNNING",
    "NOT_STARTED": "NOT STARTED",
}


def apply_manager_theme(streamlit: object) -> None:
    streamlit.markdown(MANAGER_CSS, unsafe_allow_html=True)


def compact_status_label(value: object) -> str:
    text = str(value or "UNKNOWN").strip().upper()
    return _COMPACT_LABELS.get(text, text.replace("_", " "))


def status_badge_html(value: object, *, label: str | None = None) -> str:
    normalized = str(value or "UNKNOWN").strip().upper()
    if normalized in {"PASS", "COMPLETED", "SUCCEEDED", "AVAILABLE", "CONTINUOUS", "OK"}:
        tone = "success"
    elif normalized in {
        "WARN", "WARNING", "RUNNING", "STALE_RUNNING", "NOT_STARTED",
        "NOT STARTED", "ENGINEERING_CLOSED_EVIDENCE_PENDING", "PARTIAL",
        "INSUFFICIENT", "INSUFFICIENT_EVIDENCE",
        "SAMPLE_IMMATURE", "CONTINUITY_GAP",
    }:
        tone = "warning"
    elif normalized in {
        "FAIL", "FAILED", "ERROR", "UNAVAILABLE", "UNREADABLE", "SCHEMA_INCOMPATIBLE",
    }:
        tone = "danger"
    else:
        tone = "neutral"
    prefix = f'<span class="qm-badge__key">{escape(label)}:</span>' if label else ""
    return (
        f'<span class="qm-badge qm-badge--{tone}">{prefix}'
        f'{escape(compact_status_label(normalized))}</span>'
    )


def status_badge_row_html(*items: tuple[str, object]) -> str:
    badges = "".join(status_badge_html(value, label=label) for label, value in items)
    return f'<div class="qm-badge-row">{badges}</div>'


def summary_panel_html(*, label: str, value: object, detail: str) -> str:
    return (
        '<div class="qm-summary-panel">'
        f'<div class="qm-summary-panel__label">{escape(label)}</div>'
        f'<div class="qm-summary-panel__value">{escape(str(value))}</div>'
        f'<div class="qm-summary-panel__detail">{escape(detail)}</div>'
        '</div>'
    )


def decision_panel_html(*, label: str, value: object, detail: str) -> str:
    return (
        '<div class="qm-decision-panel">'
        f'<div class="qm-decision-panel__label">{escape(label)}</div>'
        f'<div class="qm-decision-panel__value">{escape(compact_status_label(value))}</div>'
        f'<div class="qm-decision-panel__detail">{escape(detail)}</div>'
        '</div>'
    )


def research_progression_html(
    stages: tuple[tuple[str, object, str | None], ...],
) -> str:
    steps = ''.join(
        '<div class="qm-progress__step">'
        f'<div class="qm-progress__name">{escape(name)}</div>'
        f'<div class="qm-progress__state">{escape(compact_status_label(state))}</div>'
        f'<div class="qm-progress__date">{escape(as_of or "AS OF UNKNOWN")}</div>'
        '</div>'
        for name, state, as_of in stages
    )
    return f'<div class="qm-progress">{steps}</div>'


def research_stage_row_html(
    *,
    name: str,
    state: object,
    as_of: str | None,
    detail: str,
) -> str:
    return (
        '<div class="qm-research-stage">'
        f'<div class="qm-research-stage__name">{escape(name)}</div>'
        f'<div>{status_badge_html(state)}</div>'
        f'<div class="qm-research-stage__date">{escape(as_of or "AS OF UNKNOWN")}</div>'
        f'<div class="qm-research-stage__detail">{escape(detail)}</div>'
        '</div>'
    )


def sidebar_metadata_html(*, environment: str, repository: str) -> str:
    return (
        '<div class="qm-sidebar-meta">'
        '<div class="qm-sidebar-meta__label">Environment</div>'
        f'<div class="qm-sidebar-meta__value">{escape(environment)}</div>'
        '<div class="qm-sidebar-meta__label">Repository</div>'
        f'<div class="qm-sidebar-meta__value">{escape(repository)}</div>'
        '</div>'
        '<div class="qm-nav-label">Workspace</div>'
    )
