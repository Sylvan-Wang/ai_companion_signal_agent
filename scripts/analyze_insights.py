"""
analyze_insights.py — LLM-assisted qualitative coding and product signal analysis.

Implements the bounded method-selection layer from the spec:
  - Selects analysis methods based on sample_size and analysis_goal
  - Assembles the prompt from skill file + config context + cleaned data
  - Calls the OpenAI API
  - Parses and validates the JSON response
  - Computes priority scores (double-checks LLM arithmetic)
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

load_dotenv()  # Load .env file if present (for local development)


# ─── Method selection ─────────────────────────────────────────────────────────

def select_methods(sample_size: int, analysis_goal: str, config: dict) -> list[str]:
    """
    Apply bounded method-selection rules from the spec.
    Returns the list of method names to apply for this run.
    """
    thresholds = config.get("analysis", {}).get("sample_size_thresholds", {})
    small_threshold = thresholds.get("small", 50)
    medium_threshold = thresholds.get("medium", 200)

    optional = config.get("analysis", {}).get("optional_methods", {})

    # Always-on default pipeline
    methods = [
        "relevance_filtering",
        "qualitative_content_analysis",
        "sentiment_and_emotion_labeling",
        "product_signal_classification",
        "priority_scoring",
        "recommended_action_matching",
    ]

    # Size-gated optional methods
    if sample_size >= small_threshold and optional.get("embedding_based_clustering", False):
        methods.append("embedding_based_clustering")

    if sample_size > medium_threshold and optional.get("topic_modeling", False):
        methods.append("topic_modeling")

    # Goal-gated optional methods
    if analysis_goal == "market_monitoring":
        if optional.get("trend_comparison", True):
            methods.append("trend_comparison")
        if optional.get("novelty_detection", True):
            methods.append("novelty_detection")

    if optional.get("selected_quote_extraction", True):
        methods.append("selected_quote_extraction")

    return methods


# ─── Previous insight loading (for trend comparison) ──────────────────────────

def _load_previous_insight_ids(config: dict) -> list[str]:
    """Load insight IDs from the most recent previous report (if any)."""
    report_dir = Path(__file__).parent.parent / config.get("report", {}).get("output_dir", "reports/daily")
    if not report_dir.exists():
        return []

    json_files = sorted(report_dir.glob("*.json"))
    # Filter to insights JSON files (not quotes)
    insight_files = [f for f in json_files if "quotes" not in f.name]
    if not insight_files:
        return []

    latest = insight_files[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ins.get("id", "") for ins in data.get("insights", [])]
    except Exception:
        return []


# ─── Prompt assembly ──────────────────────────────────────────────────────────

def _load_skill() -> str:
    skill_path = Path(__file__).parent.parent / "skills" / "ai_companion_product_insight_skill.md"
    with open(skill_path, "r", encoding="utf-8") as f:
        return f.read()


def _load_prompt_template() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "daily_report_prompt.md"
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _assemble_prompt(
    clean_data: dict,
    config: dict,
    run_date: str,
    methods: list[str],
    previous_ids: list[str],
) -> str:
    skill_content = _load_skill()
    template = _load_prompt_template()

    emotion_baseline = config.get("emotion_labels", {}).get("baseline", [])
    sample_size = clean_data["sample_size"]
    analysis_goal = _infer_analysis_goal(sample_size)

    # Serialize cleaned posts (limit body size to avoid token overflow)
    posts_for_prompt = []
    for post in clean_data["posts"]:
        trimmed_post = {
            "post_id": post["post_id"],
            "subreddit": post["subreddit"],
            "tier": post.get("tier", ""),
            "keyword_matched": post.get("keyword_matched", ""),
            "post_title": post["post_title"],
            "post_body": post.get("post_body", "")[:800],  # Trim very long posts
            "post_score": post.get("post_score", 0),
            "post_url": post.get("post_url", ""),
            "comments": [
                {
                    "comment_id": c["comment_id"],
                    "comment_body": c["comment_body"][:600],
                    "comment_score": c.get("comment_score", 0),
                    "vulnerability_flag": c.get("vulnerability_flag", False),
                }
                for c in post.get("comments", [])
            ],
        }
        posts_for_prompt.append(trimmed_post)

    cleaned_data_json = json.dumps(posts_for_prompt, indent=2, ensure_ascii=False)

    # Build track coverage summary for prompt injection
    track_coverage = clean_data.get("track_coverage", {})
    missing_tracks = clean_data.get("missing_tracks", [])
    track_lines = []
    for track_name, count in track_coverage.items():
        status = f"{count} posts" if count > 0 else "⚠️ NO DATA"
        track_lines.append(f"- {track_name}: {status}")
    if missing_tracks:
        track_lines.append(f"\nMissing tracks (zero posts): {', '.join(missing_tracks)}")
    track_coverage_summary = "\n".join(track_lines) if track_lines else "No track coverage data available."

    # Build subreddit list from all active tracks
    tracks_cfg = config.get("market_coverage_tracks", {})
    all_subreddits = []
    for tc in tracks_cfg.values():
        if tc.get("active_source", "").lower() == "reddit":
            all_subreddits.extend(tc.get("subreddits", []))
    subreddits = list(dict.fromkeys(all_subreddits))

    prompt = template
    prompt = prompt.replace("{{SKILL_CONTENT}}", skill_content)
    prompt = prompt.replace("{{RUN_DATE}}", run_date)
    prompt = prompt.replace("{{SAMPLE_SIZE}}", str(sample_size))
    prompt = prompt.replace("{{ANALYSIS_GOAL}}", analysis_goal)
    prompt = prompt.replace("{{SUBREDDITS}}", ", ".join(subreddits))
    prompt = prompt.replace("{{PREVIOUS_INSIGHT_IDS}}", json.dumps(previous_ids))
    prompt = prompt.replace("{{EMOTION_BASELINE}}", json.dumps(emotion_baseline))
    prompt = prompt.replace("{{TRACK_COVERAGE_SUMMARY}}", track_coverage_summary)
    prompt = prompt.replace("{{CLEANED_DATA_JSON}}", cleaned_data_json)

    return prompt


def _infer_analysis_goal(sample_size: int) -> str:
    """Infer analysis goal based on context. Can be overridden in future versions."""
    return "user_need_discovery"


# ─── LLM call ────────────────────────────────────────────────────────────────

def _call_llm(prompt: str) -> str:
    """Call OpenAI API and return the raw response text."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY not found. Set it in your .env file or environment."
        )

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a product intelligence analyst specializing in AI companion products. "
                    "You output only valid JSON — no markdown fences, no explanation text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,         # Low temperature for consistent, structured analysis
        response_format={"type": "json_object"},
    )

    return response.choices[0].message.content


