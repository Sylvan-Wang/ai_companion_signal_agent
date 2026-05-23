"""
collect_signals.py (collect_reddit.py) — Multi-source signal intelligence collection.

Data sources (priority order):
  1. Reddit public JSON  — primary; best user-voice signal. Works locally, may 403 on cloud.
  2. App Store reviews   — Apple App Store RSS + Google Play; real user voice; no IP blocks.
  3. Product Hunt RSS    — new AI product launches; no auth needed, works everywhere.
  4. Tech news RSS       — industry news (TechCrunch, VentureBeat, The Verge, Wired, MIT TR).

Reddit 403 errors are silently skipped — App Store reviews + RSS provide baseline coverage
on cloud runners; full Reddit data is collected on local/scheduled runs.

Return format:
  {
    "posts": [...],
    "track_coverage": {track_name: post_count, ...},
    "missing_tracks": [track_names_with_zero_posts]
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


# ─── Mode detection ───────────────────────────────────────────────────────────

def _is_sample_mode() -> bool:
    return os.getenv("SIGNAL_AGENT_USE_SAMPLE", "").lower() == "true"

def _is_praw_mode() -> bool:
    return all([
        os.getenv("REDDIT_CLIENT_ID"),
        os.getenv("REDDIT_CLIENT_SECRET"),
        os.getenv("REDDIT_USER_AGENT"),
    ])


# ─── Config helpers ───────────────────────────────────────────────────────────

def _get_active_tracks(config: dict) -> dict[str, dict]:
    return config.get("market_coverage_tracks", {})

def _get_exclusion_keywords(config: dict) -> list[str]:
    return config.get("exclusion_keywords", [])

def _is_vulnerable_track(track_name: str, track_cfg: dict) -> bool:
    return track_cfg.get("vulnerability_flag", False)


# ─── Text helpers ─────────────────────────────────────────────────────────────

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
        cutoff  = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        return created >= cutoff
    except Exception:
        return True


# ─── Sample data loader ───────────────────────────────────────────────────────

def _load_sample_data(config: dict) -> dict[str, Any]:
    sample_path = Path(__file__).parent.parent / "data" / "raw" / "sample_raw_posts.json"
    if not sample_path.exists():
        raise FileNotFoundError(f"Sample data not found at {sample_path}")
    with open(sample_path, "r", encoding="utf-8") as f:
        raw_posts = json.load(f)
    tracks = config.get("market_coverage_tracks", {})
    sub_to_track: dict[str, str] = {}
    for track_name, track_cfg in tracks.items():
        for sub in track_cfg.get("subreddits", []):
            sub_to_track[sub.lower()] = track_name
    exclusions = _get_exclusion_keywords(config)
    tagged_posts: list[dict] = []
    track_coverage: dict[str, int] = {name: 0 for name in tracks}
    for post in raw_posts:
        subreddit  = post.get("subreddit", "")
        track_name = sub_to_track.get(subreddit.lower(), "ai_companion_apps")
        track_cfg  = tracks.get(track_name, {})
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)
        search_text = f"{post.get('post_title', '')} {post.get('post_body', '')}"
        keywords    = track_cfg.get("keywords", [])
        matched_kw  = _find_matched_keyword(search_text, keywords) or post.get("keyword_matched", "")
        if _contains_excluded(search_text, exclusions):
            continue
        tagged_posts.append({**post, "market_track": track_name, "matched_keyword": matched_kw,
                              "source": "sample", "vulnerability_flag": vulnerability_flag})
        track_coverage[track_name] = track_coverage.get(track_name, 0) + 1
    missing_tracks = [t for t, count in track_coverage.items() if count == 0]
    print(f"      [sample] Loaded {len(tagged_posts)} posts from {sample_path.name}")
    return {"posts": tagged_posts, "track_coverage": track_coverage, "missing_tracks": missing_tracks}


# ─── Reddit public JSON ───────────────────────────────────────────────────────

_REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

def _reddit_fetch(subreddit: str, sort: str = "new", limit: int = 50) -> list[dict]:
    """Fetch posts from Reddit public JSON. Returns [] silently on 403 (cloud IP block)."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    try:
        resp = requests.get(url, headers=_REDDIT_HEADERS,
                            params={"limit": limit, "raw_json": 1}, timeout=15)
        if resp.status_code in (403, 429, 503):
            return []   # silently skip — expected on cloud runners
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
                "num_comments": p.get("num_comments", 0),
            })
        return posts
    except Exception:
        return []


