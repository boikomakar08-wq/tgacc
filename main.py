"""Точка входа. Запуск: python main.py"""
import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery

import admin
import admin_content
import config
import db
import payments
import texts
import user as user_handlers


class UserMiddleware(BaseMiddleware):
    """Регистрирует/обновляет пользователя и подставляет user и lang в хендлеры."""

    async def __call__(self, handler, event, data):
        tu = data.get("event_from_user")
        if tu and not tu.is_bot:
            u, is_new = await db.upsert_user(tu)
            data["user"] = u
            data["is_new_user"] = is_new
            data["lang"] = u["lang"] if u["lang"] in texts.L else "ru"
            if isinstance(event, CallbackQuery) and event.data and not event.data.startswith("a:") and event.data != "noop":
                state = data.get("state")
                if state:
                    await state.clear()      # любая навигация пользователя сбрасывает незавершённый ввод
        return await handler(event, data)


async def cleanup_loop():
    """Освобождает просроченные резервы (запросы и так учитывают reserved_until)."""
    while True:
        try:
            await db.release_expired()
        except Exception:
            logging.exception("cleanup failed")
        await asyncio.sleep(60)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await db.init()
    await db.refresh_labels()
    bot = Bot(config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())
    dp.include_router(admin.router)
    dp.include_router(admin_content.router)
    dp.include_router(payments.router)
    dp.include_router(user_handlers.router)

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню"),
                               BotCommand(command="support", description="Поддержка"),
                               BotCommand(command="paysupport", description="Помощь с платежами")])
    cleanup = asyncio.create_task(cleanup_loop())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
