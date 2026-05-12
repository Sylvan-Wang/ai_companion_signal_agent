"""
send_email.py — Daily report email delivery for the AI Companion Signal Agent.

Reads the generated markdown report, converts it to a clean HTML email,
and sends via Gmail SMTP (or any SMTP provider).

Required environment variables (set in .env for local, GitHub Secrets for CI):
    REPORT_EMAIL_TO      — recipient address (your email)
    SMTP_USER            — sender Gmail address
    SMTP_PASSWORD        — Gmail App Password (not your regular password)
                           Create one at: myaccount.google.com/apppasswords

Optional:
    SMTP_HOST            — default: smtp.gmail.com
    SMTP_PORT            — default: 587
    REPORT_EMAIL_FROM    — display name + address, defaults to SMTP_USER

Gracefully skips (no error) if email config is missing — so the pipeline
still works without email configured.
"""

import os
import re
import smtplib
import textwrap
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


# ─── Config ────────────────────────────────────────────────────────────────────

def _email_config() -> dict | None:
    """Read email config from environment. Returns None if not configured."""
    to_addr = os.getenv("REPORT_EMAIL_TO", "").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    if not (to_addr and smtp_user and smtp_pass):
        return None

    return {
        "to": to_addr,
        "from": os.getenv("REPORT_EMAIL_FROM", smtp_user).strip(),
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_user": smtp_user,
        "smtp_password": smtp_pass,
    }


# ─── Markdown → HTML ───────────────────────────────────────────────────────────

def _md_to_html(md: str) -> str:
    """
    Lightweight markdown → HTML converter (no external deps).
    Handles: headings, bold, bullet lists, horizontal rules, blockquotes, code.
    """
    lines = md.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        # Horizontal rule
        if re.match(r"^---+$", line.strip()):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append('<hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">')
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(m.group(1))
            text = _inline(m.group(2))
            sizes = {1: "22px", 2: "18px", 3: "15px"}
            margins = {1: "28px 0 8px", 2: "22px 0 6px", 3: "16px 0 4px"}
            html_lines.append(
                f'<h{level} style="font-size:{sizes[level]};margin:{margins[level]};'
                f'color:#1a1a1a;font-family:sans-serif;">{text}</h{level}>'
            )
            continue

        # Blockquote
        if line.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = _inline(line[2:])
            html_lines.append(
                f'<blockquote style="border-left:4px solid #f0a500;margin:8px 0;padding:6px 14px;'
                f'background:#fffbf0;color:#555;font-style:italic;">{text}</blockquote>'
            )
            continue

        # Bullet list items
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            if not in_list:
                html_lines.append('<ul style="margin:6px 0 6px 20px;padding:0;">')
                in_list = True
            text = _inline(m.group(1))
            html_lines.append(
                f'<li style="margin:3px 0;color:#333;font-size:14px;">{text}</li>'
            )
            continue

        # Close list if needed
        if in_list:
            html_lines.append("</ul>")
            in_list = False

        # Empty line → spacer
        if not line.strip():
            html_lines.append('<div style="height:6px;"></div>')
            continue

        # Normal paragraph
        html_lines.append(
            f'<p style="margin:4px 0;color:#333;font-size:14px;line-height:1.6;">'
            f'{_inline(line)}</p>'
        )

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline(text: str) -> str:
    """Handle inline markdown: **bold**, `code`, emoji pass-through."""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r'<strong>\1</strong>', text)
    # Inline code
    text = re.sub(r"`(.+?)`", r'<code style="background:#f4f4f4;padding:1px 5px;border-radius:3px;font-size:13px;">\1</code>', text)
    return text


# ─── Email assembly ────────────────────────────────────────────────────────────

def _build_email(report_md: str, run_date: str) -> tuple[str, str, str]:
    """
    Returns (subject, plain_text, html_body).
    """
    # Extract first few insight titles for subject line teaser
    titles = re.findall(r"^###?\s+.+?:\s+(.+)$", report_md, re.MULTILINE)
    teaser = " · ".join(titles[:2]) if titles else "AI Companion Signal Report"

    subject = f"[Signal Agent] {run_date} — {teaser}"

    # Plain text fallback (strip markdown)
    plain = re.sub(r"#{1,3}\s+", "", report_md)
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
    plain = re.sub(r"`(.+?)`", r"\1", plain)
    plain = re.sub(r"^---+$", "---", plain, flags=re.MULTILINE)
    plain = textwrap.shorten(plain, width=5000, placeholder="...[see full report on GitHub]")

    # HTML body
    body_html = _md_to_html(report_md)

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:30px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;overflow:hidden;
                    box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr><td style="background:#1a1a2e;padding:24px 32px;">
          <p style="margin:0;color:#a8d8ea;font-size:12px;letter-spacing:1px;text-transform:uppercase;">
            AI Companion Signal Intelligence Agent
          </p>
          <h1 style="margin:6px 0 0;color:#ffffff;font-size:20px;">
            Daily Report · {run_date}
          </h1>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding:28px 32px;">
          {body_html}
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#f9f9f9;padding:16px 32px;border-top:1px solid #eee;">
          <p style="margin:0;color:#999;font-size:12px;">
            Generated by AI Companion Signal Intelligence Agent ·
            <a href="https://github.com/Sylvan-Wang/ai_companion_signal_agent"
               style="color:#999;">View on GitHub</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return subject, plain, html


# ─── Send ──────────────────────────────────────────────────────────────────────

def send_report_email(report_path: str, run_date: str) -> bool:
    """
    Send the daily report email.

    Args:
        report_path: Path to the generated markdown report file.
        run_date:    ISO date string (YYYY-MM-DD).

    Returns:
        True if sent, False if skipped (no config) or failed.
    """
    cfg = _email_config()
    if cfg is None:
        print("      Email skipped — REPORT_EMAIL_TO / SMTP_USER / SMTP_PASSWORD not set.")
        print("      To enable: add these to .env (local) or GitHub Secrets (CI).")
        return False

    # Read report
    try:
        report_md = Path(report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"      Email skipped — report file not found: {report_path}")
        return False

    subject, plain_text, html_body = _build_email(report_md, run_date)

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Send
    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.sendmail(cfg["smtp_user"], cfg["to"], msg.as_string())
        print(f"      Email sent → {cfg['to']}")
        print(f"      Subject: {subject}")
        return True
    except Exception as e:
        print(f"      Email FAILED: {e}")
        print("      Pipeline continues — email failure is non-fatal.")
        return False
