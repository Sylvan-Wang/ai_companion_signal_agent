# AI Companion Signal Intelligence Agent

A GitHub Actions-powered workflow that monitors Reddit communities discussing AI companion products and converts raw community conversations into structured, prioritized product intelligence reports.

**Built for:** Early-stage AI product founders and product managers who need to track user signals, adoption barriers, trust risks, and MVP opportunities in the AI companion market — without reading Reddit manually.

---

## What It Does

Every day (or on-demand), the agent:

1. **Collects** posts and comments from targeted subreddits about AI companions, companion robots, AI pets, and related products
2. **Cleans** the data — removes duplicates, noise, bot content, and short reactions
3. **Analyzes** using LLM-assisted qualitative coding (method selection adapts to sample size)
4. **Generates** a structured markdown report with prioritized insights, emotion labels, recommended actions, and selected quotes
5. **Commits** the report back to the repo automatically (GitHub Actions)

---

## Monitored Market — 7 Coverage Tracks

The agent monitors the AI companion market across 7 distinct tracks. All tracks use Reddit as the active MVP data source.

| Track | What It Covers | Key Subreddits |
|---|---|---|
| 💬 AI Companion Apps | Chatbots, memory, emotional attachment, pricing, retention | r/Replika, r/CharacterAI, r/NomiAI |
| 🤖 Physical AI Companions | Robots, AI pets, desktop companions, smart plush | r/robotics, r/robots, r/gadgets |
| ⌚ Wearable AI | AI pendants, pins, smart glasses, rings, always-on devices | r/wearables, r/smartglasses |
| 🖥️ AI Office Hardware | AI recorders, meeting assistants, transcription devices | r/gadgets, r/productivity |
| 🔊 Ambient AI Devices | Always-listening, screenless, context-aware home AI | r/smarthome, r/homeautomation |
| 📦 Reference Products | Specific launches: Rabbit R1, Humane Pin, Plaud, Limitless | r/gadgets, r/Futurology |
| 💙 Emotional Need Adjacent | Loneliness, social support needs (vulnerability-flagged) | r/lonely, r/selfimprovement |

**Source roadmap:**
- v0.1 MVP: Reddit only
- v0.2: + Product Hunt (AI hardware launches, early adopter reactions)
- v0.3: + YouTube comments (hardware reviews, failed product reactions)
- Not implemented: X, Instagram, TikTok (API friction, noise, compliance)

---

## Report Output

Each run produces three files in `reports/daily/`:

| File | Contents |
|---|---|
| `YYYY-MM-DD.md` | Full human-readable report with insights, scores, quotes |
| `YYYY-MM-DD_insights.json` | Structured insight data (machine-readable) |
| `YYYY-MM-DD_quotes.json` | Selected quotes grouped by insight ID |

### Report Sections

- **New Signals Today** — Fresh insights with priority scores and recommended actions
- **Updated Signals** — Existing insights with new supporting evidence
- **Persistent Watchlist** — Signals being monitored but not yet actionable
- **Noise Log** — Cleaning statistics
- **Newly Proposed Emotion Labels** — Auto-discovered emotions from the data

---

## Insight Priority Scoring (6 Dimensions)

Each insight is scored across two separate tracks:

```
Opportunity Score = frequency + emotional_intensity + strategic_relevance + actionability + novelty
Risk Score        = frequency + emotional_intensity + strategic_relevance + risk_level
```

| Priority | Opportunity Score | Risk Score |
|---|---|---|
| 🔴 High | ≥ 20 | ≥ 16 |
| 🟡 Medium | 13–19 | 10–15 |
| ⚪ Low | < 13 | < 10 |

---

## Analysis Method Selection

The agent automatically selects the smallest sufficient method stack based on sample size:

| Sample Size | Methods Used |
|---|---|
| < 50 posts+comments | Qualitative content analysis + emotion labeling + signal classification |
| 50–200 | + Embedding-based clustering |
| > 200 | + Topic modeling + trend comparison |

---

## Local Setup (Phase 1)

### Prerequisites

- Python 3.11+
- An OpenAI API key

### Install

```bash
git clone https://github.com/YOUR_USERNAME/ai_companion_signal_agent
cd ai_companion_signal_agent
pip install -r requirements.txt
```