def _collect_reddit(config: dict, active_tracks: dict, exclusions: list[str],
                    lookback_hours: int) -> tuple[list[dict], dict[str, int], set[str]]:
    """Collect from Reddit. Returns (posts, partial_coverage, seen_ids)."""
    collection_cfg = config.get("collection", {})
    post_limit = min(collection_cfg.get("post_limit_per_run", 60), 100)

    all_posts:      list[dict]     = []
    seen_ids:       set[str]       = set()
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}
    reddit_blocked = False

    for track_name, track_cfg in active_tracks.items():
        subreddits         = track_cfg.get("subreddits", [])
        keywords           = track_cfg.get("keywords", [])
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)

        for subreddit_name in subreddits:
            for sort in ("new", "hot"):
                raw_posts = _reddit_fetch(subreddit_name, sort, post_limit)
                if not raw_posts and not reddit_blocked:
                    # Check if the first subreddit is blocked; if so, flag it
                    pass
                time.sleep(1.5)

                for p in raw_posts:
                    post_id = p.get("id", "")
                    if not post_id or post_id in seen_ids:
                        continue
                    combined = f"{p.get('title', '')} {p.get('selftext', '')}"
                    matched_kw = _find_matched_keyword(combined, keywords)
                    if not matched_kw:
                        continue
                    if _contains_excluded(combined, exclusions):
                        continue
                    created_ts  = float(p.get("created_utc", 0))
                    created_str = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
                    if not _within_lookback(created_str, lookback_hours):
                        continue
                    permalink = p.get("permalink", "")
                    if not permalink.startswith("http"):
                        permalink = f"https://reddit.com{permalink}"
                    all_posts.append({
                        "post_id":           post_id,
                        "subreddit":         subreddit_name,
                        "market_track":      track_name,
                        "matched_keyword":   matched_kw,
                        "source":            "reddit",
                        "post_title":        p.get("title", ""),
                        "post_body":         p.get("selftext", ""),
                        "post_score":        p.get("score", 0),
                        "post_num_comments": p.get("num_comments", 0),
                        "post_created_utc":  created_str,
                        "post_url":          permalink,
                        "vulnerability_flag": vulnerability_flag,
                        "comments":          [],
                    })
                    seen_ids.add(post_id)
                    track_coverage[track_name] = track_coverage.get(track_name, 0) + 1

    return all_posts, track_coverage, seen_ids


# ─── Product Hunt RSS ─────────────────────────────────────────────────────────

PRODUCT_HUNT_RSS = "https://www.producthunt.com/feed"

# ─── Tech news RSS feeds ──────────────────────────────────────────────────────

TECH_RSS_FEEDS = [
    ("TechCrunch AI",   "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI",  "https://venturebeat.com/category/ai/feed/"),
    ("The Verge",       "https://www.theverge.com/rss/index.xml"),
    ("Wired AI",        "https://www.wired.com/feed/category/artificial-intelligence/latest/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
]

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; feedparser/6.0; ai_companion_signal_agent)"
}


def _parse_rss_feed(feed_name: str, feed_url: str, after_dt: datetime) -> list[dict]:
    """Fetch and parse one RSS/Atom feed. Returns raw item dicts."""
    try:
        import feedparser, calendar
    except ImportError:
        return []
    try:
        feed = feedparser.parse(feed_url, request_headers=_RSS_HEADERS)
    except Exception as e:
        print(f"        WARNING: RSS parse error ({feed_name}): {e}")
        return []
    items = []
    for entry in feed.entries:
        try:
            ts = calendar.timegm(
                entry.get("published_parsed") or entry.get("updated_parsed") or time.gmtime()
            )
        except Exception:
            ts = int(time.time())
        if datetime.fromtimestamp(ts, tz=timezone.utc) < after_dt:
            continue
        link     = entry.get("link", "")
        entry_id = entry.get("id", link) or link
        body = ""
        if entry.get("content"):
            body = entry.content[0].get("value", "")
        elif entry.get("summary"):
            body = entry.summary
        body = re.sub(r"<[^>]+>", " ", body).strip()
        items.append({
            "_id":   entry_id,
            "_feed": feed_name,
            "_ts":   ts,
            "title": entry.get("title", ""),
            "body":  body,
            "link":  link,
        })
    return items


def _rss_item_to_post(item: dict, track_name: str, matched_kw: str,
                      vulnerability_flag: bool) -> dict:
    created_str = datetime.fromtimestamp(item["_ts"], tz=timezone.utc).isoformat()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", item["_id"])[:64]
    return {
        "post_id":           safe_id,
        "subreddit":         item["_feed"],
        "market_track":      track_name,
        "matched_keyword":   matched_kw,
        "source":            "rss_news",
        "post_title":        item["title"],
        "post_body":         item["body"],
        "post_score":        0,
        "post_num_comments": 0,
        "post_created_utc":  created_str,
        "post_url":          item["link"],
        "vulnerability_flag": vulnerability_flag,
        "comments":          [],
    }


