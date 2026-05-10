"""
collect_reddit.py — Track-aware Reddit data collection with sample data fallback.

Each collected post is tagged with:
  - market_track   : which of the 7 coverage tracks matched
  - matched_keyword: the specific keyword that triggered inclusion
  - source         : always "reddit" for MVP
  - subreddit      : the subreddit it came from
  - vulnerability_flag: True for emotional_need_adjacent content

Phase 1 (no Reddit credentials): loads data/raw/sample_raw_posts.json
Phase 3 (credentials in env):    fetches live data via PRAW per-track

Returns:
  {
    "posts": [...],              # All collected posts with track tags
    "track_coverage": {          # Which tracks had data
        "ai_companion_apps": 3,
        "physical_ai_companions": 1,
        ...
    },
    "missing_tracks": [...]      # Track names with zero posts
  }
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


# ─── Credential check ────────────────────────────────────────────────────────

def _is_live_mode() -> bool:
    return all([
        os.getenv("REDDIT_CLIENT_ID"),
        os.getenv("REDDIT_CLIENT_SECRET"),
        os.getenv("REDDIT_USER_AGENT"),
    ])


# ─── Config helpers ───────────────────────────────────────────────────────────

def _get_active_tracks(config: dict) -> dict[str, dict]:
    """Return only tracks with active_source == 'reddit'."""
    tracks = config.get("market_coverage_tracks", {})
    return {
        name: track
        for name, track in tracks.items()
        if track.get("active_source", "").lower() == "reddit"
    }


def _get_exclusion_keywords(config: dict) -> list[str]:
    return config.get("exclusion_keywords", [])


def _is_vulnerable_track(track_name: str, track_cfg: dict) -> bool:
    return track_cfg.get("vulnerability_flag", False)


# ─── Filtering helpers ────────────────────────────────────────────────────────

def _contains_excluded(text: str, exclusions: list[str]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in exclusions)


def _find_matched_keyword(text: str, keywords: list[str]) -> str | None:
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return kw
    return None


def _within_lookback(created_utc_str: str, lookback_hours: int) -> bool:
    try:
        created = datetime.fromisoformat(created_utc_str.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        return created >= cutoff
    except Exception:
        return True


# ─── Sample data loader ───────────────────────────────────────────────────────

def _load_sample_data(config: dict) -> dict[str, Any]:
    """
    Load sample_raw_posts.json and tag each post with market_track metadata.
    Uses subreddit name to assign the correct track.
    """
    sample_path = Path(__file__).parent.parent / "data" / "raw" / "sample_raw_posts.json"
    if not sample_path.exists():
        raise FileNotFoundError(f"Sample data not found at {sample_path}")

    with open(sample_path, "r", encoding="utf-8") as f:
        raw_posts = json.load(f)

    # Build subreddit → track map
    sub_to_track: dict[str, str] = {}
    tracks = config.get("market_coverage_tracks", {})
    for track_name, track_cfg in tracks.items():
        for sub in track_cfg.get("subreddits", []):
            sub_to_track[sub.lower()] = track_name

    exclusions = _get_exclusion_keywords(config)

    tagged_posts = []
    track_coverage: dict[str, int] = {name: 0 for name in tracks}

    for post in raw_posts:
        subreddit = post.get("subreddit", "")
        track_name = sub_to_track.get(subreddit.lower(), "ai_companion_apps")
        track_cfg = tracks.get(track_name, {})
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)

        # Find matched keyword from track's keyword list
        search_text = f"{post.get('post_title', '')} {post.get('post_body', '')}"
        keywords = track_cfg.get("keywords", [])
        matched_kw = _find_matched_keyword(search_text, keywords) or post.get("keyword_matched", "")

        # Apply exclusions
        if _contains_excluded(search_text, exclusions):
            continue

        # Tag post
        tagged_post = {
            **post,
            "market_track": track_name,
            "matched_keyword": matched_kw,
            "source": "reddit",
            "vulnerability_flag": vulnerability_flag,
        }

        # Tag comments too
        tagged_comments = []
        for comment in post.get("comments", []):
            tagged_comments.append({
                **comment,
                "vulnerability_flag": vulnerability_flag,
            })
        tagged_post["comments"] = tagged_comments

        tagged_posts.append(tagged_post)
        track_coverage[track_name] = track_coverage.get(track_name, 0) + 1

    missing_tracks = [t for t, count in track_coverage.items() if count == 0]

    print(f"      [sample mode] Loaded {len(tagged_posts)} posts from {sample_path.name}")
    print(f"      Track coverage: { {k: v for k, v in track_coverage.items() if v > 0} }")
    if missing_tracks:
        print(f"      Missing tracks: {missing_tracks}")

    return {
        "posts": tagged_posts,
        "track_coverage": track_coverage,
        "missing_tracks": missing_tracks,
    }


# ─── Live Reddit collection ───────────────────────────────────────────────────

def _collect_live(config: dict) -> dict[str, Any]:
    """Collect posts from Reddit via PRAW, organised by track."""
    try:
        import praw
    except ImportError:
        raise ImportError("praw is not installed. Run: pip install praw")

    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT"),
    )

    collection_cfg = config.get("collection", {})
    post_limit = collection_cfg.get("post_limit_per_run", 60)
    comment_limit = collection_cfg.get("comment_limit_per_post", 20)
    lookback_window = collection_cfg.get("lookback_window", "24h")
    lookback_hours = int(re.sub(r"[^\d]", "", str(lookback_window))) if lookback_window else 24

    exclusions = _get_exclusion_keywords(config)
    active_tracks = _get_active_tracks(config)


    all_posts: list[dict] = []
    seen_ids: set[str] = set()
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}

    for track_name, track_cfg in active_tracks.items():
        subreddits = track_cfg.get("subreddits", [])
        keywords = track_cfg.get("keywords", [])
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)

        print(f"        [{track_name}] Collecting from {subreddits}...")

        for subreddit_name in subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                for post in subreddit.new(limit=post_limit):
                    if post.id in seen_ids or post.stickied:
                        continue

                    combined_text = f"{post.title} {post.selftext}"
                    matched_kw = _find_matched_keyword(combined_text, keywords)
                    if not matched_kw:
                        continue
                    if _contains_excluded(combined_text, exclusions):
                        continue

                    created_utc_str = datetime.fromtimestamp(
                        post.created_utc, tz=timezone.utc
                    ).isoformat()
                    if not _within_lookback(created_utc_str, lookback_hours):
                        continue

                    post.comments.replace_more(limit=0)
                    comments = []
                    for comment in post.comments[:comment_limit]:
                        if not hasattr(comment, "body"):
                            continue
                        if comment.body in ("[deleted]", "[removed]"):
                            continue
                        comments.append({
                            "comment_id": comment.id,
                            "comment_body": comment.body,
                            "comment_score": comment.score,
                            "comment_created_utc": datetime.fromtimestamp(
                                comment.created_utc, tz=timezone.utc
                            ).isoformat(),
                            "vulnerability_flag": vulnerability_flag,
                        })

                    all_posts.append({
                        "post_id": post.id,
                        "subreddit": subreddit_name,
                        "market_track": track_name,
                        "matched_keyword": matched_kw,
                        "source": "reddit",
                        "post_title": post.title,
                        "post_body": post.selftext,
                        "post_score": post.score,
                        "post_num_comments": post.num_comments,
                        "post_created_utc": created_utc_str,
                        "post_url": f"https://reddit.com{post.permalink}",
                        "vulnerability_flag": vulnerability_flag,
                        "comments": comments,
                    })
                    seen_ids.add(post.id)
                    track_coverage[track_name] = track_coverage.get(track_name, 0) + 1

            except Exception as e:
                print(f"        WARNING: Failed to fetch r/{subreddit_name}: {e}")
                continue

    missing_tracks = [t for t, count in track_coverage.items() if count == 0]
    return {
        "posts": all_posts,
        "track_coverage": track_coverage,
        "missing_tracks": missing_tracks,
    }


# Public entry point
def collect_posts(config: dict) -> dict:
    """
    Main collection function. Auto-selects live or sample mode.

    Returns:
        {
            "posts": [...],
            "track_coverage": {track_name: post_count, ...},
            "missing_tracks": [track_names_with_zero_posts]
        }
    """
    if _is_live_mode():
        print("      [live mode] Reddit credentials detected -- fetching live data")
        return _collect_live(config)
    else:
        print("      [sample mode] No Reddit credentials -- using sample data")
        print("      To use live data: set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT")
        return _load_sample_data(config)
