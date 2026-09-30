from __future__ import annotations

from html import escape


MANAGER_CSS = """
<style>
:root {
  --qm-ink: #17202b;
  --qm-muted: #617083;
  --qm-line: #d9e0e7;
  --qm-surface: #ffffff;
  --qm-soft: #f4f6f8;
  --qm-navy: #172438;
  --qm-blue: #2d5f8b;
  --qm-green: #257052;
  --qm-amber: #8a5b12;
  --qm-red: #9a3636;
}

[data-testid="stAppViewContainer"] { background: #f4f6f8; color: var(--qm-ink); }
[data-testid="stHeader"] { background: rgba(244, 246, 248, .92); }
[data-testid="stMainBlockContainer"] {
  max-width: 1180px;
  padding: 2.15rem 2rem 4rem;
}
[data-testid="stMainBlockContainer"] > div { gap: .75rem; }

h1 { font-size: 1.75rem !important; line-height: 1.15 !important; letter-spacing: -.035em; margin-bottom: .1rem !important; }
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

.qm-console-mode { border-left: 3px solid #567894; background: #eaf0f5; color: #34485b; padding: .55rem .75rem; font-size: .78rem; margin: .45rem 0 .9rem; }

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
        "NOT STARTED", "ENGINEERING_CLOSED_EVIDENCE_PENDING",
    }:
        tone = "warning"
    elif normalized in {"FAIL", "FAILED", "ERROR", "UNAVAILABLE", "UNREADABLE"}:
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
