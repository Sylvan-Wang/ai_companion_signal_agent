"""
collect_reddit.py — Track-aware Reddit data collection with public JSON fallback.

Each collected post is tagged with:
  - market_track   : which of the 7 coverage tracks matched
  - matched_keyword: the specific keyword that triggered inclusion
  - source         : always "reddit" for MVP
  - subreddit      : the subreddit it came from
  - vulnerability_flag: True for emotional_need_adjacent content

Mode selection (auto-detected):
  Phase 3a — PRAW (REDDIT_CLIENT_ID etc. set):  live data via OAuth
  Phase 3b — Public JSON (no credentials):       live data via reddit.com/*.json
  Sample    — SIGNAL_AGENT_USE_SAMPLE=true:      loads sample_raw_posts.json

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
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


# ─── Mode detection ──────────────────────────────────────────────────────────

def _is_praw_mode() -> bool:
    """PRAW mode: all three Reddit OAuth credentials are present."""
    return all([
        os.getenv("REDDIT_CLIENT_ID"),
        os.getenv("REDDIT_CLIENT_SECRET"),
        os.getenv("REDDIT_USER_AGENT"),
    ])

def _is_sample_mode() -> bool:
    """Force sample data via env var (for testing)."""
    return os.getenv("SIGNAL_AGENT_USE_SAMPLE", "").lower() == "true"

# Public JSON mode is the default fallback — no credentials needed.


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


# ─── Public JSON collection (no credentials needed) ──────────────────────────

_PUBLIC_HEADERS = {
    "User-Agent": "ai_companion_signal_agent/0.1 (research; github.com/Sylvan-Wang/ai_companion_signal_agent)"
}
_REQUEST_DELAY = 1.5  # seconds between requests — be respectful


def _public_fetch(url: str, params: dict | None = None) -> dict | None:
    """GET a reddit public JSON endpoint. Returns parsed JSON or None on error."""
    try:
        resp = requests.get(url, headers=_PUBLIC_HEADERS, params=params, timeout=15)
        if resp.status_code == 429:
            print(f"        Rate-limited by Reddit, waiting 10s...")
            time.sleep(10)
            resp = requests.get(url, headers=_PUBLIC_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"        WARNING: request failed for {url}: {e}")
        return None


def _collect_public_json(config: dict) -> dict[str, Any]:
    """
    Collect posts using Reddit's public JSON API (no OAuth needed).
    Hits: https://www.reddit.com/r/{sub}/new.json and /hot.json
    """
    collection_cfg = config.get("collection", {})
    post_limit = min(collection_cfg.get("post_limit_per_run", 60), 100)
    comment_limit = collection_cfg.get("comment_limit_per_post", 10)
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

        print(f"        [{track_name}] Fetching from {subreddits}...")

        for subreddit_name in subreddits:
            # Fetch from both /new and /hot to maximise coverage
            for sort in ("new", "hot"):
                url = f"https://www.reddit.com/r/{subreddit_name}/{sort}.json"
                data = _public_fetch(url, params={"limit": post_limit, "raw_json": 1})
                time.sleep(_REQUEST_DELAY)

                if not data:
                    continue

                children = data.get("data", {}).get("children", [])
                for child in children:
                    p = child.get("data", {})
                    post_id = p.get("id", "")

                    if post_id in seen_ids or p.get("stickied"):
                        continue

                    combined_text = f"{p.get('title', '')} {p.get('selftext', '')}"
                    matched_kw = _find_matched_keyword(combined_text, keywords)
                    if not matched_kw:
                        continue
                    if _contains_excluded(combined_text, exclusions):
                        continue

                    created_utc = p.get("created_utc", 0)
                    created_str = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
                    if not _within_lookback(created_str, lookback_hours):
                        continue

                    # Fetch comments via public API
                    comments = []
                    comments_url = f"https://www.reddit.com/r/{subreddit_name}/comments/{post_id}.json"
                    cdata = _public_fetch(comments_url, params={"limit": comment_limit, "depth": 1, "raw_json": 1})
                    time.sleep(_REQUEST_DELAY)

                    if cdata and len(cdata) >= 2:
                        comment_children = cdata[1].get("data", {}).get("children", [])
                        for cc in comment_children[:comment_limit]:
                            cd = cc.get("data", {})
                            body = cd.get("body", "")
                            if not body or body in ("[deleted]", "[removed]"):
                                continue
                            comments.append({
                                "comment_id": cd.get("id", ""),
                                "comment_body": body,
                                "comment_score": cd.get("score", 0),
                                "comment_created_utc": datetime.fromtimestamp(
                                    cd.get("created_utc", 0), tz=timezone.utc
                                ).isoformat(),
                                "vulnerability_flag": vulnerability_flag,
                            })

                    all_posts.append({
                        "post_id": post_id,
                        "subreddit": subreddit_name,
                        "market_track": track_name,
                        "matched_keyword": matched_kw,
                        "source": "reddit_public",
                        "post_title": p.get("title", ""),
                        "post_body": p.get("selftext", ""),
                        "post_score": p.get("score", 0),
                        "post_num_comments": p.get("num_comments", 0),
                        "post_created_utc": created_str,
                        "post_url": f"https://reddit.com{p.get('permalink', '')}",
                        "vulnerability_flag": vulnerability_flag,
                        "comments": comments,
                    })
                    seen_ids.add(post_id)
                    track_coverage[track_name] = track_coverage.get(track_name, 0) + 1

    missing_tracks = [t for t, count in track_coverage.items() if count == 0]
    return {
        "posts": all_posts,
        "track_coverage": track_coverage,
        "missing_tracks": missing_tracks,
    }


# ─── PRAW Live Reddit collection ─────────────────────────────────────────────

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


# ─── Public entry point ───────────────────────────────────────────────────────

def collect_posts(config: dict) -> dict:
    """
    Main collection function. Auto-selects collection mode:

      1. PRAW mode      — REDDIT_CLIENT_ID + SECRET + USER_AGENT all set
      2. Public JSON    — default; uses reddit.com/*.json (no credentials)
      3. Sample mode    — SIGNAL_AGENT_USE_SAMPLE=true (local testing only)

    Returns:
        {
            "posts": [...],
            "track_coverage": {track_name: post_count, ...},
            "missing_tracks": [track_names_with_zero_posts]
        }
    """
    if _is_sample_mode():
        print("      [sample mode] SIGNAL_AGENT_USE_SAMPLE=true -- using sample data")
        return _load_sample_data(config)

    if _is_praw_mode():
        print("      [praw mode] Reddit OAuth credentials detected -- fetching via PRAW")
        return _collect_live(config)

    print("      [public JSON mode] No credentials -- fetching via reddit.com public API")
    print("      (No API key required. Rate-limited to ~1 req/1.5s.)")
    return _collect_public_json(config)
