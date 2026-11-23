"""Real-time sentiment and trend monitoring dashboard.

Run:  python -m streamlit run dashboard.py

Reads the shared SQLite DB data/app.db for posts, trends, and sentiment.
Historical snapshots live in a separate file (data/history.db), so the
dashboard cannot contend with the pipeline for the single SQLite writer lock.

Auto-refresh uses st.fragment(run_every=N): only the chart region re-executes,
so sidebar filters and tab selection survive each tick.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import db
import entity_registry
from text_quality import filter_tokens

ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="AI Industry Sentiment & Trend Monitor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLOT_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=40, r=25, t=45, b=35),
    hovermode="x unified",
    font=dict(family="Inter, Segoe UI, sans-serif", size=12, color="#2b3440"),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)


# -------------------------------------------------------------------- utils
def q(sql: str, params=()) -> pd.DataFrame:
    """Read query that degrades to an empty frame and records interface errors."""
    errs = st.session_state.setdefault("_db_errors", [])
    out = db.q(sql, params, errors=errs)
    del errs[:-8]
    return out


def empty_fig(msg: str, height: int = 260) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=14, color="#8a94a3"))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(**{**PLOT_LAYOUT, "height": height})
    return fig


def show(fig: go.Figure, height: int | None = None):
    if height:
        fig.update_layout(height=height)
    st.plotly_chart(fig, width="stretch",
                    config={"displaylogo": False, "responsive": True})


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def as_dt(s):
    """ISO-8601 text column -> tz-aware pandas datetime (UTC)."""
    return pd.to_datetime(s, utc=True, format="mixed")


def to_hkt(s):
    """UTC column -> Hong Kong Time, tz-naive for Plotly.

    Display-only conversion: the shared DB is never rewritten. The +8 h shift
    is a whole number of hours, so hourly buckets stay aligned and every bar
    still represents a real 1-hour window. tz-naive values stop Plotly from
    re-localising them to the viewer's own machine timezone.
    """
    return as_dt(s).dt.tz_convert(config.DISPLAY_TZ).dt.tz_localize(None)


def in_clause(values) -> tuple[str, tuple]:
    vals = list(values)
    return (",".join("?" * len(vals)) or "''"), tuple(vals)


def wordcloud_figure(df: pd.DataFrame, max_words: int = 30,
                     height: int = 400) -> go.Figure:
    """Dependency-free word cloud: Plotly text on an R2 low-discrepancy layout.

    Font size encodes the burst_score. Avoids the `wordcloud` C-extension
    package (font problems on Windows) and stays interactive in the browser.
    """
    if df.empty:
        return empty_fig("No bursty keywords in the latest snapshot", height)
    items = [(str(r.keyword), float(r.burst_score),
              int(getattr(r, "count_1h", 0) or 0))
             for r in df.head(max_words).itertuples(index=False)]
    lo = min(s for _, s, _ in items)
    hi = max(s for _, s, _ in items)
    span = hi - lo if hi > lo else 1.0

    X_LIM, Y_LIM = 500.0, 225.0
    golden = math.pi * (3 - math.sqrt(5))
    R2_A, R2_B = 0.7548776662466927, 0.5698402909980532
    palette = ["#c0392b", "#d9603b", "#e08a2e", "#3b6fd4", "#2e9e7b", "#8a63d2"]
    placed, xs, ys, texts, sizes, colors, hovers = [], [], [], [], [], [], []

    for i, (term, score, cnt) in enumerate(items):
        norm = ((score - lo) / span) ** 0.65
        fs = 16 + 38 * norm
        w, h = len(term) * fs * 0.60, fs * 1.35
        tx = (((i + 1) * R2_A) % 1 - 0.5) * 2 * X_LIM * 0.80
        ty = (((i + 1) * R2_B) % 1 - 0.5) * 2 * Y_LIM * 0.74
        chosen = None
        for k in range(700):
            if k == 0:
                x, y = tx, ty
            else:
                theta = (i * 7 + k) * golden
                r = 9.0 * math.sqrt(k)
                x, y = tx + r * math.cos(theta), ty + r * math.sin(theta) * 0.55
            box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
            if box[0] < -X_LIM or box[2] > X_LIM or box[1] < -Y_LIM or box[3] > Y_LIM:
                continue
            if any(not (box[2] < b[0] or box[0] > b[2] or
                        box[3] < b[1] or box[1] > b[3]) for b in placed):
                continue
            chosen = box
            break
        if chosen is None:
            continue
        placed.append(chosen)
        xs.append((chosen[0] + chosen[2]) / 2)
        ys.append((chosen[1] + chosen[3]) / 2)
        texts.append(term)
        sizes.append(fs)
        colors.append(palette[min(len(palette) - 1, int(norm * len(palette)))])
        hovers.append(f"<b>{term}</b><br>burst {score:.2f}<br>last 1 h: {cnt}")

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="text", text=texts, hovertext=hovers,
        hovertemplate="%{hovertext}<extra></extra>",
        textfont=dict(size=sizes, color=colors, family="Inter, Segoe UI, sans-serif"),
        textposition="middle center"))
    fig.update_xaxes(visible=False, range=[-X_LIM, X_LIM])
    fig.update_yaxes(visible=False, range=[-Y_LIM, Y_LIM])
    fig.update_layout(**{**PLOT_LAYOUT, "height": height, "hovermode": "closest"})
    return fig


def recent_token_counts(cutoff_1h: str) -> Counter:
    """Supplementary view: real 1 h term frequency from clean_posts.tokens.

    This is computed by the dashboard for display only. It is NOT the
    burst_score and must not be presented as their algorithm's output.
    """
    rows = q("SELECT tokens FROM clean_posts WHERE created_at >= ?", (cutoff_1h,))
    c = Counter()
    for raw in rows.get("tokens", pd.Series(dtype=str)):
        if not raw:
            continue
        try:
            words = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(words, list):
            c.update(filter_tokens(words))
    return c


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("⚙️ Controls")
    st.caption(f"**{config.DOMAIN_NAME}**  \n{config.DOMAIN_NOTE}  \n"
               f"Source: {config.DATA_SOURCE}")
    st.divider()

    auto = st.toggle("Auto-refresh", value=True,
                     help="Streamlit native timer: only the chart area re-runs, so widget state is kept")
    interval_label = st.selectbox(
        "Refresh interval", list(config.REFRESH_OPTIONS),
        index=list(config.REFRESH_OPTIONS).index(config.DEFAULT_REFRESH),
        disabled=not auto)
    refresh_secs = config.REFRESH_OPTIONS[interval_label]
    window_label = st.selectbox(
        "Time window", list(config.TIME_WINDOWS),
        index=list(config.TIME_WINDOWS).index(config.DEFAULT_WINDOW))
    window_hours = config.TIME_WINDOWS[window_label]
    top_n = st.slider("Top-N trending terms", 5, 40, config.DEFAULT_TOP_N)
    burst_min = st.number_input("Min burst score", 0.0, 50.0,
                                float(config.BURST_MIN_SCORE), 0.5)

    entity_options = sorted(config.TRACKED_ENTITIES.keys())
    selected_entities = st.multiselect("Tracked entities", entity_options,
                                       default=entity_options,
                                       key="tracked_entities_selection")
    st.caption("Entities and aliases are defined once in `entity_registry.py`.")

    with st.expander("Manage entities", expanded=False):
        current_entities = (
            dict(entity_registry.DEFAULT_ENTITIES)
            if entity_registry.PROFILE != "default"
            else dict(entity_registry.TRACKED_ENTITIES)
        )
        st.caption(
            "Changes are saved to `data/entity_overrides.json` and applied to "
            "the default profile."
        )

        add_name = st.text_input("Entity name", key="add_entity_name",
                                 placeholder="Microsoft")
        add_aliases = st.text_input(
            "Aliases (comma-separated)",
            key="add_entity_aliases",
            placeholder="microsoft, microsoft corporation, windows, azure",
        )
        if st.button("Add or update entity", width="stretch"):
            name = add_name.strip()
            aliases = tuple(
                alias.strip().lower()
                for alias in add_aliases.split(",")
                if alias.strip()
            )
            if not name:
                st.error("Entity name is required.")
            elif not aliases:
                st.error("Enter at least one alias.")
            else:
                current_entities[name] = aliases
                entity_registry.save_custom_entities(current_entities)
                entity_registry.PROFILE = "default"
                entity_registry.TRACKED_ENTITIES = dict(current_entities)
                config.ENTITY_PROFILE = "default"
                config.TRACKED_ENTITIES = dict(current_entities)
                st.success(f"Saved {name}. Re-analyze the database to generate data.")
                st.rerun()

        removable = sorted(current_entities)
        remove_entities = st.multiselect(
            "Entities to remove",
            removable,
            key="remove_entities",
        )
        col_remove, col_reset = st.columns(2)
        with col_remove:
            if st.button("Remove selected", width="stretch",
                         disabled=not remove_entities):
                for name in remove_entities:
                    current_entities.pop(name, None)
                if current_entities:
                    entity_registry.save_custom_entities(current_entities)
                    active_entities = dict(current_entities)
                else:
                    entity_registry.reset_custom_entities()
                    active_entities = dict(entity_registry.DEFAULT_ENTITIES)
                entity_registry.PROFILE = "default"
                entity_registry.TRACKED_ENTITIES = dict(active_entities)
                config.ENTITY_PROFILE = "default"
                config.TRACKED_ENTITIES = dict(active_entities)
                st.success("Entity configuration updated.")
                st.rerun()
        with col_reset:
            if st.button("Restore defaults", width="stretch"):
                entity_registry.reset_custom_entities()
                active_entities = dict(entity_registry.DEFAULT_ENTITIES)
                entity_registry.PROFILE = "default"
                entity_registry.TRACKED_ENTITIES = dict(active_entities)
                config.ENTITY_PROFILE = "default"
                config.TRACKED_ENTITIES = dict(active_entities)
                st.success("Restored the shipped entity set.")
                st.rerun()

        if st.button("Apply and re-analyze entities", width="stretch"):
            with st.spinner("Re-analyzing cleaned posts with current entities…"):
                env = os.environ.copy()
                env["ENTITY_PROFILE"] = "default"
                env["APP_DB"] = str(db.db_path())
                res = subprocess.run(
                    [sys.executable, str(ROOT / "run_pipeline.py"),
                     "--offline"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    cwd=str(ROOT),
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                if res.returncode == 0:
                    st.success("Entity configuration applied.")
                    st.code((res.stdout or "")[-800:], language="text")
                else:
                    st.error((res.stderr or res.stdout or "unknown error")[-800:])
            st.rerun()

    st.divider()
    if st.button("🔄 Refresh now", width="stretch"):
        st.rerun()
    if st.button("▶ Run pipeline once", width="stretch",
                 help="Runs ingestion, cleaning, trend detection and sentiment scoring against the live Hacker News API"):
        with st.spinner("Ingesting / cleaning / detecting / scoring…"):
            try:
                res = subprocess.run(
                    [sys.executable, str(ROOT / "run_pipeline.py"), "--once"],
                    capture_output=True, text=True, timeout=180, cwd=str(ROOT),
                    encoding="utf-8", errors="replace")
                if res.returncode == 0:
                    st.success("Pipeline cycle complete")
                    st.code((res.stdout or "")[-800:], language="text")
                else:
                    st.error((res.stderr or res.stdout or "unknown error")[-800:])
            except Exception as exc:                  # noqa: BLE001
                st.error(str(exc))
        st.rerun()

    st.caption(f"Shared DB: `{db.db_path().name}` (read-only)  \n"
               f"History DB: `{db.history_path().name}`  \n"
               f"Display timezone: **{config.DISPLAY_TZ_NAME}**  \n"
               "Stored timestamps stay UTC; only labels are converted.")
    with st.expander("Run commands"):
        st.code("python run_pipeline.py --once\n"
                "python run_pipeline.py --watch --interval 60\n"
                "python -m streamlit run dashboard.py", language="bash")


# -------------------------------------------------------------- live region
@st.fragment(run_every=(refresh_secs if auto else None))
def render_live():
    if not db.db_path().exists():
        st.info("### Shared database not found\n\n"
                "Run `python run_pipeline.py --once` in the project root, "
                "or set `APP_DB` to point at `data/app.db`.", icon="ℹ️")
        return

    db.record_snapshot()

    clock = db.data_clock()
    cutoff = db.iso_cutoff(window_hours)
    cutoff_1h = db.iso_cutoff(1)
    ent_sql, ent_params = in_clause(selected_entities)

    # ---- Trend detection ------------------------------------------------
    trend_stamp = q("SELECT MAX(updated_at) s FROM trending_keywords")
    stamp = (float(trend_stamp["s"].iloc[0])
             if not trend_stamp.empty and pd.notna(trend_stamp["s"].iloc[0]) else None)
    # Raw frequencies are persisted for trend snapshots. Fall back to the dashboard's own
    # counter when reading an older 3-column snapshot.
    has_freq = {"recent_count", "baseline_count"} <= set(
        db.table_columns("trending_keywords"))
    freq_sql = ", recent_count, baseline_count" if has_freq else ""
    trends = q(
        f"SELECT keyword, burst_score, updated_at{freq_sql} FROM trending_keywords "
        "WHERE updated_at = (SELECT MAX(updated_at) FROM trending_keywords) "
        "AND burst_score >= ? ORDER BY burst_score DESC, keyword LIMIT ?",
        (burst_min, top_n))
    tokens_1h = recent_token_counts(cutoff_1h)
    if not trends.empty:
        trends["count_1h"] = (
            trends["recent_count"].fillna(0).astype(int) if has_freq
            else trends["keyword"].map(lambda k: tokens_1h.get(k, 0)))
    n_trends_all = db.scalar("SELECT COUNT(*) FROM trending_keywords", default=0) or 0

    # ---- Ingestion and cleaning -----------------------------------------
    volume = q(
        "SELECT substr(c.created_at,1,13) || ':00:00+00:00' AS bucket, "
        "       r.source, COUNT(*) AS n "
        "FROM clean_posts c LEFT JOIN raw_posts r ON r.post_id = c.post_id "
        "WHERE c.created_at >= ? GROUP BY bucket, r.source ORDER BY bucket",
        (cutoff,))

    # ---- Sentiment analysis ---------------------------------------------
    sent_ts = q(
        "SELECT entity, time_bucket, post_count, avg_compound, positive_count, "
        "       negative_count, neutral_count, sentiment_shift "
        "FROM sentiment_trends WHERE time_bucket >= ? "
        f"AND entity IN ({ent_sql}) ORDER BY time_bucket, entity",
        (cutoff, *ent_params))
    labels = q(
        "SELECT r.entity, r.sentiment_label, COUNT(*) AS n "
        "FROM sentiment_results r JOIN clean_posts c ON c.post_id = r.post_id "
        f"WHERE c.created_at >= ? AND r.entity IN ({ent_sql}) "
        "GROUP BY r.entity, r.sentiment_label",
        (cutoff, *ent_params))
    scores = q(
        "SELECT r.entity, r.compound_score, r.sentiment_label, r.analyzed_at "
        "FROM sentiment_results r JOIN clean_posts c ON c.post_id = r.post_id "
        f"WHERE c.created_at >= ? AND r.entity IN ({ent_sql})",
        (cutoff, *ent_params))
    shifts = q(
        "SELECT entity, time_bucket, avg_compound, post_count, sentiment_shift "
        "FROM sentiment_trends WHERE sentiment_shift <> 'stable' "
        "AND time_bucket >= ? ORDER BY time_bucket DESC LIMIT 30", (cutoff,))
    recent = q(
        "SELECT c.created_at, r.source, c.title_clean, s.entity, "
        "       s.sentiment_label, s.compound_score "
        "FROM clean_posts c "
        "LEFT JOIN raw_posts r ON r.post_id = c.post_id "
        "LEFT JOIN sentiment_results s ON s.post_id = c.post_id "
        "WHERE c.created_at >= ? ORDER BY c.created_at DESC LIMIT 120",
        (cutoff,))

    # ---- History ---------------------------------------------------------
    hist = db.history_q(
        "SELECT source_updated_at, keyword, burst_score, rank_no "
        "FROM trend_snapshots ORDER BY source_updated_at")
    run_log = db.history_q(
        "SELECT component, ran_at, rows_in, note FROM run_log "
        "ORDER BY ran_at DESC LIMIT 12")

    # ---- Derived component freshness ------------------------------------
    def _ts(sql, is_unix=False):
        v = db.scalar(sql)
        if v is None:
            return None
        try:
            return float(v) if is_unix else datetime.fromisoformat(str(v)).timestamp()
        except Exception:                          # noqa: BLE001
            return None

    now_unix = db.utc_now()
    comp = [
        ("Ingestion", _ts("SELECT MAX(collected_at) FROM raw_posts"),
         "raw_posts.collected_at"),
        ("Cleaning & Storage", _ts("SELECT MAX(inserted_at) FROM clean_posts"),
         "clean_posts.inserted_at"),
        ("Trend Detection", _ts("SELECT MAX(updated_at) FROM trending_keywords", True),
         "trending_keywords.updated_at"),
        ("Sentiment Scoring", _ts("SELECT MAX(analyzed_at) FROM sentiment_results"),
         "sentiment_results.analyzed_at"),
    ]

    # ---- header -----------------------------------------------------------
    stamp_txt = db.iso(stamp)
    now_hkt = pd.Timestamp.now(tz="UTC").tz_convert(config.DISPLAY_TZ)
    clock_hkt = clock.tz_convert(config.DISPLAY_TZ)
    st.markdown(
        f"#### 🕒 Last refresh {now_hkt.strftime('%H:%M:%S')} HKT"
        f"  ·  {'Auto-refresh ' + interval_label if auto else 'Auto-refresh off'}"
        f"  ·  Data clock {clock_hkt.strftime('%Y-%m-%d %H:%M')} HKT")
    st.caption(
        f"All times are shown in {config.DISPLAY_TZ_NAME}. The shared DB still stores UTC "
        "(ISO 8601 text and REAL Unix seconds) — the group's interface contract is unchanged. "
        "Queries are anchored on `MAX(clean_posts.created_at)`, the same baseline as trend detection, "
        "so a static snapshot still renders instead of looking empty.")

    if stamp and now_unix - stamp > 3 * config.STALE_AFTER_SECONDS:
        st.warning(
            f"The trend snapshot is {int((now_unix-stamp)/60)} min old; this is **static data**. "
            "Run `python run_pipeline.py --watch --interval 60` in a terminal to see the numbers change.",
            icon="⏳")

    n_raw = db.scalar("SELECT COUNT(*) FROM raw_posts", default=0) or 0
    n_clean = db.scalar("SELECT COUNT(*) FROM clean_posts", default=0) or 0
    n_results = db.scalar("SELECT COUNT(*) FROM sentiment_results", default=0) or 0
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Raw posts", fmt_int(n_raw))
    k2.metric("Clean posts", fmt_int(n_clean))
    k3.metric("Trending terms", fmt_int(n_trends_all))
    k4.metric("Entity mentions scored", fmt_int(n_results))
    k5.metric("Tracked entities", fmt_int(len(entity_options)))
    st.divider()

    tab_overview, tab_trend, tab_sent, tab_ops = st.tabs(
        ["📊 Overview", "🔥 Trend Detection", "💬 Sentiment", "🔌 Interface & Status"])

    # ==================================================== TAB 1 : OVERVIEW
    with tab_overview:
        left, right = st.columns([1.35, 1])
        with left:
            st.subheader("Post Volume per Hour")
            if volume.empty:
                show(empty_fig("No data in clean_posts"), 300)
            else:
                volume["t"] = to_hkt(volume["bucket"])
                fig = go.Figure()
                for src, grp in volume.groupby("source", dropna=False):
                    fig.add_trace(go.Bar(
                        x=grp["t"], y=grp["n"], name=str(src or "unknown"),
                        marker_color="#3b6fd4"))
                fig.update_layout(**{**PLOT_LAYOUT, "height": 300,
                                     "barmode": "stack", "yaxis_title": "posts / hour",
                                     "xaxis_title": config.DISPLAY_AXIS_LABEL})
                show(fig)
        with right:
            st.subheader("Sentiment Composition")
            if labels.empty:
                show(empty_fig("No data in sentiment_results"), 300)
            else:
                agg = labels.groupby("sentiment_label")["n"].sum()
                order = [k for k in ("positive", "neutral", "negative") if k in agg.index]
                fig = go.Figure(go.Pie(
                    labels=[config.LABEL_TEXT[k] for k in order],
                    values=[agg[k] for k in order], hole=0.62, sort=False,
                    marker_colors=[config.LABEL_COLORS[k] for k in order]))
                fig.update_layout(**{**PLOT_LAYOUT, "height": 300,
                                     "showlegend": True, "hovermode": "closest"})
                show(fig)

        curve_col, burst_col = st.columns([1.35, 1])
        with curve_col:
            st.subheader("Sentiment Trend by Entity")
            st.caption(
                f"{window_label} query cutoff: `{cutoff}` | "
                f"{len(sent_ts)} sentiment trend rows in the current selection"
            )
            if sent_ts.empty:
                fig = go.Figure()
                fig.add_hrect(y0=-0.05, y1=0.05, fillcolor=config.COLOR_NEUTRAL,
                              opacity=0.13, line_width=0,
                              annotation_text="Neutral band", annotation_position="top left")
                for idx, ent in enumerate(sorted(selected_entities)):
                    color = config.ENTITY_PALETTE[
                        idx % len(config.ENTITY_PALETTE)]
                    fig.add_trace(go.Scatter(
                        x=[None],
                        y=[None],
                        name=ent,
                        mode="lines+markers",
                        visible="legendonly",
                        marker=dict(size=5, color=color),
                        line=dict(width=2.4, color=color),
                        hovertemplate="<b>%{fullData.name}</b>"
                                      "<br>No data in selected window<extra></extra>",
                    ))
                fig.add_hline(y=0, line=dict(color="#b6bdc7", width=1, dash="dot"))
                fig.add_annotation(
                    text="No data in sentiment_trends",
                    x=0.5, y=0.5, xref="paper", yref="paper",
                    showarrow=False,
                    font=dict(size=14, color="#8a94a3"),
                )
                fig.update_layout(**{**PLOT_LAYOUT, "height": 320,
                                     "yaxis_title": "avg compound",
                                     "yaxis_range": [-1, 1],
                                     "xaxis_title": config.DISPLAY_AXIS_LABEL,
                                     "margin": dict(l=45, r=15, t=45, b=30)})
                show(fig)
            else:
                fig = go.Figure()
                fig.add_hrect(y0=-0.05, y1=0.05, fillcolor=config.COLOR_NEUTRAL,
                              opacity=0.13, line_width=0,
                              annotation_text="Neutral band", annotation_position="top left")
                for idx, ent in enumerate(sorted(selected_entities)):
                    grp = sent_ts[sent_ts["entity"] == ent].sort_values("time_bucket")
                    color = config.ENTITY_PALETTE[
                        idx % len(config.ENTITY_PALETTE)]
                    if grp.empty:
                        fig.add_trace(go.Scatter(
                            x=[None],
                            y=[None],
                            name=ent,
                            mode="lines+markers",
                            visible="legendonly",
                            marker=dict(size=5, color=color),
                            line=dict(width=2.4, color=color),
                            hovertemplate="<b>%{fullData.name}</b>"
                                          "<br>No data in selected window<extra></extra>",
                        ))
                    else:
                        fig.add_trace(go.Scatter(
                            x=to_hkt(grp["time_bucket"]),
                            y=grp["avg_compound"].rolling(3, min_periods=1).mean(),
                            name=ent, mode="lines+markers",
                            marker=dict(size=5, color=color),
                            line=dict(width=2.4, color=color),
                            customdata=grp["post_count"],
                            hovertemplate="%{fullData.name}: %{y:.3f}"
                                          "<br>mentions %{customdata}<extra></extra>"))
                fig.add_hline(y=0, line=dict(color="#b6bdc7", width=1, dash="dot"))
                fig.update_layout(**{**PLOT_LAYOUT, "height": 320,
                                     "yaxis_title": "avg compound",
                                     "yaxis_range": [-1, 1], "xaxis_title": config.DISPLAY_AXIS_LABEL,
                                     "margin": dict(l=45, r=15, t=45, b=30)})
                show(fig)
        with burst_col:
            st.subheader("Top 8 Trending Terms")
            if trends.empty:
                show(empty_fig("No trending terms, or threshold too high"), 320)
            else:
                t8 = trends.head(8).iloc[::-1]
                fig = go.Figure(go.Bar(
                    x=t8["burst_score"], y=t8["keyword"], orientation="h",
                    marker_color=config.COLOR_BURST_HIGH,
                    customdata=t8["count_1h"],
                    hovertemplate="<b>%{y}</b><br>burst %{x:.2f}"
                                  "<br>last 1 h: %{customdata}<extra></extra>"))
                fig.update_layout(**{**PLOT_LAYOUT, "height": 320,
                                     "xaxis_title": "burst score",
                                     "yaxis_title": None, "hovermode": "closest",
                                     "margin": dict(l=150, r=15, t=45, b=30)})
                show(fig)

    # ====================================================== TAB 2 : TRENDS
    with tab_trend:
        st.subheader(f"Trending Terms — Top {top_n}")
        if not trends.empty:
            eff_min = int(trends["count_1h"].min())
            n_tokens = sum(tokens_1h.values())
            st.caption(
                f"Quality gate: **{n_tokens}** content tokens in the last 1 h. "
                f"Minimum frequency this cycle **≥{eff_min}**; the threshold is fixed and"
                " is never relaxed to pad the chart. Ties are broken by actual frequency."
                f"Formula: `{config.TREND_BURST_FORMULA}`. The baseline is normalised to an hourly rate, "
                "so unequal 1 h / 24 h windows do not inflate brand-new words.")
            if len(trends) < 8:
                st.warning(
                    f"Small 1 h sample: only **{len(trends)} terms** meet the minimum frequency"
                    f" of ≥{config.TREND_MIN_RECENT_COUNT}; interpret with care. "
                    "Keep `python run_pipeline.py --watch` running and the list will grow naturally. "
                    "Noise terms are not added to fill the chart.",
                    icon="ℹ️")
        if trends.empty:
            show(empty_fig("No data in trending_keywords, or all below threshold"), 380)
        else:
            t = trends.iloc[::-1]
            fig = go.Figure(go.Bar(
                x=t["burst_score"], y=t["keyword"], orientation="h",
                marker=dict(color=t["burst_score"],
                            colorscale=[[0, config.COLOR_BURST_LOW],
                                        [1, config.COLOR_BURST_HIGH]],
                            colorbar=dict(title="burst", thickness=12)),
                customdata=np.stack([t["count_1h"]], axis=-1),
                hovertemplate="<b>%{y}</b><br>burst %{x:.2f}"
                              "<br>last 1 h: %{customdata[0]}<extra></extra>"))
            fig.update_layout(**{**PLOT_LAYOUT,
                                 "height": max(380, 24 * len(t) + 90),
                                 "xaxis_title": "burst score", "yaxis_title": None,
                                 "hovermode": "closest",
                                 "margin": dict(l=150, r=95, t=45, b=40)})
            show(fig)

        st.subheader("Trending Keyword Word Cloud")
        show(wordcloud_figure(trends, max_words=min(top_n, 30), height=400))

        st.subheader("Burst Score over Time")
        # Only compare snapshots produced by the SAME burst formula. The
        # history also contains 'pre-fix' rows from before rate normalisation;
        # mixing them would draw a fake spike that is really an algorithm change.
        same_formula = db.history_q(
            "SELECT source_updated_at, MAX(burst_score) AS max_b, "
            "AVG(burst_score) AS avg_b, COUNT(*) AS n_kw "
            "FROM trend_snapshots WHERE formula = ? "
            "GROUP BY source_updated_at ORDER BY source_updated_at",
            (config.TREND_BURST_FORMULA,))
        old_formula = db.history_q(
            "SELECT COUNT(DISTINCT source_updated_at) n FROM trend_snapshots "
            "WHERE formula = 'pre-fix'")
        agg_hist = same_formula
        if agg_hist.empty or len(agg_hist) < 2:
            show(empty_fig("Fewer than 2 snapshots; keep the pipeline running"), 300)
        else:
            x = (pd.to_datetime(agg_hist["source_updated_at"], unit="s", utc=True)
                 .dt.tz_convert(config.DISPLAY_TZ).dt.tz_localize(None))
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=x, y=agg_hist["max_b"], name="Max burst",
                                     mode="lines+markers", marker=dict(size=6),
                                     line=dict(width=2.6, color=config.COLOR_BURST_HIGH)))
            fig.add_trace(go.Scatter(x=x, y=agg_hist["avg_b"], name="Mean burst",
                                     mode="lines+markers", marker=dict(size=5),
                                     line=dict(width=2.2, color=config.COLOR_ACCENT)))
            fig.update_layout(**{**PLOT_LAYOUT, "height": 300,
                                 "yaxis_title": "burst score", "xaxis_title": config.DISPLAY_AXIS_LABEL,
                                 "hovermode": "x unified"})
            show(fig)
            # The whole Top-20 may be replaced each cycle, so per-word line
            # continuity is NOT guaranteed. Report the real overlap instead.
            if len(agg_hist) >= 2:
                last_two = agg_hist["source_updated_at"].tail(2).tolist()
                sets = []
                for ts in last_two:
                    kw = db.history_q(
                        "SELECT keyword FROM trend_snapshots WHERE source_updated_at=?",
                        (ts,))
                    sets.append(set(kw["keyword"]) if not kw.empty else set())
                overlap = len(sets[0] & sets[1])
                st.caption(
                    f"Terms present in both of the last two snapshots: **{overlap} / {len(sets[1])}**. "
                    "The detector rewrites the whole Top-20 each cycle, so the chart aggregates per snapshot"
                    " instead of drawing a misleading line for a single term.")

        n_old = int(old_formula["n"].iloc[0]) if not old_formula.empty else 0
        if n_old:
            st.caption(
                f"Comparability: **{n_old} older snapshots** were produced before the formula change"
                ", using the original raw ratio, and are not on the same scale as the current normalised formula; "
                "they are excluded so the chart cannot show a fake spike.")
        st.subheader("Actual Term Frequency, Last 1 h")
        if tokens_1h:
            tf = pd.DataFrame(tokens_1h.most_common(20),
                              columns=["Term", "Count"]).iloc[::-1]
            # Descending visual order comes from the ROW ORDER itself. Do NOT
            # use categoryorder="array" here: with a horizontal Bar it can
            # decouple values from labels (the ai=7 bar was labelled applemail).
            fig = go.Figure(go.Bar(x=tf["Count"], y=tf["Term"],
                                   orientation="h", marker_color="#2a9dbf"))
            # 20 categories need ~24 px each, otherwise Plotly silently hides
            # every other tick label and the bars LOOK mislabelled.
            fig.update_layout(**{**PLOT_LAYOUT,
                                 "height": max(420, 24 * len(tf) + 90),
                                 "xaxis_title": "count", "yaxis_title": None,
                                 "hovermode": "closest",
                                 "margin": dict(l=110, r=25, t=45, b=35)})
            fig.update_yaxes(
                tickmode="array", tickvals=tf["Term"].tolist(),
                ticktext=tf["Term"].tolist(), tickfont=dict(size=11))
            show(fig)
        else:
            show(empty_fig("No tokens in the last 1 h"), 300)
        st.caption("Computed live from `clean_posts.tokens` after stop-word filtering, for reference only; "
                   "**not** the burst score. Current formula: "
                   "`freq(1 h) / (freq(prior 23 h) / 23 + 1)`, i.e. the baseline is converted to an hourly rate; "
                   "`BURST_FORMULA=original` restores the original ratio. "
                   "Top-N and raw counts are written to `trending_keywords` by the detector.")

        st.subheader("Keyword Detail")
        if trends.empty:
            st.caption("—")
        else:
            cols = ["keyword", "burst_score", "count_1h"]
            names = ["Term", "burst_score", "1 h count"]
            if has_freq:
                cols.append("baseline_count")
                names.append("24 h baseline")
            detail = trends[cols].copy()
            detail.columns = names
            detail["burst_score"] = detail["burst_score"].round(2)
            detail.insert(0, "Rank", range(1, len(detail) + 1))
            st.dataframe(detail, width="stretch", hide_index=True, height=320)
            st.caption("Raw counts are written directly to `trending_keywords`"
                       ", as required by the brief, and are not recomputed by the dashboard.")
        st.caption(f"Snapshot: {stamp_txt}  ·  Window: {window_label}"
                   "  ·  Written by the detector; this dashboard is read-only.")

    # =================================================== TAB 3 : SENTIMENT
    with tab_sent:
        if not selected_entities:
            st.warning("Select at least one tracked entity in the sidebar.")
            focus = None
        else:
            focus = st.selectbox("Entity detail", selected_entities, key="focus_entity")
        if focus:
            g = sent_ts[sent_ts["entity"] == focus].sort_values("time_bucket")
            if g.empty:
                show(empty_fig(f"No sentiment time series for {focus} in this window"), 300)
                st.caption("Sentiment is scored only for posts matching an entity alias; "
                           "entity mentions are naturally sparse in general Hacker News tech stories.")
            else:
                cur = float(g["avg_compound"].tail(3).mean())
                prev = (float(g["avg_compound"].iloc[-6:-3].mean())
                        if len(g) >= 6 else float(g["avg_compound"].head(3).mean()))
                a, b, d = st.columns(3)
                a.metric("Latest Average, 3 Buckets", f"{cur:.3f}", f"{cur-prev:+.3f}")
                b.metric("Mentions in Window", fmt_int(g["post_count"].sum()))
                d.metric("Overall Tone", "Positive" if cur > 0.05 else
                         ("Negative" if cur < -0.05 else "Neutral"))

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=to_hkt(g["time_bucket"]), y=g["avg_compound"],
                    name="Hourly entity average",
                    mode="lines+markers", marker=dict(size=5, color="#c3ccd8"),
                    line=dict(color="#c3ccd8", width=1.4)))
                fig.add_trace(go.Scatter(
                    x=to_hkt(g["time_bucket"]),
                    y=g["avg_compound"].rolling(3, min_periods=1).mean(),
                    name="3-hour moving average", mode="lines",
                    line=dict(color=config.COLOR_ACCENT, width=2.8)))
                marked = g[g["sentiment_shift"] != "stable"]
                if not marked.empty:
                    fig.add_trace(go.Scatter(
                        x=to_hkt(marked["time_bucket"]),
                        y=marked["avg_compound"], mode="markers", name="Significant shift",
                        marker=dict(size=12, symbol="diamond", color="#e2603f",
                                    line=dict(width=1, color="#fff"))))
                fig.add_hline(y=0, line=dict(color="#b6bdc7", width=1, dash="dot"))
                fig.update_layout(**{**PLOT_LAYOUT, "height": 300,
                                     "yaxis_title": "avg compound",
                                     "yaxis_range": [-1, 1],
                                     "xaxis_title": config.DISPLAY_AXIS_LABEL})
                show(fig)
                st.caption(
                    "Line definitions: gray = average compound score for one hourly "
                    "bucket; blue = rolling mean of the current and previous two "
                    "hourly buckets (fewer buckets are used near the start); "
                    "red diamond = significant shift between consecutive hourly averages."
                )

                st.caption("Label Counts by Hour")
                # Bars, not a stacked area: entity mentions are sparse (often only
                # 1-2 hourly buckets), and an area chart would interpolate a
                # misleading slope between distant points.
                fig2 = go.Figure()
                for col, name, color in (
                        ("positive_count", "Positive", config.COLOR_POSITIVE),
                        ("neutral_count", "Neutral", config.COLOR_NEUTRAL),
                        ("negative_count", "Negative", config.COLOR_NEGATIVE)):
                    fig2.add_trace(go.Bar(
                        x=to_hkt(g["time_bucket"]), y=g[col], name=name,
                        marker_color=color,
                        width=30 * 60 * 1000))  # 30 min in ms on a date axis
                fig2.update_layout(**{**PLOT_LAYOUT, "height": 250,
                                      "barmode": "stack", "yaxis_title": "posts",
                                      "xaxis_title": config.DISPLAY_AXIS_LABEL,
                                      "hovermode": "x unified"})
                show(fig2)

        st.subheader("VADER Compound Distribution")
        if scores.empty:
            show(empty_fig("No data in sentiment_results"), 260)
        else:
            fig = go.Figure()
            for label, color in config.LABEL_COLORS.items():
                sub = scores[scores["sentiment_label"] == label]
                if not sub.empty:
                    fig.add_trace(go.Histogram(
                        x=sub["compound_score"], name=config.LABEL_TEXT[label],
                        marker_color=color, opacity=0.8, nbinsx=20))
            fig.update_layout(**{**PLOT_LAYOUT, "height": 260, "barmode": "overlay",
                                 "xaxis_title": "compound", "yaxis_title": "posts",
                                 "hovermode": "closest"})
            show(fig)

        st.subheader("⚠️ Significant Sentiment Shifts")
        if shifts.empty:
            st.success("No significant shifts flagged in this window.", icon="✅")
        else:
            s = shifts.copy()
            s["Time (HKT)"] = to_hkt(s["time_bucket"]).dt.strftime("%m-%d %H:%M")
            s["Direction"] = s["sentiment_shift"].map(
                {"positive": "↑ to Positive", "negative": "↓ to Negative"}).fillna("—")
            s = s[["entity", "Time (HKT)", "Direction", "avg_compound", "post_count"]]
            s.columns = ["Entity", "Time (HKT)", "Shift", "avg_compound", "Mentions"]
            s["avg_compound"] = s["avg_compound"].round(3)
            st.dataframe(s, width="stretch", hide_index=True, height=260)

        st.subheader("Recent Posts with Sentiment")
        if recent.empty:
            st.caption("—")
        else:
            rp = recent.copy()
            rp["Time (HKT)"] = to_hkt(rp["created_at"]).dt.strftime("%m-%d %H:%M")
            rp["Title"] = rp["title_clean"].astype(str).str.slice(0, 110)
            rp["Sentiment"] = rp["sentiment_label"].map(config.LABEL_TEXT).fillna("—")
            rp["compound_score"] = rp["compound_score"].round(3)
            rp = rp[["Time (HKT)", "source", "entity", "Sentiment",
                     "compound_score", "Title"]]
            rp.columns = ["Time (HKT)", "Source", "Entity", "Sentiment", "compound", "Title"]
            st.dataframe(rp, width="stretch", hide_index=True, height=320)

    # =================================================== TAB 4 : OPS / API
    with tab_ops:
        st.subheader("Pipeline Component Freshness")
        hb_rows = []
        for name, ts, source_col in comp:
            if ts is None:
                hb_rows.append({"Component": name, "Status": "⚪ No data",
                                "Last write": "—", "Age": "—", "Timestamp column": source_col})
                continue
            age = now_unix - ts
            state = ("🔴 Stale" if age > 3 * config.STALE_AFTER_SECONDS else
                     "🟡 Lagging" if age > config.STALE_AFTER_SECONDS else "🟢 Healthy")
            hb_rows.append({"Component": name, "Status": state,
                            "Last write": db.iso(ts),
                            "Age": f"{int(age/60)} min" if age >= 60 else f"{int(age)} s",
                            "Timestamp column": source_col})
        st.dataframe(pd.DataFrame(hb_rows), width="stretch", hide_index=True)
        st.caption("No heartbeat API exists, so status is derived from the timestamps in each table; "
                   "Age is relative to the machine clock, so a static snapshot may look old.")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Shared DB Row Counts")
            st.dataframe(db.table_counts(), width="stretch", hide_index=True)
        with c2:
            st.subheader("Snapshot History")
            n_snap = db.history_q(
                "SELECT COUNT(DISTINCT source_updated_at) n FROM trend_snapshots")
            n_kw = db.history_q("SELECT COUNT(*) n FROM trend_snapshots")
            st.metric("Trend snapshots recorded",
                      fmt_int(n_snap["n"].iloc[0] if not n_snap.empty else 0))
            st.metric("Historical keyword rows",
                      fmt_int(n_kw["n"].iloc[0] if not n_kw.empty else 0))
            st.caption("The detector clears and rewrites `trending_keywords` every cycle; "
                       "a deduplicated snapshot is stored by `updated_at` after every pipeline cycle and page refresh, "
                       "so history accumulates even with the browser closed, "
                       "which is what makes the score-over-time chart possible.")

        st.subheader("Interface Contract")
        contract = [
            ("raw_posts", "Ingestion", "post_id, title, text, source, created_at(ISO UTC), collected_at, url"),
            ("clean_posts", "Processing", "post_id, title_clean, text_clean, tokens (JSON array text), created_at, inserted_at"),
            ("trending_keywords", "Trend", "keyword, burst_score, updated_at (REAL Unix seconds), recent_count, baseline_count — rewritten each cycle"),
            ("sentiment_results", "Sentiment", "post_id, entity, positive/negative/neutral_score, compound_score, sentiment_label, analyzed_at"),
            ("sentiment_trends", "Sentiment", "entity, time_bucket (ISO hour), post_count, avg_compound, *_count, sentiment_shift, updated_at"),
        ]
        st.dataframe(pd.DataFrame(contract, columns=["Table", "Writer", "Columns"]),
                     width="stretch", hide_index=True, height=210)
        st.caption(
            "Stored timestamps remain UTC (ISO 8601 text / REAL Unix seconds) — this is the "
            "group's interface contract and has **not** been changed. Only the dashboard's "
            f"human-readable labels are converted to {config.DISPLAY_TZ_NAME}.")

        mismatch = sorted(set(config.SLIDE_ENTITIES) ^ set(config.TRACKED_ENTITIES))
        st.info(
            f"**Active entities:** `{', '.join(sorted(config.TRACKED_ENTITIES))}` "
            f"(profile `{config.ENTITY_PROFILE}`, the delivered entity set, agreed by the group). "
            f"The slide suggests `{' / '.join(config.SLIDE_ENTITIES)}`; "
            f"differing entities: {', '.join(mismatch) if mismatch else 'none'}. "
            "This is not an error and does not need changing. To switch, run "
            "`ENTITY_PROFILE=slide python run_pipeline.py --offline`. "
            "The pipeline will then remove scores for entities no longer tracked. "
            "`entity_registry.py` is the single source shared by the analyzer, the pipeline and this dashboard.",
            icon="ℹ️")
        st.caption(f"Shift threshold |Δavg_compound| ≥ {config.SHIFT_THRESHOLD}; "
                   "the dashboard only displays it.")

        with st.expander("Interface Fixes"):
            fixes = [
                ("Entity config was duplicated; switching left stale rows", "The delivered entity set is the default; "
                 "entity_registry.py is now the single source, the slide set is an optional profile, and stale rows are pruned on switch",
                 "entity_registry.py / entities.py / run_pipeline.py"),
                ("Stop-word list had only 18 words", "Standard English stop words + HN boilerplate: "
                 f" {len(__import__('text_quality').STOP_WORDS)} words, shared by cleaning, detection and the dashboard",
                 "text_quality.py / cleaner.py / trend.py"),
                ("Unequal 1 h / 24 h windows inflated new words",
                 "Baseline normalised to an hourly rate; BURST_FORMULA=original restores the raw ratio",
                 "trend.py"),
                ("Single-occurrence words dominated the chart", "Minimum frequency ≥2; a short honest list instead of padded noise",
                 "trend.py"),
                ("Raw frequency data was missing", "trending_keywords gained "
                 "recent_count / baseline_count, with automatic ALTER of existing tables",
                 "trend.py / schema.sql"),
                ("get_recent_posts used the machine clock", "Now anchored on MAX(created_at)",
                 "src/processing/store.py"),
                ("Stale entity scores after a profile switch", "Rows for untracked entities are pruned each cycle",
                 "run_pipeline.py"),
                ("History was only recorded while the browser was open", "A snapshot is recorded at the end of every pipeline cycle",
                 "run_pipeline.py / db.py"),
                ("Times were displayed in UTC", f"All labels now show {config.DISPLAY_TZ_SHORT}; "
                 "stored UTC formats are untouched so SQL hour filters still work",
                 "config.py / db.py / dashboard.py"),
            ]
            st.dataframe(pd.DataFrame(fixes, columns=["Issue", "Fix", "Location"]),
                         width="stretch", hide_index=True, height=290)

        if not run_log.empty:
            st.subheader("Pipeline Run Log")
            rl = run_log.copy()
            rl["Time"] = rl["ran_at"].apply(db.iso)
            rl = rl[["Time", "component", "rows_in", "note"]]
            rl.columns = ["Time (HKT)", "Component", "Rows", "Note"]
            st.dataframe(rl, width="stretch", hide_index=True, height=220)

        errs = st.session_state.get("_db_errors", [])
        if errs:
            st.error("Errors while reading the shared DB (usually a missing table or column):\n\n" +
                     "\n\n".join(f"- `{e}`" for e in errs))
        else:
            st.success("All queries match the live schema; no column errors.", icon="✅")

        with st.expander("View Live DDL"):
            ddl = "\n\n".join(
                f"-- {t}\n{db.table_ddl(t)};" for t in db.SHARED_TABLES
                if db.table_ddl(t))
            st.code(ddl or "no tables", language="sql")

        p = db.db_path()
        st.caption(f"DB: `data/app.db`  ·  {p.stat().st_size/1024:.0f} KB  ·  "
                   f"Last write {db.iso(p.stat().st_mtime)}  ·  opened with mode=ro")


render_live()
