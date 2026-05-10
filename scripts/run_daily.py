"""
run_daily.py — Entry point for the AI Companion Signal Intelligence Agent.

Called by GitHub Actions on schedule, or run locally:
    python scripts/run_daily.py

Orchestrates the full pipeline:
    collect → clean → analyze → save report
"""

import sys
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.collect_reddit import collect_posts
from scripts.clean_data import clean_posts
from scripts.analyze_insights import analyze_insights
from scripts.save_report import save_report

import yaml


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "topics.yml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    start_time = time.time()

    # Allow run date override via env var (set by GitHub Actions workflow_dispatch)
    run_date = os.getenv("SIGNAL_AGENT_RUN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Allow dry-run mode (collect + clean only, skip LLM call)
    dry_run = os.getenv("SIGNAL_AGENT_DRY_RUN", "false").lower() == "true"

    print(f"\n{'='*60}")
    print(f"  AI Companion Signal Intelligence Agent")
    print(f"  Run date : {run_date}")
    if dry_run:
        print(f"  Mode     : DRY RUN (LLM analysis skipped)")
    print(f"  Started  : {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"{'='*60}\n")

    # ── 1. Load config ────────────────────────────────────────────
    print("[1/4] Loading config...")
    try:
        config = load_config()
        tracks = config.get("market_coverage_tracks", {})
        active_tracks = [k for k, v in tracks.items() if v.get("active_source") == "reddit"]
        print(f"      Active tracks ({len(active_tracks)}): {', '.join(active_tracks)}")
    except Exception as e:
        print(f"      ERROR loading config: {e}")
        sys.exit(1)

    # ── 2. Collect ────────────────────────────────────────────────
    print("\n[2/4] Collecting posts...")
    try:
        collection_result = collect_posts(config)
        raw_posts = collection_result["posts"]
        track_coverage = collection_result["track_coverage"]
        missing_tracks = collection_result["missing_tracks"]
        print(f"      Collected {len(raw_posts)} posts (raw)")
        if missing_tracks:
            print(f"      Missing tracks this run: {missing_tracks}")
    except Exception as e:
        print(f"      ERROR during collection: {e}")
        traceback.print_exc()
        sys.exit(1)

    if not raw_posts:
        print("      No posts collected. Exiting.")
        sys.exit(0)

    # ── 3. Clean ──────────────────────────────────────────────────
    print("\n[3/4] Cleaning data...")
    try:
        clean_data = clean_posts(raw_posts, config)
        clean_data["track_coverage"] = track_coverage
        clean_data["missing_tracks"] = missing_tracks
        kept = len(clean_data["posts"])
        removed = len(raw_posts) - kept
        print(f"      Kept   : {kept} posts")
        print(f"      Removed: {removed} posts (duplicates, noise, short comments)")
        sample_size = clean_data["sample_size"]
        print(f"      Sample size (posts + comments): {sample_size}")
    except Exception as e:
        print(f"      ERROR during cleaning: {e}")
        traceback.print_exc()
        sys.exit(1)

    if kept == 0:
        print("      All posts were filtered out. Exiting.")
        sys.exit(0)

    # ── 4. Analyze ────────────────────────────────────────────────
    if dry_run:
        print("\n[4/4] Skipping LLM analysis (dry run mode).")
        print("      Pipeline verified: collect → clean completed successfully.")
        print(f"      Sample size ready for analysis: {sample_size}")
        sys.exit(0)

    print("\n[4/4] Analyzing insights (LLM call)...")
    try:
        analysis_result = analyze_insights(clean_data, config, run_date)
        new_count = analysis_result.get("run_summary", {}).get("new_insights", 0)
        print(f"      New insights     : {new_count}")
        print(f"      Updated insights : {analysis_result.get('run_summary', {}).get('updated_insights', 0)}")
        print(f"      Watchlist signals: {analysis_result.get('run_summary', {}).get('watchlist_signals', 0)}")
        proposed = analysis_result.get("run_summary", {}).get("proposed_new_emotion_labels", 0)
        if proposed:
            print(f"      New emotion labels proposed: {proposed}")
    except Exception as e:
        print(f"      ERROR during analysis: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 5. Save report ──────────────────────────────