# ─── Priority score verification ─────────────────────────────────────────────

def _verify_priority_scores(insights: list[dict]) -> list[dict]:
    """
    Recompute opportunity_score and risk_score from raw dimensions.
    Corrects any LLM arithmetic errors and assigns priority_level.
    """
    for insight in insights:
        ps = insight.get("priority_score", {})
        if not ps:
            continue

        freq = ps.get("frequency", 0)
        ei = ps.get("emotional_intensity", 0)
        sr = ps.get("strategic_relevance", 0)
        act = ps.get("actionability", 0)
        nov = ps.get("novelty", 0)
        risk = ps.get("risk_level", 0)

        opp = freq + ei + sr + act + nov
        risk_score = freq + ei + sr + risk

        ps["opportunity_score"] = opp
        ps["risk_score"] = risk_score

        # Assign priority level
        if opp >= 20 or risk_score >= 16:
            priority = "high"
        elif opp >= 13 or risk_score >= 10:
            priority = "medium"
        else:
            priority = "low"

        insight["priority_score"] = ps
        insight["priority_level"] = priority

        # Special rule: single high-emotion anecdote → Research Follow-up
        if ei == 5 and freq == 1:
            insight["priority_level"] = "low"
            if insight.get("insight_type") not in ("Research Follow-up",):
                insight["insight_type"] = "Research Follow-up"

    return insights


