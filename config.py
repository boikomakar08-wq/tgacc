"""Конфигурация. Все секреты читаются только из переменных окружения."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv необязателен
    pass


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} обязательна")
    return value


BOT_TOKEN = _required("BOT_TOKEN")
DATABASE_URL = _required("DATABASE_URL")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

TIMEZONE = os.getenv("TIMEZONE", "UTC")
DEFAULT_SUPPORT = os.getenv("DEFAULT_SUPPORT", "@cryptcursed")
DEFAULT_SHOP_NAME = os.getenv("DEFAULT_SHOP_NAME", "Digital Shop")

MAX_TOPUP = 10000          # максимум Stars за одно пополнение
PER_PAGE = 8               # элементов на страницу
BROADCAST_RATE = 20        # сообщений в секунду (лимит Telegram ~30/с)
