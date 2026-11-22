from aiogram.filters import BaseFilter

import config


class IsAdmin(BaseFilter):
    """Проверка прав на сервере: только Telegram ID из ADMIN_IDS."""

    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and u.id in config.ADMIN_IDS)
