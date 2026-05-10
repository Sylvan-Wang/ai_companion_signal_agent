"""
clean_data.py — Deduplication, relevance filtering, and low-signal removal.

Applies the cleaning rules defined in config/topics.yml.
Returns a clean data dict ready for analysis.
"""

import re
from typing import Any


# ─── Low-signal comment patterns ─────────────────────────────────────────────

DEFAULT_LOW_SIGNAL_PATTERNS = [
    r"^lol\.?$",
    r"^same\.?$",
    r"^cool\.?$",
    r"^yes\.?$",
    r"^no\.?$",
    r"^maybe\.?$",
    r"^agreed?\.?$",
    r"^disagree\.?$",
    r"^same here\.?$",
    r"^this\.?$",
    r"^wow\.?$",
    r"^nice\.?$",
    r"^true\.?$",
    r"robots will (kill|destroy|take over|enslave)",
    r"ai (will|is going to) (kill|destroy|replace) (us|humans|everyone)",
]

BOT_SIGNALS = [
    "i am a bot",
    "i'm a bot",
    "automoderator",
    "this action was performed automatically",
    "contact the moderators",
]


def _compile_patterns(pattern_strings: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in pattern_strings]


def _is_low_signal(text: str, patterns: list[re.Pattern]) -> bool:
    stripped = text.strip()
    return any(p.search(stripped) for p in patterns)


def _is_bot_content(text: str) -> bool:
    lower = text.lower()
    return any(signal in lower for signal in BOT_SIGNALS)


def _is_deleted(text: str) -> bool:
    return text.strip() in ("[deleted]", "[removed]", "")


def clean_posts(raw_posts: list[dict], config: dict) -> dict[str, Any]:
    """
    Clean raw posts according to config rules.

    Returns:
        {
            "posts": [...],        # Cleaned post list
            "sample_size": int,    # Total posts + comments kept
            "stats": {...}         # Cleaning statistics
        }
    """
    cleaning_cfg = config.get("cleaning", {})
    collection_cfg = config.get("collection", {})
    min_comment_length = collection_cfg.get("minimum_comment_length", 120)

    # Compile low-signal patterns
    custom_patterns = cleaning_cfg.get("low_signal_patterns", [])
    all_pattern_strings = DEFAULT_LOW_SIGNAL_PATTERNS + custom_patterns
    low_signal_patterns = _compile_patterns(all_pattern_strings)

    stats = {
        "raw_posts": len(raw_posts),
        "removed_duplicates": 0,
        "removed_deleted": 0,
        "removed_short_comments": 0,
        "removed_bot_comments": 0,
        "removed_low_signal_comments": 0,
        "kept_posts": 0,
        "kept_comments": 0,
    }

    seen_post_ids: set[str] = set()
    clean_posts_list = []

    for post in raw_posts:
        post_id = post.get("post_id", "")

        # ── Deduplicate posts ─────────────────────────────────────
        if cleaning_cfg.get("remove_duplicates", True):
            if post_id in seen_post_ids:
                stats["removed_duplicates"] += 1
                continue
            seen_post_ids.add(post_id)

        # ── Remove deleted post bodies ────────────────────────────
        post_body = post.get("post_body", "")
        if cleaning_cfg.get("remove_deleted_content", True):
            if _is_deleted(post_body):
                post_body = ""  # Keep post but clear body if deleted

        # ── Clean comments ────────────────────────────────────────
        raw_comments = post.get("comments", [])
        clean_comments = []

        for comment in raw_comments:
            body = comment.get("comment_body", "")

            # Remove deleted
            if cleaning_cfg.get("remove_deleted_content", True) and _is_deleted(body):
                stats["removed_deleted"] += 1
                continue

            # Remove bot content
            if cleaning_cfg.get("remove_bot_like_content", True) and _is_bot_content(body):
                stats["removed_bot_comments"] += 1
                continue

            # Remove short comments
            if cleaning_cfg.get("remove_short_comments", True) and len(body.strip()) < min_comment_length:
                stats["removed_short_comments"] += 1
                continue

            # Remove low-signal reactions
            if cleaning_cfg.get("remove_low_signal_reactions", True) and _is_low_signal(body, low_signal_patterns):
                stats["removed_low_signal_comments"] += 1
                continue

            clean_comments.append(comment)

        # ── Keep post if it has a title (posts without title are invalid) ─
        if not post.get("post_title", "").strip():
            continue

        # ── Keep post (even if no comments pass — post body may have signal) ─
        clean_post = {**post, "post_body": post_body, "comments": clean_comments}
        clean_posts_list.append(clean_post)
        stats["kept_posts"] += 1
        stats["kept_comments"] += len(clean_comments)

    sample_size = stats["kept_posts"] + stats["kept_comments"]

    return {
        "posts": clean_posts_list,
        "sample_size": sample_size,
        "stats": stats,
    }
