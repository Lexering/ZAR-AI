"""Send an alert with whatever this ZAR has (newer notify.alert, older versions, or Telegram)."""


def alert(text: str, important: bool = False, key: str | None = None, cooldown_s: float = 900):
    try:
        from actions import notify
        try:
            return notify.alert(text, important=important, key=key, cooldown_s=cooldown_s)
        except TypeError:
            return notify.alert(text)
    except Exception:
        pass
    try:
        from actions import telegram_link
        telegram_link.send(text)
    except Exception:
        print(f"[ZAR] {text}")
