import hmac
import json
import os
import secrets
import time

from bot import Telegram, TelegramError
from monitor import RECIPIENT_FILE


def pair():
    telegram = Telegram(os.environ.pop("TELEGRAM_BOT_TOKEN"))
    username = telegram.call("getMe")["username"]
    code = secrets.token_urlsafe(24)
    print(f"https://t.me/{username}?start={code}", flush=True)
    expires = time.monotonic() + 900
    offset = 0
    while time.monotonic() < expires:
        try:
            updates = telegram.call("getUpdates", offset=offset, timeout=20, allowed_updates=["message"])
        except TelegramError as error:
            if error.code not in {0, 429, 500, 502, 503, 504}:
                raise
            time.sleep(max(3, error.retry_after))
            continue
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat = message.get("chat", {})
            command = message.get("text", "").encode("utf-8")
            if chat.get("type") != "private" or not hmac.compare_digest(command, f"/start {code}".encode()):
                continue
            if time.monotonic() >= expires:
                return
            chat_id = chat["id"]
            temporary = RECIPIENT_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(chat_id), encoding="utf-8")
            temporary.replace(RECIPIENT_FILE)
            telegram.send(chat_id, "Аккаунт подключён. Новые заявки будут приходить сюда.")
            print("Получатель подтверждён.", flush=True)
            return
    print("Срок действия ссылки истёк.", flush=True)


if __name__ == "__main__":
    try:
        pair()
    except TelegramError as error:
        raise SystemExit(f"Не удалось привязать получателя. Код Telegram: {error.code}.") from None
