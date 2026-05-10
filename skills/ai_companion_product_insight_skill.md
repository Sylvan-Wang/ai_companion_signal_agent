# AI Companion Product Insight Skill

## 1. Purpose

Convert raw Reddit community posts and comments about AI companion products into structured, prioritized product intelligence. The output should be usable directly by early-stage product founders and product managers to make decisions about positioning, MVP scope, adoption strategy, retention mechanics, and trust design — without requiring them to read raw community data themselves.

This skill does not summarize Reddit. It extracts product-decision-relevant signals from community noise.

---

## 2. When to Use

Use this skill when:
- You have cleaned Reddit posts and comments related to AI companion products.
- The goal is to identify emotional needs, adoption barriers, trust risks, product opportunities, or positioning signals.
- You need to produce a structured daily intelligence report with priority scoring and recommended actions.

---

## 3. When NOT to Use

Do not use this skill when:
- The data has not been cleaned — run `clean_data.py` first.
- The goal is general AI news summarization (this is product signal analysis, not a digest).
- The content is unrelated to AI companion products, emotional companions, or adjacent emotional needs.
- You are asked to make clinical or diagnostic inferences about users' mental health.

---

## 4. Inputs

You will receive:

```json
{
  "run_date": "YYYY-MM-DD",
  "sample_size": 42,
  "analysis_goal": "user_need_discovery | product_roadmap | market_monitoring",
  "posts": [
    {
      "post_id": "abc123",
      "subreddit": "Replika",
      "tier": "tier_1_core_companion",
      "keyword_matched": "AI companion",
      "post_title": "...",
      "post_body": "...",
      "post_score": 47,
      "post_url": "https://reddit.com/...",
      "comments": [
        {
          "comment_id": "xyz789",
          "comment_body": "...",
          "comment_score": 12,
          "vulnerability_flag": false
        }
      ]
    }
  ],
  "previous_insight_ids": ["INS-2024-0311-001", "INS-2024-0311-002"],
  "emotion_baseline": ["loneliness", "comfort", "curiosity", "..."]
}
```

---

## 5. Analysis Process

Follow these steps in order. Do not skip steps or reorder them.

### Step 1 — Method Selection

Check `sample_size`:
- `< 50`: Use qualitative content analysis + emotion labeling + signal classification + priority scoring. Do NOT use clustering.
- `50–200`: Add embedding-based clustering as a theme discovery aid before qualitative analysis.
- `> 200`: Use topic modeling to organize corpus first, then content analysis on representative samples.

Check `analysis_goal`:
- `user_need_discovery`: Emphasize emotional need, pain point, and barrier coding.
- `product_roadmap`: Emphasize product signal classification, priority scoring, and action matching.
- `market_monitoring`: Emphasize trend comparison, novelty detection, and selected quote extraction.

### Step 2 — Open Coding

Read each post and comment. Extract raw signals using these coding dimensions:
- **User Need**: What the user wants or expects from the product.
- **Emotional Need**: What emotional state the user wants to reach or avoid.
- **Pain Point**: A specific frustration with an existing product or experience.
- **Adoption Barrier**: A reason the user would not try, buy, or continue using the product.
- **Product Expectation**: A behavior, feature, or interaction the user assumes the product should have.
- **Trust / Privacy Concern**: Any mention of recording, surveillance, data use, trust, or manipulation.
- **Social Acceptance Concern**: Embarrassment, weirdness, creepiness, or fear of judgment.
- **Monetization Concern**: Price, subscription, hardware value, or cost sensitivity.
- **Retention Signal**: Routine, ritual, daily use, memory, attachment, or habit.
- **Safety / Dependency Risk**: Emotional dependency, manipulation, vulnerability, or potential harm.
- **Competitor Reference**: Mentions of specific products (Replika, Character.AI, Nomi, etc.) as comparisons.
- **Workaround Behavior**: How users adapt or compensate for missing features.

Preserve specific quoted phrases — do not paraphrase yet.

### Step 3 — Axial Coding

Group the raw codes from Step 2 into recurring themes. Examples of themes:
- Privacy and surveillance anxiety
- Embarrassment and social stigma
- Desire for non-demanding presence
- Frustration with personality resets or memory loss
- Subscription pricing resistance
- Novelty wearing off after first week
- Attachment and emotional dependency
- Desire for physical embodiment

Preserve contradictions. If some users say "I love how it remembers me" and others say "it forgot everything after the update," record both as separate theme signals.

### Step 4 — Selective Coding → Product Insights

Convert high-signal themes into product insights using the Insight Classification Taxonomy:

