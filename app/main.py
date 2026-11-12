"""Точка входа Этапа 1: безопасная диагностика и публичный супервизор сервисов."""

from app.bot import texts
from app.check import main as check_main
from app.utils.runtime import run_services

__all__ = ["main", "run_services"]


def main(argv: list[str] | None = None) -> int:
    code = check_main(argv)
    print(texts.APP_READY if code == 0 else texts.APP_FAILED)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
