#!/usr/bin/env python3
"""
ShiftMatch — Email Notifier
Sends beautifully formatted HTML emails with top 5 shift matches.
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"email_config.json not found at {CONFIG_PATH}. "
            "Copy email_config.example.json and fill in your SMTP settings."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_email_html(top_matches: list, user_email: str) -> str:
    """Build a beautiful HTML email with the top 5 shift matches."""
    rows = ""
    for idx, item in enumerate(top_matches[:5]):
        shift = item["shift"]
        score = item["score"]
        breakdown = item.get("breakdown", {})

        # Score color
        if score >= 90:
            badge_bg = "#e17055"
            emoji = "&#x1F525;"
        elif score >= 75:
            badge_bg = "#6C5CE7"
            emoji = "&#x1F49C;"
        else:
            badge_bg = "#636e72"
            emoji = "&#x2B50;"

        breakdown_lines = "".join(
            f'<div style="font-size:13px;color:#636e72;padding:2px 0;">{v}</div>'
            for v in breakdown.values()
        )

        signup_btn = ""
        if shift.get("signup_url"):
            signup_btn = (
                f'<a href="{shift["signup_url"]}" style="display:inline-block;margin-top:10px;'
                f'padding:10px 24px;background:linear-gradient(135deg,#6C5CE7,#0984E3);'
                f'color:#fff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">'
                f'Claim This Shift</a>'
            )

        rows += f"""
        <tr>
            <td style="padding:20px 24px;border-bottom:1px solid #f0f0f0;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="vertical-align:top;width:44px;">
                            <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#6C5CE7,#0984E3);
                                color:#fff;text-align:center;line-height:36px;font-weight:700;font-size:14px;">
                                {idx + 1}
                            </div>
                        </td>
                        <td style="vertical-align:top;padding-left:12px;">
                            <div style="font-weight:700;font-size:16px;color:#2d3436;">{shift.get("committee", "")}</div>
                            <div style="font-size:14px;color:#636e72;margin-top:4px;">
                                {shift.get("day", "")} &middot; {shift.get("time_raw", shift.get("time_slot", ""))} &middot; {shift.get("slots", "?")} slots
                            </div>
                            <div style="font-size:13px;color:#2d3436;margin-top:8px;line-height:1.5;">
                                {shift.get("description", "")}
                            </div>
                            <div style="margin-top:8px;">{breakdown_lines}</div>
                            {signup_btn}
                        </td>
                        <td style="vertical-align:top;text-align:right;width:60px;">
                            <span style="display:inline-block;padding:4px 12px;border-radius:20px;
                                background:{badge_bg};color:#fff;font-weight:700;font-size:14px;">
                                {emoji} {score}%
                            </span>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8f9fe;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fe;padding:24px 0;">
            <tr><td align="center">
                <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#6C5CE7,#0984E3);padding:28px 24px;text-align:center;">
                            <div style="font-size:1.4rem;letter-spacing:4px;margin-bottom:6px;">&#x1FAD3;&#x1FAD8;&#x1F9C0;&#x1F34C;</div>
                            <div style="font-family:Georgia,serif;font-size:24px;font-weight:700;color:#fff;">ShiftMatch</div>
                            <div style="font-size:14px;color:rgba(255,255,255,0.85);margin-top:4px;">Your daily top 5 shift matches</div>
                        </td>
                    </tr>
                    <!-- Matches -->
                    {rows}
                    <!-- Footer -->
                    <tr>
                        <td style="padding:20px 24px;text-align:center;font-size:12px;color:#b2bec3;">
                            Sent to {user_email} &middot;
                            <a href="#" style="color:#6C5CE7;">Unsubscribe</a>
                        </td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </body>
    </html>
    """


def send_email(smtp_config: dict, to_email: str, subject: str, html_body: str) -> bool:
    """Send an HTML email via SMTP. Returns True on success."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.get("username", "shiftmatch@gmail.com")
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_config["host"], smtp_config["port"]) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_config["username"], smtp_config["password"])
            server.sendmail(msg["From"], [to_email], msg.as_string())
        return True
    except Exception as exc:
        print(f"  [ERROR] Failed to send to {to_email}: {exc}")
        return False


def send_daily_matches(config: dict):
    """For each user in config, crawl shifts, score, and email top 5."""
    # Import here to avoid circular dependency
    from app import ShiftMatchCrawler, ShiftMatcher

    smtp = config.get("smtp", {})
    if not smtp.get("username") or not smtp.get("password"):
        print("[WARN] SMTP credentials not configured. Skipping email send.")
        return

    for user in config.get("users", []):
        email = user.get("email")
        member = user.get("member_number")
        password = user.get("password")
        prefs = user.get("preferences", {})

        if not email or not member:
            continue

        print(f"  Processing {email}...")

        crawler = ShiftMatchCrawler()
        login_result = crawler.login(member, password)

        if not login_result["success"]:
            print(f"  [WARN] Login failed for {email}: {login_result['message']}")
            continue

        shift_result = crawler.get_shifts()
        shifts = shift_result.get("parsed_shifts", [])
        if not shifts:
            print(f"  [WARN] No shifts found for {email}")
            continue

        matcher = ShiftMatcher(prefs)
        top5 = matcher.top(shifts, n=5)

        html = build_email_html(top5, email)
        ok = send_email(smtp, email, "ShiftMatch: Your Top 5 Shifts Today", html)
        print(f"  {'Sent' if ok else 'FAILED'}: {email}")


if __name__ == "__main__":
    print("ShiftMatch Email Notifier — manual test run")
    cfg = load_config()
    send_daily_matches(cfg)
    print("Done.")
