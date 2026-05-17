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


# ─── Arctic Shift + RSS + Public JSON (no credentials needed) ────────────────
# Priority order:
#   1. Arctic Shift — third-party Reddit archive; not subject to Reddit IP blocks
#   2. Reddit RSS   — less aggressively blocked than JSON on cloud runner IPs
#   3. Reddit JSON  — fallback for local runs where RSS is also unavailable

_ARCTIC_HEADERS = {
    "User-Agent": "ai_companion_signal_agent/0.1 (research)"
}
_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; feedparser/6.0; +https://github.com/Sylvan-Wang/ai_companion_signal_agent)"
}
_JSON_HEADERS = {
    "User-Agent": "ai_companion_signal_agent/0.1 (research)"
}
_REQUEST_DELAY = 1.5  # seconds between requests


def _arctic_shift_fetch(subreddit_name: str, limit: int = 100, after_utc: float | None = None) -> list[dict]:
    """
    Fetch posts from Arctic Shift API (third-party Reddit archive).
    https://arctic-shift.photon-reddit.com
    Not subject to Reddit's IP-level blocks on Azure/cloud runners.

    Returns list of dicts with keys: id, title, selftext, score, created_utc, permalink.
    """
    url = "https://arctic-shift.photon-reddit.com/api/posts/search"
    params: dict = {
        "subreddit": subreddit_name,
        "limit": min(limit, 100),
        "sort": "desc",          # newest first
        "sort_by": "created_utc",
    }
    if after_utc is not None:
        params["after"] = str(int(after_utc))

    try:
        resp = requests.get(url, params=params, headers=_ARCTIC_HEADERS, timeout=20)
        if resp.status_code == 429:
            print(f"        WARNING: Arctic Shift rate-limited for r/{subreddit_name}, retrying in 10s...")
            time.sleep(10)
            resp = requests.get(url, params=params, headers=_ARCTIC_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"        WARNING: Arctic Shift returned {resp.status_code} for r/{subreddit_name}")
            return []
        data = resp.json()
        raw = data.get("data", [])
        posts = []
        for p in raw:
            # created_utc may be int, float, or ISO string
            raw_ts = p.get("created_utc", 0)
            try:
                ts = float(raw_ts)
            except (TypeError, ValueError):
                try:
                    ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0.0
            permalink = p.get("permalink", "") or f"/r/{subreddit_name}/comments/{p.get('id', '')}/"
            posts.append({
                "id":          p.get("id", ""),
                "title":       p.get("title", ""),
                "selftext":    p.get("selftext", "") or p.get("body", ""),
                "score":       p.get("score", 0),
                "created_utc": ts,
                "permalink":   permalink,
            })
        return posts
    except Exception as e:
        print(f"        WARNING: Arctic Shift fetch failed for r/{subreddit_name}: {e}")
        return []


def _rss_fetch(subreddit_name: str, sort: str = "new", limit: int = 25) -> list[dict]:
    """
    Fetch posts via Reddit RSS feed.
    Returns list of dicts with keys: id, title, selftext, score, created_utc, permalink.
    """
    try:
        import feedparser
    except ImportError:
        return []

    url = f"https://www.reddit.com/r/{subreddit_name}/{sort}.rss"
    try:
        feed = feedparser.parse(url, request_headers=_RSS_HEADERS)
        if feed.bozo and not feed.entries:
            return []
        posts = []
        for entry in feed.entries[:limit]:
            # RSS entries have published_parsed for timestamp
            import calendar
            try:
                ts = calendar.timegm(entry.get("published_parsed") or entry.get("updated_parsed") or time.gmtime())
            except Exception:
                ts = time.time()
            # Extract post id from the entry id/link (format: t3_XXXXX)
            post_id = entry.get("id", "").split("_")[-1] or entry.get("link", "").split("/")[-2]
            # Content is in summary or content
            content = entry.get("summary", "")
            posts.append({
                "id":          post_id,
                "title":       entry.get("title", ""),
                "selftext":    content,
                "score":       0,   # RSS doesn't expose score
                "created_utc": ts,
                "permalink":   entry.get("link", ""),
            })
        return posts
    except Exception as e:
        print(f"        WARNING: RSS fetch failed for r/{subreddit_name}/{sort}: {e}")
        return []