def _collect_rss(active_tracks: dict, exclusions: list[str], after_dt: datetime,
                 seen_ids: set[str]) -> tuple[list[dict], dict[str, int]]:
    """Collect from Product Hunt + tech news RSS. Adds to existing seen_ids set."""
    all_posts:      list[dict]     = []
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}

    # All feeds: Product Hunt first, then tech news
    all_feeds = [("Product Hunt", PRODUCT_HUNT_RSS)] + TECH_RSS_FEEDS

    for feed_name, feed_url in all_feeds:
        items = _parse_rss_feed(feed_name, feed_url, after_dt)
        time.sleep(1.0)
        for item in items:
            item_id = item["_id"]
            if item_id in seen_ids:
                continue
            combined = f"{item['title']} {item['body']}"
            for track_name, track_cfg in active_tracks.items():
                keywords           = track_cfg.get("keywords", [])
                vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)
                if _contains_excluded(combined, exclusions):
                    break
                matched_kw = _find_matched_keyword(combined, keywords)
                if not matched_kw:
                    continue
                all_posts.append(_rss_item_to_post(item, track_name, matched_kw, vulnerability_flag))
                seen_ids.add(item_id)
                track_coverage[track_name] = track_coverage.get(track_name, 0) + 1
                break  # assign to first matching track only

    return all_posts, track_coverage


# ─── App Store Reviews ───────────────────────────────────────────────────────

def _fetch_apple_reviews(apple_id: str, app_name: str, limit: int = 50) -> list[dict]:
    """Fetch reviews from Apple App Store RSS API (no auth required)."""
    url = f"https://itunes.apple.com/us/rss/customerreviews/id={apple_id}/sortBy=mostRecent/json"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "ai_companion_signal_agent/0.1"})
        resp.raise_for_status()
        entries = resp.json().get("feed", {}).get("entry", [])
        reviews = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            rating  = entry.get("im:rating", {}).get("label", "")
            title   = entry.get("title", {}).get("label", "")
            content = entry.get("content", {}).get("label", "")
            updated = entry.get("updated", {}).get("label", "")
            author  = entry.get("author", {}).get("name", {}).get("label", "")
            if not content or len(content.strip()) < 30:
                continue
            reviews.append({
                "rating": rating,
                "title":  title,
                "body":   content.strip(),
                "date":   updated,
                "author": author,
                "store":  "apple",
            })
            if len(reviews) >= limit:
                break
        return reviews
    except Exception as e:
        print(f"        WARNING: Apple App Store fetch failed for {app_name}: {e}")
        return []


def _fetch_google_play_reviews(package_id: str, app_name: str, limit: int = 50) -> list[dict]:
    """Fetch reviews from Google Play (google-play-scraper). No auth required."""
    try:
        from google_play_scraper import reviews as gp_reviews, Sort
        result, _ = gp_reviews(
            package_id, lang="en", country="us",
            sort=Sort.NEWEST, count=limit,
        )
        reviews = []
        for r in result:
            content = r.get("content", "") or ""
            if len(content.strip()) < 30:
                continue
            reviews.append({
                "rating": str(r.get("score", "")),
                "title":  "",
                "body":   content.strip(),
                "date":   str(r.get("at", "")),
                "author": r.get("userName", ""),
                "store":  "google_play",
            })
        return reviews
    except ImportError:
        return []   # library not installed — skip silently
    except Exception as e:
        print(f"        WARNING: Google Play fetch failed for {app_name} ({package_id}): {e}")
        return []


def _review_to_post(review: dict, app_name: str, market_track: str,
                    vulnerability_flag: bool) -> dict:
    """Convert an app store review to the standard post dict format."""
    rating = review.get("rating", "")
    stars  = f"★{rating}" if rating else ""
    title  = review.get("title", "") or ""
    body   = review.get("body", "")
    store  = review.get("store", "app_store")
    date   = review.get("date", "")

    # Build a stable unique ID
    import hashlib
    uid = hashlib.md5(f"{app_name}:{store}:{body[:80]}".encode()).hexdigest()[:16]

    # Parse date if possible
    try:
        from dateutil import parser as du_parser
        created_str = du_parser.parse(date).astimezone(timezone.utc).isoformat()
    except Exception:
        created_str = datetime.now(timezone.utc).isoformat()

    # Prefix title with star rating for LLM context
    full_title = f"[{app_name} {stars} App Review] {title}".strip()

    return {
        "post_id":            uid,
        "subreddit":          f"{app_name} App Store ({store})",
        "market_track":       market_track,
        "matched_keyword":    app_name,
        "source":             f"app_store_{store}",
        "post_title":         full_title,
        "post_body":          body,
        "post_score":         int(review.get("rating", 0) or 0),
        "post_num_comments":  0,
        "post_created_utc":   created_str,
        "post_url":           "",
        "vulnerability_flag": vulnerability_flag,
        "comments":           [],
    }