### Configure environment

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
```

*(Reddit credentials are not required for Phase 1 — the agent uses sample data automatically.)*

### Run

```bash
python scripts/run_daily.py
```

The report will be saved to `reports/daily/YYYY-MM-DD.md`.

---

## GitHub Actions Setup (Phase 2)

### 1. Add secrets to your GitHub repo

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `REDDIT_CLIENT_ID` | From reddit.com/prefs/apps (Phase 3) |
| `REDDIT_CLIENT_SECRET` | From reddit.com/prefs/apps (Phase 3) |
| `REDDIT_USER_AGENT` | e.g. `ai_companion_signal_agent/0.1 by yourusername` |

### 2. Push the workflow file

The workflow is already configured at `.github/workflows/daily-ai-companion-insights.yml`.
Push to your default branch to activate it.

### 3. Manual trigger

Go to **Actions → Daily AI Companion Signal Intelligence → Run workflow** to trigger manually without waiting for the schedule.

---

## Reddit API Setup (Phase 3)

1. Go to [reddit.com/prefs/apps](https://reddit.com/prefs/apps)
2. Click **Create App** → select **script**
3. Set redirect URI to `http://localhost:8080`
4. Copy `client_id` (under the app name) and `client_secret`
5. Add all three values to your `.env` file or GitHub Secrets

When Reddit credentials are present, the agent automatically switches from sample data to live collection.

---

## Repo Structure

```
ai_companion_signal_agent/
├── README.md
├── requirements.txt
├── .env                         ← Create locally, never commit
├── config/
│   └── topics.yml               ← All agent configuration lives here
├── skills/
│   └── ai_companion_product_insight_skill.md   ← LLM analysis instructions
├── prompts/
│   └── daily_report_prompt.md   ← Runtime prompt template
├── scripts/
│   ├── run_daily.py             ← Entry point
│   ├── collect_reddit.py        ← Data collection (sample + live mode)
│   ├── clean_data.py            ← Noise removal and filtering
│   ├── analyze_insights.py      ← LLM analysis and priority scoring
│   └── save_report.py           ← Report generation
├── data/
│   ├── raw/
│   │   └── sample_raw_posts.json   ← Sample data for Phase 1 testing
│   └── processed/
├── reports/
│   └── daily/                   ← Auto-generated reports
└── .github/
    └── workflows/
        └── daily-ai-companion-insights.yml
```

---

## Configuration

All agent behavior is controlled by `config/topics.yml`. Key settings:

```yaml
community_source:
  mvp_subreddits: [Replika, CharacterAI, robotics, lonely, Futurology]

collection:
  lookback_window: "24h"
  post_limit_per_run: 60
  comment_limit_per_post: 20
  minimum_comment_length: 120

insight_policy:
  max_new_insights_per_report: 5
  allow_zero_new_insight_days: true   # Do not fabricate when there's no signal
```

---

## Ethics

This agent monitors communities that include vulnerable users (loneliness, mental health). The following rules are enforced in the skill file and analysis output:

- No usernames or author data stored
- Content from `r/lonely` and similar communities is flagged and treated as emotional need signal only — not as an acquisition target
- No mental health diagnoses produced
- Dependency, manipulation, and safety risks are flagged as product risk signals, not engagement opportunities
- All insights carry a confidence level and source limitation statement

---

## Build Phases

| Phase | Status | Description |
|---|---|---|
| Phase 1 | ✅ Ready | Local run with sample data |
| Phase 2 | 🔜 Next | GitHub Actions with manual trigger |
| Phase 3 | 🔜 Later | Live Reddit data collection |
| Phase 4 | 🔜 Later | Scheduled automation |

---

## Portfolio Context

**Project type:** Product Operations + AI Workflow Automation  
**Target role:** AI Product Manager / Product Operations / Growth Analyst  
**Skills demonstrated:** Product signal intelligence design, LLM prompt engineering, qualitative coding automation, GitHub Actions CI/CD, Python data pipeline

---

*Source limitation: Reddit signals are directional qualitative signals, not market-wide demand proof. All insights require validation before driving major product decisions.*
