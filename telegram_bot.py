"""
Crypto Observatory V1 — telegram_bot.py
إرسال التقارير عبر تيليجرام (مجاني، بلا حد).

الإعداد: TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID كمتغيّرات بيئة.
إن لم تُضبط، يطبع التقرير محلياً فقط (لا يُسقط التشغيل).
"""

from __future__ import annotations

import os

import requests

_TIMEOUT = 20


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def tg_send(text: str, parse_mode: str = "Markdown") -> bool:
    """يرسل رسالة. يُرجع True عند النجاح، False إن لم يُضبط أو فشل."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("[تيليجرام] غير مُهيّأ — يُطبع محلياً فقط.")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[تيليجرام] فشل الإرسال: {e}")
        return False