def _collect_app_reviews(config: dict, active_tracks: dict,
                          seen_ids: set[str]) -> tuple[list[dict], dict[str, int]]:
    """
    Collect App Store reviews from Apple + Google Play.
    Apps are configured in config/topics.yml → app_store_apps.
    Always works from cloud runners — no IP restrictions.
    """
    app_configs   = config.get("app_store_apps", [])
    all_posts:      list[dict]     = []
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}

    if not app_configs:
        print("        [App Store] No apps configured in topics.yml → app_store_apps")
        return all_posts, track_coverage

    for app_cfg in app_configs:
        app_name     = app_cfg.get("name", "")
        apple_id     = app_cfg.get("apple_id", "")
        gplay_id     = app_cfg.get("google_play_id", "")
        market_track = app_cfg.get("market_track", "ai_companion_apps")
        vuln_flag    = active_tracks.get(market_track, {}).get("vulnerability_flag", False)

        apple_reviews = []
        gplay_reviews = []

        if apple_id:
            apple_reviews = _fetch_apple_reviews(apple_id, app_name)
            time.sleep(0.5)

        if gplay_id:
            gplay_reviews = _fetch_google_play_reviews(gplay_id, app_name)
            time.sleep(0.5)

        all_reviews = apple_reviews + gplay_reviews
        added = 0
        for review in all_reviews:
            post = _review_to_post(review, app_name, market_track, vuln_flag)
            pid  = post["post_id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            all_posts.append(post)
            track_coverage[market_track] = track_coverage.get(market_track, 0) + 1
            added += 1

        print(f"          {app_name}: {len(apple_reviews)} Apple + {len(gplay_reviews)} Google Play "
              f"→ {added} added")

    return all_posts, track_coverage


# ─── PRAW (OAuth) collection ──────────────────────────────────────────────────

def _collect_praw(config: dict) -> dict[str, Any]:
    """Full Reddit collection via PRAW OAuth — used when credentials are present."""
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
    post_limit     = collection_cfg.get("post_limit_per_run", 60)
    comment_limit  = collection_cfg.get("comment_limit_per_post", 20)
    lookback_window = collection_cfg.get("lookback_window", "24h")
    lookback_hours  = int(re.sub(r"[^\d]", "", str(lookback_window))) if lookback_window else 24

    exclusions    = _get_exclusion_keywords(config)
    active_tracks = _get_active_tracks(config)
    all_posts:      list[dict]     = []
    seen_ids:       set[str]       = set()
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}

    for track_name, track_cfg in active_tracks.items():
        subreddits         = track_cfg.get("subreddits", [])
        keywords           = track_cfg.get("keywords", [])
        vulnerability_flag = _is_vulnerable_track(track_name, track_cfg)
        for subreddit_name in subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                for post in subreddit.new(limit=post_limit):
                    if post.id in seen_ids or post.stickied:
                        continue
                    combined = f"{post.title} {post.selftext}"
                    matched_kw = _find_matched_keyword(combined, keywords)
                    if not matched_kw or _contains_excluded(combined, exclusions):
                        continue
                    created_str = datetime.fromtimestamp(
                        post.created_utc, tz=timezone.utc).isoformat()
                    if not _within_lookback(created_str, lookback_hours):
                        continue
                    post.comments.replace_more(limit=0)
                    comments = []
                    for comment in post.comments[:comment_limit]:
                        if not hasattr(comment, "body"):
                            continue
                        if comment.body in ("[deleted]", "[removed]"):
                            continue
                        comments.append({
                            "comment_id":          comment.id,
                            "comment_body":        comment.body,
                            "comment_score":       comment.score,
                            "comment_created_utc": datetime.fromtimestamp(
                                comment.created_utc, tz=timezone.utc).isoformat(),
                            "vulnerability_flag":  vulnerability_flag,
                        })
                    all_posts.append({
                        "post_id":           post.id,
                        "subreddit":         subreddit_name,
                        "market_track":      track_name,
                        "matched_keyword":   matched_kw,
                        "source":            "reddit",
                        "post_title":        post.title,
                        "post_body":         post.selftext,
                        "post_score":        post.score,
                        "post_num_comments": post.num_comments,
                        "post_created_utc":  created_str,
                        "post_url":          f"https://reddit.com{post.permalink}",
                        "vulnerability_flag": vulnerability_flag,
                        "comments":          comments,
                    })
                    seen_ids.add(post.id)
                    track_coverage[track_name] = track_coverage.get(track_name, 0) + 1
            except Exception as e:
                print(f"        WARNING: PRAW failed for r/{subreddit_name}: {e}")
    missing_tracks = [t for t, count in track_coverage.items() if count == 0]
    return {"posts": all_posts, "track_coverage": track_coverage, "missing_tracks": missing_tracks}


