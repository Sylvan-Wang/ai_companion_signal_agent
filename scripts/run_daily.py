"""
run_daily.py — Entry point for the AI Companion Signal Intelligence Agent.

Called by GitHub Actions on schedule, or run locally:
    python scripts/run_daily.py

Pipeline (staging mode):
    Every run  : collect → clean → save to data/staging/
    Report day : load + merge all staged data → analyze → save report → send email

Report cadence is controlled by config/topics.yml → report_schedule.report_every_days
(default: 3 — analyze every 3 collect cycles).
"""

import sys
import os
import json
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from project root (local runs)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on environment variables (GitHub Actions)

from scripts.collect_reddit import collect_posts
from scripts.clean_data import clean_posts
from scripts.analyze_insights import analyze_insights
from scripts.save_report import save_report
from scripts.send_email import send_report_email

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
STAGING_DIR  = PROJECT_ROOT / "data" / "staging"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "topics.yml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Staging helpers ───────────────────────────────────────────────────────────

def save_staged(clean_data: dict, run_date: str) -> Path:
    """Save today's cleaned posts to data/staging/YYYY-MM-DD_staged.json."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{run_date}_staged.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, ensure_ascii=False, indent=2)
    return path


def load_staged(lookback_days: int) -> dict:
    """
    Load and merge staged data from the last `lookback_days` days.
    Deduplicates by post_id. Merges track_coverage and missing_tracks.
    """
    merged_posts: list[dict] = []
    seen_ids: set[str] = set()
    merged_coverage: dict[str, int] = {}
    missing_set: set[str] = set()
    files_loaded: list[str] = []

    today = datetime.now(timezone.utc).date()
    for offset in range(lookback_days):
        day = today - timedelta(days=offset)
        path = STAGING_DIR / f"{day.isoformat()}_staged.json"
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            files_loaded.append(path.name)
            for post in data.get("posts", []):
                pid = post.get("post_id") or post.get("id") or post.get("url", "")
                if pid and pid in seen_ids:
                    continue
                if pid:
                    seen_ids.add(pid)
                merged_posts.append(post)
            for track, count in data.get("track_coverage", {}).items():
                merged_coverage[track] = merged_coverage.get(track, 0) + count
            for t in data.get("missing_tracks", []):
                missing_set.add(t)
        except Exception as e:
            print(f"      WARNING: could not load staging file {path.name}: {e}")

    # Tracks that were always missing (missing in ALL days)
    missing_tracks = [t for t in missing_set if merged_coverage.get(t, 0) == 0]

    print(f"      Staging files merged: {files_loaded}")
    print(f"      Total unique posts   : {len(merged_posts)}")

    return {
        "posts":          merged_posts,
        "sample_size":    len(merged_posts),
        "track_coverage": merged_coverage,
        "missing_tracks": missing_tracks,
    }


def is_report_day(config: dict, run_date: str) -> bool:
    """
    Return True if today is an analysis+report day.

    Logic (from config report_schedule):
      - report_every_days: N  → report on days where day_number % N == 0
      - force_report_today env var overrides to True
    """
    # Env var override (for manual triggers / testing)
    if os.getenv("SIGNAL_AGENT_FORCE_REPORT", "").lower() == "true":
        return True

    schedule = config.get("report_schedule", {})
    every_n = int(schedule.get("report_every_days", 3))
    if every_n <= 1:
        return True  # report every day (original behaviour)

    # Use a fixed epoch so the cycle is deterministic regardless of first run date
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        today = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        today = datetime.now(timezone.utc)

    day_number = (today - epoch).days
    return day_number % every_n == 0


def count_staged_days(lookback_days: int) -> int:
    """Count how many staging files exist within the lookback window."""
    today = datetime.now(timezone.utc).date()
    count = 0
    for offset in range(lookback_days):
        day = today - timedelta(days=offset)
        if (STAGING_DIR / f"{day.isoformat()}_staged.json").exists():
            count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()

    run_date = os.getenv("SIGNAL_AGENT_RUN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dry_run  = os.getenv("SIGNAL_AGENT_DRY_RUN", "false").lower() == "true"

    print(f"\n{'='*60}")
    print(f"  AI Companion Signal Intelligence Agent")
    print(f"  Run date : {run_date}")
    if dry_run:
        print(f"  Mode     : DRY RUN (LLM analysis skipped)")
    print(f"  Started  : {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"{'='*60}\n")

    # ── 1. Load config ─────────────────────────────────────────────
    print("[1/5] Loading config...")
    try:
        config = load_config()
        tracks = config.get("market_coverage_tracks", {})
        active_tracks = [k for k, v in tracks.items() if v.get("active_source") == "reddit"]
        schedule = config.get("report_schedule", {})
        every_n  = int(schedule.get("report_every_days", 3))
        lookback = int(schedule.get("staging_lookback_days", every_n))
        print(f"      Active tracks ({len(active_tracks)}): {', '.join(active_tracks)}")
        print(f"      Report cadence: every {every_n} days | lookback: {lookback} days")
    except Exception as e:
        print(f"      ERROR loading config: {e}")
        sys.exit(1)

    # ── 2. Collect ─────────────────────────────────────────────────
    print("\n[2/5] Collecting posts...")
    try:
        collection_result = collect_posts(config)
        raw_posts      = collection_result["posts"]
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

    # ── 3. Clean ───────────────────────────────────────────────────
    print("\n[3/5] Cleaning data...")
    try:
        clean_data = clean_posts(raw_posts, config)
        clean_data["track_coverage"] = track_coverage
        clean_data["missing_tracks"] = missing_tracks
        kept    = len(clean_data["posts"])
        removed = len(raw_posts) - kept
        print(f"      Kept   : {kept} posts")
        print(f"      Removed: {removed} posts (duplicates, noise, short comments)")
        print(f"      Sample size (posts + comments): {clean_data['sample_size']}")
    except Exception as e:
        print(f"      ERROR during cleaning: {e}")
        traceback.print_exc()
        sys.exit(1)

    if kept == 0:
        print("      All posts were filtered out. Exiting.")
        sys.exit(0)

    # ── 4. Stage today's data ──────────────────────────────────────
    print("\n[4/5] Saving to staging...")
    try:
        staged_path = save_staged(clean_data, run_date)
        staged_count = count_staged_days(lookback)
        print(f"      Staged : {staged_path.name}")
        print(f"      Days in staging pool: {staged_count} / {lookback}")
    except Exception as e:
        print(f"      ERROR saving staged data: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 5. Decide: analyze today? ──────────────────────────────────
    report_today = is_report_day(config, run_date)

    if dry_run:
        print(f"\n[5/5] Dry run — skipping analysis.")
        print(f"      Staging pool ready: {staged_count} day(s) of data.")
        _finish(start_time)
        return

    if not report_today:
        print(f"\n[5/5] Collection-only day (report every {every_n} days).")
        print(f"      Staging pool now has {staged_count} day(s) of data.")
        print(f"      Next report day: check config report_schedule.")
        _finish(start_time)
        return

    print(f"\n[5/5] Report day! Merging {lookback} days of staged data → analyze → email...")

    # ── 5a. Load + merge staged data ──────────────────────────────
    try:
        merged_data = load_staged(lookback)
        if not merged_data["posts"]:
            print("      No staged posts found — falling back to today's data only.")
            merged_data = clean_data
    except Exception as e:
        print(f"      WARNING: staging merge failed ({e}) — using today's data only.")
        merged_data = clean_data

    # ── 5b. Analyze ────────────────────────────────────────────────
    print("\n      Analyzing insights (LLM call)...")
    try:
        analysis_result = analyze_insights(merged_data, config, run_date)
        summary = analysis_result.get("run_summary", {})
        print(f"      New insights     : {summary.get('new_insights', 0)}")
        print(f"      Updated insights : {summary.get('updated_insights', 0)}")
        print(f"      Watchlist signals: {summary.get('watchlist_signals', 0)}")
        if summary.get("proposed_new_emotion_labels"):
            print(f"      New emotion labels proposed: {summary['proposed_new_emotion_labels']}")
    except Exception as e:
        print(f"      ERROR during analysis: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 5c. Save report ────────────────────────────────────────────
    print("\n      Saving report...")
    try:
        output_paths = save_report(analysis_result, merged_data, config, run_date)
        print(f"      Markdown : {output_paths['markdown']}")
        if output_paths.get("json"):
            print(f"      JSON     : {output_paths['json']}")
        if output_paths.get("quotes"):
            print(f"      Quotes   : {output_paths['quotes']}")
    except Exception as e:
        print(f"      ERROR saving report: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 5d. Send email ─────────────────────────────────────────────
    print("\n      Sending email report...")
    try:
        send_report_email(output_paths["markdown"], run_date)
    except Exception as e:
        print(f"      Email error (non-fatal): {e}")

    _finish(start_time)


def _finish(start_time: float):
    elapsed = round(time.time() - start_time, 1)
    print(f"\n{'='*60}")
    print(f"  Run complete in {elapsed}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
