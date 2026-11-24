"""Design system: tokens, CSS, badges, cards and Plotly styling.

Follows Streamlit's own light/dark setting (Settings menu -> Theme) so that
native widgets and custom components always agree.
"""
from __future__ import annotations

import html
from typing import Iterable

import streamlit as st

CONGESTION_COLORS = {"Low": "#16a34a", "Medium": "#d97706", "High": "#dc2626"}
ACCENT = "#2563eb"

LIGHT = {"bg_card": "#ffffff", "bg_soft": "#f3f5f9", "border": "#dfe3ec", "text": "#0f172a",
         "muted": "#5b6577", "shadow": "0 1px 2px rgba(15,23,42,.06)"}
DARK = {"bg_card": "#161b26", "bg_soft": "#1d2433", "border": "#2b3446", "text": "#e6eaf2",
        "muted": "#98a2b8", "shadow": "0 1px 2px rgba(0,0,0,.35)"}

# label -> (foreground, background-light-mix)
BADGES = {
    "LIVE DATA": ("#0f7b3e", "#dcfce7"),
    "SYNTHETIC DATA": ("#92400e", "#fef3c7"),
    "CACHED DATA": ("#1d4ed8", "#dbeafe"),
    "UNAVAILABLE": ("#b91c1c", "#fee2e2"),
    "OSM · STATIC": ("#0f766e", "#ccfbf1"),
    "SIMULATED": ("#6d28d9", "#ede9fe"),
    "OK": ("#0f7b3e", "#dcfce7"),
    "DEGRADED": ("#92400e", "#fef3c7"),
    "DOWN": ("#b91c1c", "#fee2e2"),
}


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def tokens() -> dict:
    return DARK if is_dark() else LIGHT


def inject_css() -> None:
    t = tokens()
    st.markdown(f"""
<style>
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1400px; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
.ti-header {{ display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:.5rem; margin-bottom:.25rem; }}
.ti-title {{ font-size:1.65rem; font-weight:700; margin:0; }}
.ti-sub {{ color:{t['muted']}; font-size:.92rem; margin:0 0 .9rem 0; }}
.ti-card {{ background:{t['bg_card']}; border:1px solid {t['border']}; border-radius:12px; padding:14px 16px;
           box-shadow:{t['shadow']}; height:100%; min-height:132px; box-sizing:border-box; }}
.ti-card .lbl {{ color:{t['muted']}; font-size:.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }}
.ti-card .val {{ font-size:1.65rem; font-weight:700; line-height:1.25; margin-top:2px; }}
.ti-card .sub {{ color:{t['muted']}; font-size:.8rem; margin-top:2px; }}
.ti-badge {{ display:inline-block; font-size:.68rem; font-weight:700; letter-spacing:.05em; padding:2px 8px;
            border-radius:999px; vertical-align:middle; white-space:nowrap; }}
.ti-row {{ display:flex; align-items:center; justify-content:space-between; gap:.75rem; padding:9px 0;
          border-bottom:1px solid {t['border']}; }}
.ti-row:last-child {{ border-bottom:none; }}
.ti-row .name {{ font-weight:600; }}
.ti-row .detail {{ color:{t['muted']}; font-size:.82rem; }}
.ti-note {{ background:{t['bg_soft']}; border-left:3px solid {ACCENT}; padding:10px 14px; border-radius:6px;
           color:{t['text']}; font-size:.88rem; margin:.5rem 0 1rem 0; }}
.ti-legend span.dot {{ display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:6px; }}
[data-testid="stSidebar"] .ti-brand {{ font-weight:700; font-size:1.05rem; margin-bottom:.1rem; }}
[data-testid="stSidebar"] .ti-brand-sub {{ color:{t['muted']}; font-size:.78rem; margin-bottom:.8rem; }}
div[data-testid="stMetric"] {{ background:{t['bg_card']}; border:1px solid {t['border']}; border-radius:12px; padding:12px 14px; }}
</style>""", unsafe_allow_html=True)


def badge(label: str) -> str:
    fg, bg = BADGES.get(label, ("#334155", "#e2e8f0"))
    if is_dark():  # translucent chip keeps contrast on dark surfaces
        return f'<span class="ti-badge" style="color:{fg};background:{bg};filter:brightness(.97)">{html.escape(label)}</span>'
    return f'<span class="ti-badge" style="color:{fg};background:{bg}">{html.escape(label)}</span>'


def page_header(title: str, subtitle: str, badges: Iterable[str] = ()) -> None:
    chips = " ".join(badge(b) for b in badges)
    st.markdown(f'<div class="ti-header"><h1 class="ti-title">{html.escape(title)}</h1><div>{chips}</div></div>'
                f'<p class="ti-sub">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def card(label: str, value: str, sub: str = "", chip: str | None = None, help_text: str | None = None) -> str:
    chip_html = f'<div style="margin-top:6px">{badge(chip)}</div>' if chip else ""
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    title = f' title="{html.escape(help_text)}"' if help_text else ""
    return (f'<div class="ti-card"{title}><div class="lbl">{html.escape(label)}</div>'
            f'<div class="val">{html.escape(str(value))}</div>{sub_html}{chip_html}</div>')


def cards_row(items: list[dict]) -> None:
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        col.markdown(card(**item), unsafe_allow_html=True)


def status_row(name: str, detail: str, label: str) -> str:
    return (f'<div class="ti-row"><div><div class="name">{html.escape(name)}</div>'
            f'<div class="detail">{html.escape(detail)}</div></div><div>{badge(label)}</div></div>')


def note(text: str) -> None:
    st.markdown(f'<div class="ti-note">{html.escape(text)}</div>', unsafe_allow_html=True)


def style_fig(fig, height: int = 340):
    """Apply a consistent Plotly look that follows the active theme."""
    t = tokens()
    fig.update_layout(
        template="plotly_dark" if is_dark() else "plotly_white", height=height,
        margin=dict(l=10, r=10, t=44, b=70), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=t["text"], size=12), legend=dict(orientation="h", yanchor="top", y=-0.28, x=0),
        title=dict(font=dict(size=14)),
    )
    fig.update_xaxes(gridcolor=t["border"], zeroline=False)
    fig.update_yaxes(gridcolor=t["border"], zeroline=False)
    return fig


def show_chart(fig, height: int = 340) -> None:
    """Render a themed Plotly chart without the hover toolbar (it overlaps chart titles)."""
    st.plotly_chart(style_fig(fig, height), width="stretch", config={"displayModeBar": False})
