"""
LINE Notification for ETF Rotation Alerts
==========================================
Sends rotation adjustment alerts via the LINE Messaging API (bot push).

Note: the old "LINE Notify" service was deprecated (2023); this uses the
official-account push endpoint instead.

Environment variables:
    LINE_CHANNEL_ACCESS_TOKEN : Long-lived channel access token
    LINE_USER_ID              : Recipient's LINE userId (Uxxxx...)
                                (optional: LINE_USER_ID_2 for a second recipient)

The recipient must be a friend of the official account.

Usage:
    from notify_line import send_line_alert
    ok = send_line_alert(alerts)
"""

import html
import os
import re
import requests

from notify_telegram import format_alert  # reuse the rotation-alert text format

LINE_MULTICAST_API = "https://api.line.me/v2/bot/message/multicast"
TEXT_LIMIT = 5000  # LINE text message length limit (chars)


def _strip_html(text: str) -> str:
    """LINE 'text' messages don't render HTML; strip tags (e.g. <b>)."""
    return re.sub(r"<[^>]+>", "", text)


def _send_line(text: str) -> bool:
    """Push a text message to all configured LINE user IDs. True if all succeeded.

    Quietly skips (returns False) when the token or recipient is not configured,
    so a missing secret never breaks the workflow."""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("  [WARN] LINE_CHANNEL_ACCESS_TOKEN not set (skip LINE)")
        return False

    user_ids = []
    for var in ("LINE_USER_ID", "LINE_USER_ID_2"):
        v = os.environ.get(var, "")
        if v:
            user_ids.append(v)
    if not user_ids:
        print("  [WARN] No LINE_USER_ID set (skip LINE)")
        return False

    if not text:
        print("  [WARN] LINE text empty (skip)")
        return False

    body = {"to": user_ids, "messages": [{"type": "text", "text": text[:TEXT_LIMIT]}]}
    try:
        r = requests.post(
            LINE_MULTICAST_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  [ERR] LINE API: HTTP {r.status_code}: {r.text}")
            return False
        # Log delivery result so a non-friend (silently dropped by multicast) is visible.
        print(f"  [LINE] {r.text[:300]}")
    except Exception as e:
        print(f"  [ERR] LINE send failed: {e}")
        return False
    return True


def send_line_alert(alerts: list[dict]) -> bool:
    """Send rotation alerts to LINE as plain text (HTML stripped). True on success."""
    if not alerts:
        return True
    message = "\n\n".join(html.unescape(_strip_html(format_alert(a))) for a in alerts)
    return _send_line(message)
