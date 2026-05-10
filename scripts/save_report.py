"""
save_report.py — Write the daily markdown report and JSON outputs.

Outputs:
  reports/daily/YYYY-MM-DD.md           — Human-readable report
  reports/daily/YYYY-MM-DD_insights.json — Structured insight data
  reports/daily/YYYY-MM-DD_quotes.json   — Selected quotes by insight ID
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


# ─── Priority badge helper ────────────────────────────────────────────────────

PRIORITY_BADGE = {
    "high":   "🔴 HIGH",
    "medium": "🟡 MEDIUM",
    "low":    "⚪ LOW",
}

TRACK_BADGE = {
    "opportunity": "📈 Opportunity",
    "risk":        "⚠️ Risk",
}

MARKET_TRACK_LABEL = {
    "ai_companion_apps":       "💬 AI Companion Apps",
    "physical_ai_companions":  "🤖 Physical AI Companions",
    "wearable_ai_companions":  "⌚ Wearable AI",
    "ai_office_hardware":      "🖥️ AI Office Hardware",
    "ambient_ai_devices":      "🔊 Ambient AI Devices",
    "reference_product_tracking": "📦 Reference Products",
    "emotional_need_adjacent": "💙 Emotional Need Adjacent",
}


def _priority_badge(level: str) -> str:
    return PRIORITY_BADGE.get(level, level.upper())


def _track_badge(track: str) -> str:
    return TRACK_BADGE.get(track, track)


def _market_track_label(track: str) -> str:
    return MARKET_TRACK_LABEL.get(track, track.replace("_", " ").title())


# ─── Markdown section builders ────────────────────────────────────────────────

def _render_insight(insight: dict, index: int) -> str:
    ps = insight.get("priority_score", {})
    confidence = insight.get("confidence", {})
    emotional = insight.get("emotional_signal", {})
    evidence_list = insight.get("evidence", [])

    lines = []
    lines.append(f"### {index}. {insight.get('title', 'Untitled Insight')}")
    lines.append("")
    market_track = insight.get("market_track", "")
    lines.append(
        f"**Type:** {insight.get('insight_type', '—')} &nbsp;|&nbsp; "
        f"**Signal Track:** {_track_badge(insight.get('signal_track', ''))} &nbsp;|&nbsp; "
        f"**Priority:** {_priority_badge(insight.get('priority_level', ''))}"
    )
    if market_track:
        lines.append(f"**Market Track:** {_market_track_label(market_track)}")
    lines.append("")

    # User need
    pain = insight.get("user_pain_or_need", "")
    if pain:
        lines.append(f"**User Need / Pain:** {pain}")
        lines.append("")

    # Product implication
    implication = insight.get("product_implication", "")
    if implication:
        lines.append(f"**Product Implication:** {implication}")
        lines.append("")

    # Emotional signal
    primary_emo = emotional.get("primary_emotion", "")
    secondary_emo = emotional.get("secondary_emotion", "")
    intensity = emotional.get("emotional_intensity", "")
    sentiment = emotional.get("sentiment", "")
    if primary_emo:
        emo_str = f"`{primary_emo}`"
        if secondary_emo:
            emo_str += f" + `{secondary_emo}`"
        lines.append(f"**Emotion:** {emo_str} &nbsp;|&nbsp; Intensity: {intensity}/5 &nbsp;|&nbsp; Sentiment: {sentiment}")
        lines.append("")

    # Priority scores
    lines.append("**Priority Scores:**")
    lines.append("")
    lines.append("| Dimension | Score |")
    lines.append("|---|---|")
    for dim in ["frequency", "emotional_intensity", "strategic_relevance", "actionability", "novelty", "risk_level"]:
        lines.append(f"| {dim.replace('_', ' ').title()} | {ps.get(dim, '—')}/5 |")
    lines.append(f"| **Opportunity Score** | **{ps.get('opportunity_score', '—')}/25** |")
    lines.append(f"| **Risk Score** | **{ps.get('risk_score', '—')}/20** |")
    lines.append("")

    # Confidence
    conf_level = confidence.get("level", "")
    conf_count = confidence.get("evidence_count", "")
    conf_diversity = confidence.get("source_diversity", "")
    contradiction = confidence.get("contradiction_level", "")
    if conf_level:
        lines.append(
            f"**Confidence:** {conf_level.upper()} &nbsp;|&nbsp; "
            f"Evidence: {conf_count} pieces &nbsp;|&nbsp; "
            f"Sources: {conf_diversity} &nbsp;|&nbsp; "
            f"Contradiction: {contradiction}"
        )
        lines.append("")

    # Recommended action
    action = insight.get("recommended_action", "")
    if action:
        lines.append(f"**Recommended Action:** {action}")
        lines.append("")

    # Evidence quotes
    if evidence_list:
        lines.append("**Selected Quotes:**")
        lines.append("")
        for ev in evidence_list:
            quote = ev.get("quote", "")
            source = ev.get("source", "")
            qtype = ev.get("quote_type", "")
            vuln = ev.get("vulnerability_flag", False)
            vuln_tag = " ⚠️ *[vulnerability-flagged source]*" if vuln else ""
            lines.append(f"> *\"{quote}\"*")
            lines.append(f"> — {source} · `{qtype}`{vuln_tag}")
            lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_proposed_labels(labels: list[dict]) -> str:
    if not labels:
        return ""
    lines = ["## 🧠 Newly Proposed Emotion Labels (Auto-Discovered)", ""]
    lines.append(
        "> These labels were not in the baseline. They will be promoted to the baseline "
        "after appearing in 3 separate runs."
    )
    lines.append("")
    for label in labels:
        lines.append(f"### `{label.get('label', '')}`")
        lines.append(f"**Definition:** {label.get('definition', '')}")
        lines.append(f"**Evidence count this run:** {label.get('evidence_count', '')}")
        lines.append(f"**Auto-discovered:** {label.get('auto_discovered', True)}")
        lines.append("")
    return "\n".join(lines)


def build_markdown_report(
    analysis_result: dict,
    clean_data: dict,
    config: dict,
    run_date: str,
) -> str:
    summary = analysis_result.get("run_summary", {})
    insights = analysis_result.get("insights", [])
    proposed_labels = analysis_result.get("newly_proposed_emotion_labels", [])
    methods_used = analysis_result.get("method_used", [])
    stats = clean_data.get("stats", {})
    track_coverage = clean_data.get("track_coverage", {})
    missing_tracks = clean_data.get("missing_tracks", [])

    # Build subreddit list from all active tracks
    tracks_cfg = config.get("market_coverage_tracks", {})
    all_subreddits = []
    for tc in tracks_cfg.values():
        if tc.get("active_source", "").lower() == "reddit":
            all_subreddits.extend(tc.get("subreddits", []))
    subreddits = list(dict.fromkeys(all_subreddits))  # deduplicate preserving order

    # Categorise insights
    new_insights = [i for i in insights if i.get("status") == "new"]
    updated_insights = [i for i in insights if i.get("status") == "updated"]
    watchlist = [i for i in insights if i.get("status") == "watchlist"]
    declining = [i for i in insights if i.get("status") == "declining"]

    lines = []

    # ── Header ─────────────────────────────────────────────────────
    lines.append(f"# AI Companion Signal Intelligence Report — {run_date}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Run metadata ───────────────────────────────────────────────
    lines.append("## 📋 Run Metadata")
    lines.append("")
    lines.append(f"| Field | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Date | {run_date} |")
    lines.append(f"| Subreddits | {', '.join(subreddits)} |")
    lines.append(f"| Posts collected (raw) | {stats.get('raw_posts', '—')} |")
    lines.append(f"| Posts after cleaning | {stats.get('kept_posts', '—')} |")
    lines.append(f"| Comments kept | {stats.get('kept_comments', '—')} |")
    lines.append(f"| Sample size (posts + comments) | {analysis_result.get('sample_size', '—')} |")
    lines.append(f"| Analysis methods | {', '.join(methods_used)} |")
    lines.append(f"| New insights | {summary.get('new_insights', 0)} |")
    lines.append(f"| Updated insights | {summary.get('updated_insights', 0)} |")
    lines.append(f"| Watchlist signals | {summary.get('watchlist_signals', 0)} |")
    lines.append(f"| New emotion labels proposed | {summary.get('proposed_new_emotion_labels', 0)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Track coverage summary ─────────────────────────────────────
    lines.append("## 📡 Track Coverage This Run")
    lines.append("")
    lines.append("| Market Track | Posts Collected | Status |")
    lines.append("|---|---|---|")
    for track_name, count in track_coverage.items():
        label = _market_track_label(track_name)
        status = "✅ Covered" if count > 0 else "⚠️ No data"
        lines.append(f"| {label} | {count} | {status} |")
    lines.append("")
    if missing_tracks:
        lines.append(
            f"> **Note:** {len(missing_tracks)} track(s) had no coverage this run. "
            "See [Missing Coverage](#-missing-coverage) section below."
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Source limitation ──────────────────────────────────────────
    lines.append("## ⚠️ Source Limitation")
    lines.append("")
    lines.append(
        "> Reddit signals are **directional qualitative signals**, not market-wide demand proof. "
        "All insights require validation with additional user research before driving major product decisions. "
        "Content from r/lonely and similar communities is treated as emotional need signal only — "
        "not as an acquisition or conversion target."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── New signals ────────────────────────────────────────────────
    lines.append("## 🆕 New Signals Today")
    lines.append("")
    if new_insights:
        for i, insight in enumerate(new_insights, 1):
            lines.append(_render_insight(insight, i))
    else:
        lines.append("*No new signals identified today. This is a valid outcome — do not fabricate insights.*")
        lines.append("")

    # ── Updated signals ────────────────────────────────────────────
    if updated_insights:
        lines.append("## 🔄 Updated Signals")
        lines.append("")
        for i, insight in enumerate(updated_insights, 1):
            lines.append(_render_insight(insight, i))

    # ── Persistent watchlist ───────────────────────────────────────
    if watchlist:
        lines.append("## 👁️ Persistent Watchlist")
        lines.append("")
        lines.append("*Signals being monitored but not yet actionable.*")
        lines.append("")
        for i, insight in enumerate(watchlist, 1):
            lines.append(_render_insight(insight, i))

    # ── Declining signals ──────────────────────────────────────────
    if declining:
        lines.append("## 📉 Declining Signals")
        lines.append("")
        for i, insight in enumerate(declining, 1):
            lines.append(_render_insight(insight, i))

    # ── Missing coverage ───────────────────────────────────────────
    lines.append("## 🔍 Missing Coverage")
    lines.append("")
    if missing_tracks:
        lines.append(
            "The following market tracks had **zero posts collected** this run. "
            "This may indicate low community activity, keyword gaps, or subreddit changes. "
            "Consider pulling additional data for these tracks next run."
        )
        lines.append("")
        for track_name in missing_tracks:
            label = _market_track_label(track_name)
            track_cfg = tracks_cfg.get(track_name, {})
            subs = track_cfg.get("subreddits", [])
            lines.append(f"- **{label}** — monitored subreddits: {', '.join(subs)}")
        lines.append("")
        lines.append(
            "> ⚠️ Do not over-index on Replika and CharacterAI. "
            "Missing tracks represent real gaps in signal coverage."
        )
    else:
        lines.append("✅ All market tracks had at least one post collected this run.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Noise log ─────────────────────────────────────────────────
    lines.append("## 🗑️ Noise Log")
    lines.append("")
    lines.append(f"- Duplicate posts removed: {stats.get('removed_duplicates', 0)}")
    lines.append(f"- Deleted/removed content filtered: {stats.get('removed_deleted', 0)}")
    min_len = config.get("collection", {}).get("minimum_comment_length", 120)
    lines.append(f"- Short comments filtered (< {min_len} chars): {stats.get('removed_short_comments', 0)}")
    lines.append(f"- Bot content filtered: {stats.get('removed_bot_comments', 0)}")
    lines.append(f"- Low-signal reactions filtered: {stats.get('removed_low_signal_comments', 0)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Auto-discovered emotion labels ─────────────────────────────
    if proposed_labels:
        lines.append(_render_proposed_labels(proposed_labels))
        lines.append("---")
        lines.append("")

    # ── Appendix ──────────────────────────────────────────────────
    lines.append("## 📎 Appendix")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Version:** AI Companion Signal Intelligence Agent v0.1.0")
    lines.append("")

    return "\n".join(lines)


# ─── Main entry point ─────────────────────────────────────────────────────────

def save_report(
    analysis_result: dict,
    clean_data: dict,
    config: dict,
    run_date: str,
) -> dict[str, str]:
    """
    Write markdown report and JSON outputs to reports/daily/.

    Returns:
        Dict with paths: {"markdown": ..., "json": ..., "quotes": ...}
    """
    report_cfg = config.get("report", {})
    output_dir = Path(__file__).parent.parent / report_cfg.get("output_dir", "reports/daily")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_paths: dict[str, str] = {}

    # ── Markdown report ───────────────────────────────────────────
    md_content = build_markdown_report(analysis_result, clean_data, config, run_date)
    md_path = output_dir / f"{run_date}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    output_paths["markdown"] = str(md_path)

    # ── Insights JSON ─────────────────────────────────────────────
    if report_cfg.get("json_output", True):
        json_path = output_dir / f"{run_date}_insights.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, indent=2, ensure_ascii=False)
        output_paths["json"] = str(json_path)

    # ── Selected quotes JSON ──────────────────────────────────────
    if report_cfg.get("quotes_output", True):
        quotes: dict[str, list] = {}
        for insight in analysis_result.get("insights", []):
            insight_id = insight.get("id", "unknown")
            quotes[insight_id] = insight.get("evidence", [])

        quotes_path = output_dir / f"{run_date}_quotes.json"
        with open(quotes_path, "w", encoding="utf-8") as f:
            json.dump(quotes, f, indent=2, ensure_ascii=False)
        output_paths["quotes"] = str(quotes_path)

    return output_paths