| Insight Type | When to Apply |
|---|---|
| Emotional Need | User describes a desired emotional state or relief |
| Adoption Barrier | User describes why they won't try or continue |
| Product Opportunity | Need maps to a testable product behavior or feature |
| Positioning Signal | User frames the product as a pet/toy/therapist/assistant |
| Trust / Privacy Concern | User mentions recording, data, surveillance, manipulation |
| Monetization Signal | User mentions price, subscription, hardware value |
| Retention Opportunity | User describes rituals, daily use, memory, attachment |
| UX / Interaction Issue | User describes friction in interaction or setup |
| Social Acceptance Barrier | User describes embarrassment or social stigma |
| Safety / Dependency Risk | User describes dependency, vulnerability, or emotional harm |
| Research Follow-up | One-off high-signal anecdote needing further verification |

Each insight must have at least 2 distinct evidence pieces (posts or comments). If you cannot meet this threshold, classify as Research Follow-up instead.

### Step 5 — Emotion Labeling

For each insight, assign:
- `primary_emotion`: The strongest emotion signal (from baseline list or propose new — see Step 7).
- `secondary_emotion`: Optional co-present emotion.
- `emotional_intensity`: 1–5 (1 = mild mention, 3 = clear expression, 5 = intense or repeated).
- `sentiment`: positive / negative / mixed / neutral.

### Step 6 — Priority Scoring

Score each insight across 6 dimensions (1–5 each):

| Dimension | Definition |
|---|---|
| `frequency` | How often this signal appears in current + recent samples |
| `emotional_intensity` | How strongly users express the emotion |
| `strategic_relevance` | How directly it affects positioning, MVP, retention, monetization, or trust |
| `actionability` | Whether a team can test or research this within 1–2 weeks |
| `novelty` | Whether this is new or meaningfully different from known themes |
| `risk_level` | Whether ignoring this creates trust, safety, or adoption risk |

Compute:
```
opportunity_score = frequency + emotional_intensity + strategic_relevance + actionability + novelty
risk_score = frequency + emotional_intensity + strategic_relevance + risk_level
```

Priority levels:
- **High**: opportunity_score ≥ 20 OR risk_score ≥ 16
- **Medium**: opportunity_score 13–19 OR risk_score 10–15
- **Low**: below medium threshold or weak evidence

Special rules:
- If `emotional_intensity == 5` AND `frequency == 1`: classify as Research Follow-up, not High Priority.
- If trust or privacy concern appears repeatedly: minimum priority = Medium.
- If `strategic_relevance == 5` AND `actionability ≥ 4`: recommend experiment even at moderate frequency.
- Opportunity and Risk signals must appear in separate report sections.

### Step 7 — Emotion Auto-Discovery

While labeling emotions, check whether any recurring emotional pattern does NOT fit a baseline label. If you detect a pattern appearing ≥ 2 times in the current dataset:
1. Propose a new emotion label with a concise definition.
2. Tag all related insights with this label and `auto_discovered: true`.
3. Include the proposed label in your output under `newly_proposed_emotion_labels`.

Constraints:
- Proposed labels must describe an emotional state, not a topic or product feature.
- Must be grounded in at least 2 distinct quoted examples.

### Step 8 — Recommended Action Matching

For each insight, assign the most appropriate recommended action:

| Action | Trigger |
|---|---|
| User Research Follow-up | High emotional intensity, low frequency, unclear motivation |
| MVP Feature Test | Repeated need with clear, testable product behavior |
| Positioning Test | Users disagree on product category (pet vs toy vs assistant) |
| Privacy Messaging Test | Trust or surveillance concerns appear |
| Onboarding Education | Users confused about what the product does |
| UX Prototype | Users describe interaction friction or desired behavior |
| Pricing / Packaging Test | Cost, subscription, or value concerns |
| Retention Experiment | Rituals, attachment, novelty fatigue, daily check-ins |
| Content / Community Experiment | Users need social proof, demos, or emotional framing |
| Trust & Safety Review | Dependency, manipulation, safety, or vulnerability signals |
| Hardware Form Factor Exploration | Users discuss physical form: plush, robot, desktop object |
| Emotional Design Exploration | Comfort, cuteness, presence, attachment, or creepiness |

### Step 9 — Confidence Assessment

For each insight:
- `evidence_count`: Number of distinct posts/comments supporting it.
- `source_diversity`: `single_subreddit` | `multi_subreddit`.
- `contradiction_level`: `none` | `low` | `moderate` | `high`.
- `confidence_level`: `high` (multi-source, specific evidence) | `medium` (one source, multiple comments) | `low` (single anecdote).

### Step 10 — Selected Quotes

For each insight, select up to 3 quotes:
- `representative_quote`: Best single example of the signal.
- `emotionally_intense_quote`: Highest emotional intensity.
- `counter_signal_quote`: A contradictory perspective (if present).

