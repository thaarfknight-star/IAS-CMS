"""(2.0.61-beta) منطق خالص تشخیص قطع/وصل مجدد تصویر دوربین.

بدون هیچ وابستگی به Qt تا هم در main.py استفاده شود و هم مستقیم تست شود.

مدل وضعیت استریم (camera_stream.py):
- "connected": استریم باز و در حال پخش است
- "reconnecting": اتصال قطع شده و تلاش خودکار برای وصل مجدد در جریان است
"""

# کول‌داون ضداسپم هشدار قطع تصویر برای هر دوربین (ثانیه)
COOLDOWN_SECONDS = 300


def transition(prev_state, state):
    """گذار وضعیت استریم -> "lost" | "recovered" | None.

    - connected -> reconnecting یعنی تصویری که وصل بود واقعاً قطع شده ("lost")
    - reconnecting -> connected یعنی وصل مجدد ("recovered")
    - بقیه‌ی گذارها (از جمله تلاش اولیه‌ی ناموفق بدون اتصال قبلی) هشدار ندارند.
    """
    if state == "reconnecting" and prev_state == "connected":
        return "lost"
    if state == "connected" and prev_state == "reconnecting":
        return "recovered"
    return None


def cooldown_ok(last_alert_ts, now, cooldown=COOLDOWN_SECONDS):
    """آیا از آخرین هشدار این دوربین به‌اندازه‌ی کول‌داون گذشته است؟"""
    try:
        return (now - float(last_alert_ts or 0)) >= cooldown
    except (TypeError, ValueError):
        return True