def _json_fetch(subreddit_name: str, sort: str = "new", limit: int = 25) -> list[dict]:
    """Fallback: fetch posts via public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit_name}/{sort}.json"
    try:
        resp = requests.get(url, headers=_JSON_HEADERS,
                            params={"limit": limit, "raw_json": 1}, timeout=15)
        if resp.status_code in (403, 429):
            return []
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
        posts = []
        for child in children:
            p = child.get("data", {})
            posts.append({
                "id":          p.get("id", ""),
                "title":       p.get("title", ""),
                "selftext":    p.get("selftext", ""),
                "score":       p.get("score", 0),
                "created_utc": p.get("created_utc", 0),
                "permalink":   p.get("permalink", ""),
            })
        return posts
    except Exception:
        return []


def _fetch_posts(subreddit_name: str, sort: str = "new", limit: int = 100,
                 after_utc: float | None = None) -> list[dict]:
    """
    Try Arctic Shift (primary), then RSS, then Reddit JSON as last resort.
    Arctic Shift is a third-party archive not subject to Reddit's IP blocks.
    """
    posts = _arctic_shift_fetch(subreddit_name, limit=limit, after_utc=after_utc)
    if posts:
        return posts
    print(f"        Arctic Shift returned 0 posts for r/{subreddit_name}, trying RSS...")
    posts = _rss_fetch(subreddit_name, sort, min(limit, 25))
    if posts:
        return posts
    print(f"        RSS also empty for r/{subreddit_name}, trying Reddit JSON...")
    return _json_fetch(subreddit_name, sort, min(limit, 25))


def _collect_public_json(config: dict) -> dict[str, Any]:
    """
    Collect posts via Arctic Shift API (primary) → RSS → JSON fallback.
    Arctic Shift is a third-party Reddit archive not subject to cloud-runner IP blocks.
    """
    collection_cfg = config.get("collection", {})
    post_limit = min(collection_cfg.get("post_limit_per_run", 60), 100)
    lookback_window = collection_cfg.get("lookback_window", "24h")
    lookback_hours = int(re.sub(r"[^\d]", "", str(lookback_window))) if lookback_window else 24

    # Compute after_utc for Arctic Shift native lookback filtering
    after_utc = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp()

    exclusions = _get_exclusion_keywords(config)
    active_tracks = _get_active_tracks(config)

    all_posts: list[dict] = []
    seen_ids: set[str] = set()
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}

    for track_name, track_cfg in active_tracks.items():
        subreddits = track_cfg.get("subreddits", [])
        keywords = track_cfg.get("keywords", [])
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)

        print(f"        [{track_name}] Fetching from {subreddits} (via Arctic Shift)...")

        for subreddit_name in subreddits:
            for sort in ("new",):  # Arctic Shift sorts by created_utc desc; one pass is enough
                raw_posts = _fetch_posts(subreddit_name, sort=sort, limit=post_limit, after_utc=after_utc)
                time.sleep(_REQUEST_DELAY)

                for p in raw_posts:
                    post_id = p.get("id", "")
                    if not post_id or post_id in seen_ids:
                        continue

                    combined_text = f"{p.get('title', '')} {p.get('selftext', '')}"
                    matched_kw = _find_matched_keyword(combined_text, keywords)
                    if not matched_kw:
                        continue
                    if _contains_excluded(combined_text, exclusions):
                        continue

                    created_utc = p.get("created_utc", 0)
                    created_str = datetime.fromtimestamp(float(created_utc), tz=timezone.utc).isoformat()
                    if not _within_lookback(created_str, lookback_hours):
                        continue

                    permalink = p.get("permalink", "")
                    if not permalink.startswith("http"):
                        permalink = f"https://reddit.com{permalink}"

                    comments = []
                    # Note: comments skipped for RSS mode (no comment API without OAuth)
                    post_url = permalink
                    # PLACEHOLDER for future comment collection:
                    # comment_data = _json_fetch comments endpoint if needed

                    dummy_comment_body = ""  # skip comment fetch to avoid 403 cascade
                    _ = dummy_comment_body

                    all_posts.append({
                        "post_id":          post_id,
                        "subreddit":        subreddit_name,
                        "market_track":     track_name,
                        "matched_keyword":  matched_kw,
                        "source":           "reddit_rss",
                        "post_title":       p.get("title", ""),
                        "post_body":        p.get("selftext", ""),
                        "post_score":       p.get("score", 0),
                        "post_num_comments": 0,
                        "post_created_utc": created_str,
                        "post_url":         post_url,
                        "vulnerability_flag": vulnerability_flag,
                        "comments":         comments,
                    })
                    seen_ids.add(post_id)
                    track_coverage[track_name] = track_coverage.get(track_name, 0) + 1

                    # Respect Reddit — brief pause between comment fetches removed
                    # (no comment fetching in RSS mode)

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

    print("      [arctic-shift mode] No Reddit credentials -- fetching via Arctic Shift archive API")
    print("      (arctic-shift.photon-reddit.com — third-party archive, no IP restrictions)")
    return _collect_public_json(config)
