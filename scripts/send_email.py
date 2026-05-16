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
    """
    Read email config from environment. Returns None if not configured.

    REPORT_EMAIL_TO supports multiple recipients, comma-separated:
        REPORT_EMAIL_TO=alice@gmail.com,bob@company.com,carol@example.org
    """
    to_raw = os.getenv("REPORT_EMAIL_TO", "").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    if not (to_raw and smtp_user and smtp_pass):
        return None

    # Parse comma-separated recipient list, strip whitespace from each
    recipients = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

    return {
        "to_list": recipients,           # list of all recipients
        "to_header": ", ".join(recipients),  # for the To: header
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

    # Weekday for the header greeting
    try:
        weekday = datetime.strptime(run_date, "%Y-%m-%d").strftime("%A")
    except Exception:
        weekday = "Daily"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Companion Signal Report · {run_date}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #f0f2f5; font-family: 'Inter', Arial, sans-serif; }}

    /* Markdown content styles */
    .content h1 {{ font-size: 20px; font-weight: 700; color: #0f172a; margin: 28px 0 10px; }}
    .content h2 {{ font-size: 17px; font-weight: 600; color: #1e293b; margin: 24px 0 8px;
                   padding-bottom: 6px; border-bottom: 2px solid #e2e8f0; }}
    .content h3 {{ font-size: 15px; font-weight: 600; color: #334155; margin: 18px 0 6px; }}
    .content p  {{ font-size: 14px; color: #475569; line-height: 1.7; margin: 6px 0; }}
    .content ul {{ margin: 8px 0 8px 20px; padding: 0; }}
    .content li {{ font-size: 14px; color: #475569; line-height: 1.7; margin: 4px 0; }}
    .content strong {{ color: #1e293b; font-weight: 600; }}
    .content code {{ background: #f1f5f9; color: #0f172a; padding: 2px 6px;
                     border-radius: 4px; font-size: 12px; font-family: monospace; }}
    .content blockquote {{ border-left: 3px solid #6366f1; margin: 12px 0;
                           padding: 10px 16px; background: #f8f7ff;
                           border-radius: 0 6px 6px 0; color: #4f46e5;
                           font-style: italic; font-size: 13px; }}
    .content hr {{ border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }}
  </style>
</head>
<body>
<div style="background:#f0f2f5;padding:32px 16px;min-height:100vh;">

  <!-- Card wrapper -->
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;
              overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- ── Header ── -->
    <div style="background:linear-gradient(135deg,#1e1b4b 0%,#312e81 60%,#4338ca 100%);
                padding:32px 36px 28px;">
      <div style="display:inline-block;background:rgba(255,255,255,0.12);
                  border-radius:20px;padding:4px 12px;margin-bottom:14px;">
        <span style="color:#c7d2fe;font-size:11px;font-weight:600;
                     letter-spacing:1.5px;text-transform:uppercase;">
          📡 Signal Intelligence
        </span>
      </div>
      <h1 style="color:#ffffff;font-size:24px;font-weight:700;line-height:1.3;
                 margin:0 0 6px;">
        {weekday}'s AI Companion Report
      </h1>
      <p style="color:#a5b4fc;font-size:14px;margin:0;">{run_date}</p>
    </div>

    <!-- ── Accent bar ── -->
    <div style="height:4px;background:linear-gradient(90deg,#6366f1,#8b5cf6,#ec4899);"></div>

    <!-- ── Body ── -->
    <div class="content" style="padding:32px 36px;">
      {body_html}
    </div>

    <!-- ── Footer ── -->
    <div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.6;">
              Generated by
              <a href="https://github.com/Sylvan-Wang/ai_companion_signal_agent"
                 style="color:#6366f1;text-decoration:none;font-weight:500;">
                AI Companion Signal Agent
              </a>
              &nbsp;·&nbsp; Automated daily pipeline
            </p>
          </td>
          <td align="right">
            <a href="https://github.com/Sylvan-Wang/ai_companion_signal_agent"
               style="display:inline-block;background:#6366f1;color:#ffffff;
                      font-size:11px;font-weight:600;padding:6px 14px;
                      border-radius:6px;text-decoration:none;letter-spacing:0.3px;">
              View on GitHub →
            </a>
          </td>
        </tr>
      </table>
    </div>

  </div>
</div>
</body>
</html>"""
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
    msg["To"] = cfg["to_header"]   # Shows all recipients in To: header
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Send to all recipients
    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.sendmail(cfg["smtp_user"], cfg["to_list"], msg.as_string())
        n = len(cfg["to_list"])
        print(f"      Email sent → {cfg['to_header']} ({n} recipient{'s' if n > 1 else ''})")
        print(f"      Subject: {subject}")
        return True
    except Exception as e:
        print(f"      Email FAILED: {e}")
        print("      Pipeline continues — email failure is non-fatal.")
        return False
