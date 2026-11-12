"""Безопасная диагностика по умолчанию; интерфейс запускается только --bot."""

import argparse
import asyncio
import logging

from app.bot import texts
from app.check import main as check_main
from app.utils.runtime import run_services

__all__ = ["main", "run_services"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Единый бот")
    parser.add_argument("--bot", action="store_true", help="Явно запустить интерфейс, без миграций")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.bot:
        from app.bot.runtime import run_interface
        from app.config import get_settings

        try:
            settings = get_settings()
            logging.basicConfig(level=settings.log_level)
            asyncio.run(run_interface(settings))
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as error:
            logging.error(texts.UI_ERROR_LOG, type(error).__name__)
            print(texts.APP_FAILED)
            if isinstance(error, (ValueError, RuntimeError)):
                print(str(error))
            return 1
    check_args = []
    if args.online:
        check_args.append("--online")
    if args.json:
        check_args.append("--json")
    code = check_main(check_args)
    print(texts.APP_READY if code == 0 else texts.APP_FAILED)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