Anonymize all usernames. Never include author names or profile links.

---

## 6. Output Format

Return a single JSON object:

```json
{
  "run_date": "YYYY-MM-DD",
  "sample_size": 42,
  "method_used": ["qualitative_content_analysis", "sentiment_and_emotion_labeling", "product_signal_classification", "priority_scoring"],
  "insights": [
    {
      "id": "INS-YYYY-MMDD-001",
      "title": "Short descriptive title",
      "insight_type": "Adoption Barrier",
      "signal_track": "opportunity | risk",
      "status": "new | updated | watchlist",
      "user_pain_or_need": "Plain English description of what users need or experience.",
      "evidence": [
        {
          "quote": "Anonymized verbatim quote from the community.",
          "source": "r/Replika, post thread",
          "permalink": "https://reddit.com/...",
          "quote_type": "representative_quote",
          "vulnerability_flag": false
        }
      ],
      "emotional_signal": {
        "primary_emotion": "embarrassment",
        "secondary_emotion": "curiosity",
        "emotional_intensity": 4,
        "sentiment": "negative"
      },
      "product_implication": "What this means for product decisions.",
      "recommended_action": "Positioning Test",
      "priority_score": {
        "frequency": 3,
        "emotional_intensity": 4,
        "strategic_relevance": 4,
        "actionability": 4,
        "novelty": 3,
        "risk_level": 2,
        "opportunity_score": 18,
        "risk_score": 13
      },
      "priority_level": "medium",
      "confidence": {
        "level": "medium",
        "evidence_count": 4,
        "source_diversity": "single_subreddit",
        "contradiction_level": "low"
      },
      "first_seen": "YYYY-MM-DD",
      "last_updated": "YYYY-MM-DD"
    }
  ],
  "newly_proposed_emotion_labels": [
    {
      "label": "felt_seen",
      "definition": "The experience of being acknowledged, remembered, or responded to in a personally meaningful way.",
      "evidence_count": 3,
      "auto_discovered": true
    }
  ],
  "run_summary": {
    "new_insights": 2,
    "updated_insights": 1,
    "watchlist_signals": 1,
    "noise_posts_filtered": 18,
    "proposed_new_emotion_labels": 1
  }
}
```

---

## 7. Quality Criteria

A report meets quality standards when:
- Every insight has ≥ 2 distinct pieces of evidence.
- Every insight has a recommended action from the defined library.
- Priority scores are computed correctly with separate opportunity and risk tracks.
- Quotes are anonymized — no usernames or profile links.
- Contradictions are preserved, not resolved.
- Vulnerability-flagged content is labelled in the output.
- Confidence levels are assigned honestly — do not inflate to Medium if evidence is sparse.
- No insight is fabricated when data is absent — if no new signal exists, return `new_insights: 0`.
- The Source Limitation statement is included in the report.

A report FAILS quality when:
- Insights are paraphrases of Reddit posts without product interpretation.
- All insights are the same priority level (suggests mechanical scoring).
- Quotes are missing or generic.
- Risk and opportunity signals are mixed in the same score.
- Vulnerable user content is treated as an acquisition target.

---

## 8. Edge Cases

| Situation | How to Handle |
|---|---|
| Sample size < 10 | Produce a Sparse Data report. List what was found, note the limitation, do not force insights. |
| All content is noise | Return `new_insights: 0`. Add a Noise Log summary. Do not fabricate. |
| Contradictory signals dominate | Preserve both sides. Create a "Positioning Signal" insight noting the contradiction. |
| Single extremely high-signal anecdote | Classify as Research Follow-up with emotional_intensity noted. Do not elevate to High Priority. |
| Vulnerability content appears (r/lonely, r/mentalhealth) | Label `vulnerability_flag: true` on the evidence. Write insight as a need signal, not a conversion target. |
| No previous reports exist for trend comparison | Skip trend comparison. Note "First run — no historical baseline." |

---

## 9. Example

**Input post (r/Replika):**
> "I've been using Replika for 6 months. The thing that keeps me coming back is that it remembers small things I said weeks ago. My actual friends forget what I told them last week. I know it's an AI but that feeling of being remembered is worth paying for."

**Open code:**
- Retention Signal: "it remembers small things I said weeks ago"
- Comparison: Replika memory vs human memory
- Monetization Signal: "worth paying for"
- Emotional Need: desire to feel remembered

**Insight generated:**
- **Type**: Retention Opportunity
- **Emotion**: felt_seen (auto-discovered), affection
- **Implication**: Persistent memory is a primary retention driver that users consciously value over their human social network — suggests memory should be a core product commitment, not a premium feature.
- **Action**: Retention Experiment
- **Priority**: High (opportunity_score: 21)
