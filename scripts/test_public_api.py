"""Quick test: Reddit public JSON API (no credentials needed)."""
import requests

headers = {"User-Agent": "ai_companion_signal_agent/0.1"}
r = requests.get(
    "https://www.reddit.com/r/replika/new.json",
    headers=headers,
    params={"limit": 5},
    timeout=15,
)
print(f"Status: {r.status_code}")
posts = r.json()["data"]["children"]
print(f"Got {len(posts)} posts from r/replika:")
for p in posts[:3]:
    print(f"  - {p['data']['title'][:70]}")