# ─── Response parsing ─────────────────────────────────────────────────────────

def _parse_response(raw_response: str, run_date: str) -> dict:
    """Parse LLM JSON response and apply post-processing."""
    # Strip any accidental markdown fences
    clean = re.sub(r"^```json\s*", "", raw_response.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"```\s*$", "", clean)

    try:
        result = json.loads(clean)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\n\nRaw response:\n{raw_response[:500]}")

    # Ensure required top-level keys exist
    result.setdefault("run_date", run_date)
    result.setdefault("insights", [])
    result.setdefault("newly_proposed_emotion_labels", [])
    result.setdefault("run_summary", {})

    # Verify and fix priority scores
    result["insights"] = _verify_priority_scores(result["insights"])

    # Ensure each insight has a valid ID
    for i, insight in enumerate(result["insights"]):
        if not insight.get("id"):
            date_compact = run_date.replace("-", "")
            insight["id"] = f"INS-{date_compact}-{str(i+1).zfill(3)}"
        insight.setdefault("first_seen", run_date)
        insight.setdefault("last_updated", run_date)

    # Update run_summary counts
    summary = result["run_summary"]
    summary["new_insights"] = len([i for i in result["insights"] if i.get("status") == "new"])
    summary["updated_insights"] = len([i for i in result["insights"] if i.get("status") == "updated"])
    summary["watchlist_signals"] = len([i for i in result["insights"] if i.get("status") == "watchlist"])
    summary["proposed_new_emotion_labels"] = len(result.get("newly_proposed_emotion_labels", []))

    return result


# ─── Main entry point ─────────────────────────────────────────────────────────

def analyze_insights(clean_data: dict, config: dict, run_date: str) -> dict[str, Any]:
    """
    Run the full analysis pipeline and return structured insight results.

    Args:
        clean_data: Output from clean_data.py
        config:     Loaded topics.yml
        run_date:   ISO date string YYYY-MM-DD

    Returns:
        Parsed analysis result dict matching the insight_output_schema
    """
    sample_size = clean_data["sample_size"]
    analysis_goal = _infer_analysis_goal(sample_size)

    # Select methods for this run
    methods = select_methods(sample_size, analysis_goal, config)
    print(f"        Methods selected: {', '.join(methods)}")

    # Load previous insight IDs for trend tracking
    previous_ids = _load_previous_insight_ids(config)
    if previous_ids:
        print(f"        Previous insight IDs loaded: {len(previous_ids)}")
    else:
        print(f"        No previous reports found — first run baseline.")

    # Assemble prompt
    prompt = _assemble_prompt(clean_data, config, run_date, methods, previous_ids)

    # Call LLM
    print(f"        Calling OpenAI API (gpt-4o)...")
    raw_response = _call_llm(prompt)

    # Parse and verify
    result = _parse_response(raw_response, run_date)
    result["method_used"] = methods
    result["sample_size"] = sample_size

    return result
_output_schema
    """
    sample_size = clean_data["sample_size"]
    analysis_goal = _infer_analysis_goal(sample_size)

    # Select methods for this run
    methods = select_methods(sample_size, analysis_goal, config)
    print(f"        Methods selected: {', '.join(methods)}")

    # Load previous insight IDs for trend tracking
    previous_ids = _load_previous_insight_ids(config)
    if previous_ids:
        print(f"        Previous insight IDs loaded: {len(previous_ids)}")
    else:
        print(f"        No previous reports found — first run baseline.")

    # Assemble prompt
    prompt = _assemble_prompt(clean_data, config, run_date, methods, previous_ids)

    # Call LLM
    print(f"        Calling OpenAI API (gpt-4o)...")
    raw_response = _call_llm(prompt)

    # Parse and verify
    result = _parse_response(raw_response, run_date)
    result["method_used"] = methods
    result["sample_size"] = sample_size

    return result