# ─── Main live collection ─────────────────────────────────────────────────────

def _collect_live(config: dict) -> dict[str, Any]:
    """
    Multi-source collection:
      1. Reddit public JSON  (primary — works locally, silently skipped on cloud)
      2. App Store reviews   (Apple + Google Play — always works, real user voice)
      3. Product Hunt RSS    (new AI product launches — always works)
      4. Tech news RSS       (industry signal — always works)
    """
    collection_cfg  = config.get("collection", {})
    lookback_window = collection_cfg.get("lookback_window", "24h")
    lookback_hours  = int(re.sub(r"[^\d]", "", str(lookback_window))) if lookback_window else 24
    after_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    exclusions    = _get_exclusion_keywords(config)
    active_tracks = _get_active_tracks(config)

    # ── 1. Reddit ─────────────────────────────────────────────────────────────
    print("        [Reddit] Collecting from subreddits...")
    reddit_posts, reddit_coverage, seen_ids = _collect_reddit(
        config, active_tracks, exclusions, lookback_hours
    )
    reddit_count = len(reddit_posts)
    if reddit_count > 0:
        print(f"        [Reddit] {reddit_count} posts collected")
    else:
        print("        [Reddit] 0 posts (likely cloud IP block — continuing with other sources)")

    # ── 2. App Store Reviews ──────────────────────────────────────────────────
    print("        [App Store] Fetching Apple + Google Play reviews...")
    app_posts, app_coverage = _collect_app_reviews(config, active_tracks, seen_ids)
    print(f"        [App Store] {len(app_posts)} reviews collected")

    # ── 3 & 4. Product Hunt + Tech news RSS ──────────────────────────────────
    print("        [RSS] Fetching Product Hunt + tech news feeds...")
    rss_posts, rss_coverage = _collect_rss(active_tracks, exclusions, after_dt, seen_ids)
    print(f"        [RSS] {len(rss_posts)} posts collected")

    # ── Merge ─────────────────────────────────────────────────────────────────
    all_posts = reddit_posts + app_posts + rss_posts
    track_coverage: dict[str, int] = {name: 0 for name in active_tracks}
    for name in active_tracks:
        track_coverage[name] = (
            reddit_coverage.get(name, 0) +
            app_coverage.get(name, 0) +
            rss_coverage.get(name, 0)
        )

    missing_tracks = [t for t, count in track_coverage.items() if count == 0]
    print(f"      Total: {len(all_posts)} posts "
          f"(Reddit: {len(reddit_posts)} | App Store: {len(app_posts)} | RSS: {len(rss_posts)})")
    print(f"      Coverage: { {k: v for k, v in track_coverage.items() if v > 0} }")

    return {"posts": all_posts, "track_coverage": track_coverage, "missing_tracks": missing_tracks}


# ─── Public entry point ───────────────────────────────────────────────────────

def collect_posts(config: dict) -> dict:
    """
    Main entry point. Mode selection:
      - SIGNAL_AGENT_USE_SAMPLE=true  → sample data (local testing)
      - REDDIT_CLIENT_ID set          → PRAW OAuth (full Reddit with comments)
      - default                       → Reddit public JSON + Product Hunt + RSS news
    """
    if _is_sample_mode():
        print("      [sample mode] SIGNAL_AGENT_USE_SAMPLE=true -- using sample data")
        return _load_sample_data(config)

    if _is_praw_mode():
        print("      [praw mode] Reddit OAuth credentials detected")
        return _collect_praw(config)

    print("      [live mode] Reddit public JSON + Product Hunt + RSS news feeds")
    print("      (Reddit silently skipped if cloud IP blocked; RSS always runs)")
    return _collect_live(config)
