"""
send_email.py -- Daily report email delivery for the AI Companion Signal Agent.

Recipient sources (merged automatically):
  1. REPORT_EMAIL_TO env var -- comma-separated addresses (GitHub Secret)
  2. config/subscribers.txt  -- one address per line, managed separately

New subscriptions can be collected via the HTML subscription form
(wired to Formspree or any form backend -- see config/subscribers.txt).

Required env vars:
    SMTP_USER         -- sender Gmail address
    SMTP_PASSWORD     -- Gmail App Password (myaccount.google.com/apppasswords)

Optional:
    REPORT_EMAIL_TO   -- additional recipients (comma-separated)
    SMTP_HOST         -- default: smtp.gmail.com
    SMTP_PORT         -- default: 587
    REPORT_EMAIL_FROM -- display name/address (defaults to SMTP_USER)
    FORMSPREE_ID      -- your Formspree form ID (enables live subscription form)
"""

import os
import re
import smtplib
import textwrap
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


# ---- Subscriber list ---------------------------------------------------------

_SUBSCRIBERS_FILE = Path(__file__).parent.parent / "config" / "subscribers.txt"


def _load_subscribers() -> list[str]:
    """
    Load the persistent subscriber list from config/subscribers.txt.
    Each non-blank, non-comment line is treated as an email address.
    """
    if not _SUBSCRIBERS_FILE.exists():
        return []
    lines = _SUBSCRIBERS_FILE.read_text(encoding="utf-8").splitlines()
    return [
        ln.strip() for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _all_recipients() -> list[str]:
    """Merge recipients from env var and subscribers.txt, deduplicating."""
    from_env = [
        a.strip()
        for a in os.getenv("REPORT_EMAIL_TO", "").split(",")
        if a.strip()
    ]
    from_file = _load_subscribers()
    seen: set[str] = set()
    merged: list[str] = []
    for addr in from_env + from_file:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            merged.append(addr)
    return merged


# ---- Config ------------------------------------------------------------------

def _email_config() -> dict | None:
    """Read SMTP config from environment. Returns None if not configured."""
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    if not (smtp_user and smtp_pass):
        return None

    recipients = _all_recipients()
    if not recipients:
        return None

    return {
        "to_list":    recipients,
        "to_header":  ", ".join(recipients),
        "from":       os.getenv("REPORT_EMAIL_FROM", smtp_user).strip(),
        "smtp_host":  os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port":  int(os.getenv("SMTP_PORT", "587")),
        "smtp_user":  smtp_user,
        "smtp_password": smtp_pass,
    }


# ---- Markdown parsing --------------------------------------------------------

def _parse_stats(md: str) -> dict:
    """Extract run stats from the markdown summary section."""
    def find(pattern: str) -> str:
        m = re.search(pattern, md, re.IGNORECASE)
        return m.group(1) if m else "—"

    return {
        "posts":     find(r"sample size[^\d]*(\d+)"),
        "new":       find(r"new insights?[^\d]*(\d+)"),
        "updated":   find(r"updated insights?[^\d]*(\d+)"),
        "watchlist": find(r"watchlist signals?[^\d]*(\d+)"),
    }


def _parse_insights(md: str, max_insights: int = 5) -> list[dict]:
    """Split markdown on ### headings and extract structured insight cards."""
    # Any opening/closing curly or straight quote
    OPEN_Q  = r'[“„‟"]'
    CLOSE_Q = r'[”„‟"]'
    DASH    = r'[-–—]'

    blocks = re.split(r"\n(?=###\s)", md)
    insights: list[dict] = []

    for block in blocks:
        if not block.startswith("###"):
            continue

        lines = block.strip().splitlines()
        title = re.sub(r"^###\s*", "", lines[0]).strip()
        body  = "\n".join(lines[1:])

        # Signal strength
        sm = re.search(r"\*\*signal.strength\*\*[:\s]+(\w+)", body, re.IGNORECASE)
        raw = sm.group(1).lower() if sm else "medium"
        pip = "high" if raw == "high" else ("med" if raw == "medium" else "low")

        # Category tag (first two words, newline-separated for narrow column)
        cm = re.search(r"\*\*(category|track)[^*]*\*\*\s*(.+)", body, re.IGNORECASE)
        if cm:
            words = cm.group(2).strip().replace("_", " ").split()
            category_tag = "<br>".join(filter(None, [
                " ".join(words[:2]),
                " ".join(words[2:4]),
            ]))
        else:
            category_tag = "Signal"

        # First descriptive paragraph
        desc = ""
        for ln in lines[1:]:
            ln = ln.strip()
            if ln and not ln.startswith(("**", ">", "#", "-", "*", "|")):
                desc = ln
                break

        # First blockquote quote + attribution
        qm  = re.search(OPEN_Q + r"(.+?)" + CLOSE_Q, body)
        qm2 = re.search(r'>\s*"(.+?)"', body)
        quote = (qm or qm2)
        quote_text = quote.group(1).strip() if quote else ""

        dm = re.search(r">" + DASH + r"\s*(.+)$", body, re.MULTILINE)
        quote_source = dm.group(1).strip() if dm else ""

        insights.append({
            "title":        title,
            "category_tag": category_tag,
            "description":  desc,
            "quote":        quote_text,
            "quote_source": quote_source,
            "pip":          pip,
        })

        if len(insights) >= max_insights:
            break

    return insights


def _parse_featured_quote(md: str) -> tuple[str, str]:
    """Find the Voice of the Day featured quote."""
    OPEN_Q  = r'[“„‟"]'
    CLOSE_Q = r'[”„‟"]'
    DASH    = r'[-–—]'

    m = re.search(
        r"voice of the day.+?\n+>\s*" + OPEN_Q + r"(.+?)" + CLOSE_Q + r".+?\n>\s*" + DASH + r"\s*(.+)",
        md, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Fallback: first blockquote pair
    bq = re.search(
        r'>\s*"(.+?)"\s*\n>\s*' + DASH + r'\s*(.+)', md
    )
    if bq:
        return bq.group(1).strip(), bq.group(2).strip()

    return "", ""


def _plain_text(md: str) -> str:
    plain = re.sub(r"#{1,3}\s+", "", md)
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
    plain = re.sub(r"`(.+?)`", r"\1", plain)
    plain = re.sub(r"^---+$", "---", plain, flags=re.MULTILINE)
    return textwrap.shorten(plain, width=5000, placeholder="...[see full report on GitHub]")


# ---- HTML rendering ----------------------------------------------------------

def _render_insight_row(ins: dict) -> str:
    quote_html = ""
    if ins["quote"]:
        quote_html = f"""
              <div class="quote-block">
                <div class="quote-text">"{ins['quote']}"</div>
                <div class="quote-source">&#8212; {ins['quote_source']}</div>
              </div>"""
    return f"""
          <div class="insight">
            <div class="insight-tag">{ins['category_tag']}</div>
            <div class="insight-body">
              <div class="insight-title">{ins['title']}</div>
              <div class="insight-desc">{ins['description']}</div>
              {quote_html}
            </div>
            <div class="priority-pip {ins['pip']}"></div>
          </div>"""


def _build_email(report_md: str, run_date: str) -> tuple[str, str, str]:
    """Returns (subject, plain_text, html_body)."""

    stats    = _parse_stats(report_md)
    insights = _parse_insights(report_md)
    fq_text, fq_source = _parse_featured_quote(report_md)

    teaser_titles = [i["title"] for i in insights[:2]]
    teaser  = " - ".join(teaser_titles) if teaser_titles else "AI Companion Signal Report"
    subject = f"[Signal Agent] {run_date} -- {teaser}"

    try:
        weekday = datetime.strptime(run_date, "%Y-%m-%d").strftime("%A")
    except Exception:
        weekday = "Daily"

    insight_rows_html = (
        "".join(_render_insight_row(i) for i in insights)
        if insights else
        '<p style="color:#9a9080;font-style:italic;font-size:13px;">'
        "No insights extracted -- view the full report on GitHub.</p>"
    )

    featured_html = ""
    if fq_text:
        featured_html = f"""
      <div class="section">
        <div class="section-header">
          <div class="section-label">Today&#8217;s Most Representative Voice</div>
          <div class="section-rule"></div>
        </div>
        <div class="featured-quote">
          <div class="featured-quote-text">{fq_text}</div>
          <div class="featured-quote-source">&#8212; {fq_source}</div>
        </div>
      </div>"""

    landing_page_url = "https://sylvan-wang.github.io/ai_companion_signal_agent/"

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Companion Signal Report &#183; {run_date}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@300;400;500;600&family=IM+Fell+English:ital@0;1&display=swap');
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:#e8e4dc; display:flex; justify-content:center;
            padding:48px 20px 64px;
            font-family:'Noto Serif SC','SimSun',serif;
            color:#2c2c2c; min-height:100vh; }}
    .page {{ width:640px; display:flex; flex-direction:column; gap:0; }}
    .email-wrap {{ background:#faf8f3; border:1px solid #d8d0c4;
                   box-shadow:0 2px 12px rgba(0,0,0,0.07); position:relative; }}
    .email-wrap::before {{ content:''; display:block; height:3px; background:#3a3a3a; }}

    /* Header */
    .header {{ padding:36px 48px 28px; border-bottom:1px solid #e0d8cc; }}
    .header-eyebrow {{ font-family:'Times New Roman',serif; font-size:9px;
                       letter-spacing:0.28em; color:#9a9080; text-transform:uppercase;
                       margin-bottom:16px; }}
    .header-title {{ font-size:26px; font-weight:600; color:#1e1e1e;
                     line-height:1.35; margin-bottom:10px; }}
    .header-date {{ font-family:'Times New Roman',serif; font-style:italic;
                    font-size:12px; color:#9a9080; }}

    /* Stats */
    .stats-strip {{ padding:14px 48px; background:#f3f0e8;
                    border-bottom:1px solid #e0d8cc; display:flex; }}
    .stat {{ display:flex; flex-direction:column; gap:3px; flex:1; position:relative; }}
    .stat + .stat::before {{ content:''; position:absolute; left:0; top:4px; bottom:4px;
                              width:1px; background:#d8d0c4; }}
    .stat + .stat {{ padding-left:20px; }}
    .stat-num {{ font-family:'Times New Roman',serif; font-size:20px;
                 color:#3a3a3a; line-height:1; font-style:italic; }}
    .stat-label {{ font-family:'Times New Roman',serif; font-size:9px;
                   letter-spacing:0.18em; color:#b0a898; text-transform:uppercase; }}

    /* Body */
    .body {{ padding:36px 48px; }}
    .section {{ margin-bottom:36px; }}
    .section:last-child {{ margin-bottom:0; }}
    .section-header {{ display:flex; align-items:center; gap:12px; margin-bottom:18px; }}
    .section-label {{ font-family:'Times New Roman',serif; font-size:9px;
                      letter-spacing:0.28em; text-transform:uppercase;
                      color:#9a9080; white-space:nowrap; }}
    .section-rule {{ flex:1; height:1px; background:#e0d8cc; }}

    /* Insight row */
    .insight {{ padding:15px 0; border-bottom:1px solid #ede8de;
                display:grid; grid-template-columns:80px 1fr 10px;
                gap:18px; align-items:start; }}
    .insight:last-child {{ border-bottom:none; padding-bottom:0; }}
    .insight-tag {{ font-family:'Times New Roman',serif; font-size:8.5px;
                    letter-spacing:0.14em; text-transform:uppercase;
                    color:#b0a898; line-height:1.7; padding-top:2px; }}
    .insight-title {{ font-size:14.5px; font-weight:500; color:#1e1e1e;
                      line-height:1.4; margin-bottom:6px; }}
    .insight-desc {{ font-size:11.5px; color:#5a5550; line-height:1.8; font-weight:300; }}
    .priority-pip {{ width:7px; height:7px; border-radius:50%;
                     margin-top:5px; flex-shrink:0; }}
    .high {{ background:#4a4a3a; }}
    .med  {{ background:#9a9080; }}
    .low  {{ background:transparent; border:1px solid #c8c0b4; }}

    /* Quote */
    .quote-block {{ margin:10px 0 4px; padding:10px 16px;
                    border-left:2px solid #c8c0b4; background:#f3f0e8; }}
    .quote-text {{ font-family:'Times New Roman',serif; font-style:italic;
                   font-size:11px; color:#6a6258; line-height:1.75; }}
    .quote-source {{ font-family:'Times New Roman',serif; font-style:italic;
                     font-size:9px; color:#b0a898; margin-top:5px; }}

    /* Featured quote */
    .featured-quote {{ padding:20px 24px; border:1px solid #d8d0c4;
                       background:#f3f0e8; position:relative; }}
    .featured-quote::before {{ content:'\\201C'; font-family:'Times New Roman',serif;
                                font-size:64px; color:#d8d0c4; position:absolute;
                                top:2px; left:18px; line-height:1; }}
    .featured-quote-text {{ font-family:'Times New Roman',serif; font-style:italic;
                             font-size:13px; color:#3a3632; line-height:1.8;
                             padding-left:28px; }}
    .featured-quote-source {{ font-family:'Times New Roman',serif; font-style:italic;
                               font-size:9.5px; color:#b0a898; margin-top:10px;
                               padding-left:28px; letter-spacing:0.08em; }}

    /* Footer */
    .footer {{ padding:22px 48px; border-top:1px solid #e0d8cc; background:#f3f0e8;
               display:flex; justify-content:space-between; align-items:center; }}
    .footer-sig {{ font-family:'Times New Roman',serif; font-style:italic;
                   font-size:12px; color:#b0a898; }}
    .footer-link {{ font-family:'Times New Roman',serif; font-size:9.5px;
                    letter-spacing:0.18em; text-transform:uppercase; color:#6a6258;
                    text-decoration:none; padding:6px 14px; border:1px solid #c8c0b4; }}

    /* Share card */
    .share-card {{ margin-top:24px; background:#faf8f3; border:1px solid #d8d0c4;
                   box-shadow:0 2px 8px rgba(0,0,0,0.05);
                   padding:32px 48px 28px; position:relative; }}
    .share-card::before {{ content:''; display:block; position:absolute;
                           top:0; left:0; right:0; height:2px;
                           background:repeating-linear-gradient(
                             90deg,#3a3a3a 0px,#3a3a3a 4px,
                             transparent 4px,transparent 8px); }}
    .share-label {{ font-family:'Times New Roman',serif; font-size:9px;
                    letter-spacing:0.28em; text-transform:uppercase;
                    color:#9a9080; margin-bottom:10px; }}
    .share-title {{ font-size:17px; font-weight:500; color:#1e1e1e; margin-bottom:6px; }}
    .share-desc {{ font-family:'Times New Roman',serif; font-style:italic;
                   font-size:11.5px; color:#9a9080; line-height:1.7; margin-bottom:16px; }}
    .share-link {{ display:inline-block; font-family:'Times New Roman',serif;
                   font-size:11px; color:#3a3632; text-decoration:none;
                   border-bottom:1px solid #d8d0c4; padding-bottom:1px; }}
    .contact-line {{ margin-top:20px; padding-top:16px; border-top:1px solid #e0d8cc;
                     display:flex; align-items:center; gap:8px; }}
    .contact-name {{ font-size:11px; color:#6a6258; letter-spacing:0.04em; }}
    .contact-divider {{ color:#c8c0b4; font-size:11px; }}
    .contact-email {{ font-family:'Times New Roman',serif; font-style:italic;
                      font-size:11px; color:#9a9080; text-decoration:none;
                      border-bottom:1px solid #d8d0c4; padding-bottom:1px; }}
    .limitation-note {{ font-family:'Times New Roman',serif; font-style:italic;
                        font-size:10px; color:#b0a898; line-height:1.7;
                        border-top:1px solid #e8e0d4; padding-top:16px; margin-top:4px; }}
  </style>
</head>
<body>
<div class="page">

  <div class="email-wrap">
    <div class="header">
      <div class="header-eyebrow">AI Companion Signal Intelligence &#183; Daily Brief</div>
      <div class="header-title">&#128225; {weekday}&#8217;s Signal Report</div>
      <div class="header-date">{run_date}</div>
    </div>

    <div class="stats-strip">
      <div class="stat">
        <span class="stat-num">{stats['posts']}</span>
        <span class="stat-label">Posts Scanned</span>
      </div>
      <div class="stat">
        <span class="stat-num">{stats['new']}</span>
        <span class="stat-label">New Signals</span>
      </div>
      <div class="stat">
        <span class="stat-num">{stats['updated']}</span>
        <span class="stat-label">Updated</span>
      </div>
      <div class="stat">
        <span class="stat-num">{stats['watchlist']}</span>
        <span class="stat-label">Watchlist</span>
      </div>
    </div>

    <div class="body">
      <div class="section">
        <div class="section-header">
          <div class="section-label">Today&#8217;s Signals</div>
          <div class="section-rule"></div>
        </div>
        {insight_rows_html}
      </div>

      {featured_html}

      <div class="limitation-note">
        * Reddit signals are directional qualitative signals, not market-wide demand proof.
        Each insight is tagged with evidence count and confidence level.
      </div>
    </div>

    <div class="footer">
      <div class="footer-sig">ai companion signal agent &#183; automated daily brief</div>
      <a class="footer-link"
         href="https://github.com/Sylvan-Wang/ai_companion_signal_agent">
        View on GitHub &#8594;
      </a>
    </div>
  </div>

  <div class="share-card">
    <div class="share-label">Share &#183; Forward this report</div>
    <div class="share-title">Know someone tracking the AI companion space?</div>
    <div class="share-desc">
      Forward this report &#8212; or send them the subscription link below.
      New subscribers can sign up and receive tomorrow&#8217;s report.
    </div>
    <a class="share-link" href="{landing_page_url}">
      &#128279;&nbsp; {landing_page_url}
    </a>
    <div class="contact-line">
      <span class="contact-name">Sylvan Wang</span>
      <span class="contact-divider">&#183;</span>
      <a class="contact-email" href="mailto:zichenwang209@gmail.com">
        zichenwang209@gmail.com
      </a>
    </div>
  </div>

</div>
</body>
</html>"""

    return subject, _plain_text(report_md), html


# ---- Send --------------------------------------------------------------------

def send_report_email(report_path: str, run_date: str) -> bool:
    """
    Send the daily report to all recipients (env var + subscribers.txt).

    Args:
        report_path: Path to the generated markdown report file.
        run_date:    ISO date string (YYYY-MM-DD).

    Returns:
        True if sent, False if skipped or failed.
    """
    cfg = _email_config()
    if cfg is None:
        print("      Email skipped -- SMTP_USER / SMTP_PASSWORD not set, or no recipients.")
        return False

    try:
        report_md = Path(report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"      Email skipped -- report file not found: {report_path}")
        return False

    subject, plain_text, html_body = _build_email(report_md, run_date)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = cfg["from"]
    msg["To"]      = cfg["to_header"]
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.sendmail(cfg["smtp_user"], cfg["to_list"], msg.as_string())
        n = len(cfg["to_list"])
        print(f"      Email sent to {n} recipient{'s' if n > 1 else ''}: {cfg['to_header']}")
        print(f"      Subject: {subject}")
        return True
    except Exception as e:
        print(f"      Email FAILED: {e}")
        print("      Pipeline continues -- email failure is non-fatal.")
        return False
