"""
Quick Reddit API auth test.
Run: python scripts/test_reddit_auth.py

Fill in your credentials below, or set them as env vars.
"""
import os, sys

CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "PASTE_YOUR_CLIENT_ID_HERE")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "PASTE_YOUR_CLIENT_SECRET_HERE")
USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "ai_companion_signal_agent/0.1 by Sylvan-Wang")

if "PASTE" in CLIENT_ID:
    print("ERROR: Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET first.")
    print("       Either edit this file or set them as env vars.")
    sys.exit(1)

try:
    import praw
except ImportError:
    print("praw not installed — run: pip install praw")
    sys.exit(1)

print(f"Connecting to Reddit with client_id={CLIENT_ID[:6]}...")
reddit = praw.Reddit(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    user_agent=USER_AGENT,
)

try:
    # Fetch 3 posts from r/replika as a quick smoke test
    sub = reddit.subreddit("replika")
    posts = list(sub.hot(limit=3))
    print(f"\n✅ SUCCESS — Reddit API is working!")
    print(f"   Fetched {len(posts)} posts from r/replika:")
    for p in posts:
        print(f"   - [{p.score}] {p.title[:60]}")
except Exception as e:
    print(f"\n❌ FAILED — {e}")
    print("   Check your client_id and client_secret.")
