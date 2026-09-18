"""
Telegram Notification for ETF Rotation Alerts
==============================================
Sends formatted alert messages via Telegram Bot API.

Environment variables:
    TELEGRAM_BOT_TOKEN : Bot token (from @BotFather)
    TELEGRAM_CHAT_ID   : Chat/user ID to send to

Usage:
    from notify_telegram import send_rotation_alert
    ok = send_rotation_alert(alerts)
"""

import os
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send_message(text: str) -> bool:
    """Send a message to Telegram. Returns True on success."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("  [WARN] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False

    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            print(f"  [ERR] Telegram API: {data}")
            return False
        return True
    except Exception as e:
        print(f"  [ERR] Telegram send failed: {e}")
        return False


def format_alert(alert: dict) -> str:
    """Format a single alert into HTML message text."""
    regime_emoji = "🔴" if alert["regime"] == "bull" else "🟢"
    regime_cn = "多頭" if alert["regime"] == "bull" else "空頭"

    if alert["delta"] > 0:
        action_text = (
            f"⬆️ <b>加碼股票</b> {alert['prev_pct']}% → {alert['new_pct']}% "
            f"(+{alert['delta']}%)"
        )
        trade_text = (
            f"操作: 買入 {alert['equity_code']} {alert['delta']}% / "
            f"賣出 {alert['bond_code']} {alert['delta']}%"
        )
    else:
        action_text = (
            f"⬇️ <b>減碼股票</b> {alert['prev_pct']}% → {alert['new_pct']}% "
            f"({alert['delta']}%)"
        )
        trade_text = (
            f"操作: 賣出 {alert['equity_code']} {abs(alert['delta'])}% / "
            f"買入 {alert['bond_code']} {abs(alert['delta'])}%"
        )

    return (
        f"📊 <b>ETF 輪動訊號</b> | {alert['time']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {alert['label']}\n"
        f"{regime_emoji} {regime_cn} | 1d動能: {alert['momentum']:+.2f}%\n"
        f"{action_text}\n"
        f"💰 {trade_text}\n"
        f"💵 {alert['equity_code']} 收: ${alert['price']:.2f}\n"
        f"📝 {alert['reason']}"
    )


def send_rotation_alert(alerts: list[dict]) -> bool:
    """Send rotation alerts via Telegram. Returns True if successful."""
    if not alerts:
        return True

    # Combine all alerts into one message
    parts = []
    for a in alerts:
        parts.append(format_alert(a))
    message = "\n\n".join(parts)

    return _send_message(message)
