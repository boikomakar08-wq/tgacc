"""Безопасная очередь рассылки с учётом лимитов Telegram и пометкой заблокировавших бота."""
import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import config
import db
import ui
from texts import t
from ui import btn

log = logging.getLogger(__name__)
_running = False


def is_running() -> bool:
    return _running


async def mass_send(users, send_one):
    sent = failed = 0
    blocked = []
    for u in users:
        try:
            await send_one(u)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await send_one(u)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            failed += 1
            blocked.append(u["id"])
        except TelegramBadRequest as e:
            failed += 1
            if any(x in str(e).lower() for x in ("chat not found", "deactivated", "user is deleted")):
                blocked.append(u["id"])
        except Exception:
            log.exception("broadcast send failed")
            failed += 1
        await asyncio.sleep(1 / config.BROADCAST_RATE)
    await db.mark_blocked(blocked)
    return sent, failed


async def start(bot, admin_id: int, draft: dict) -> bool:
    """Запускает рассылку фоновой задачей. False — если уже идёт другая."""
    global _running
    if _running:
        return False
    users = await db.broadcast_users()
    bid = await db.create_broadcast(admin_id, draft, len(users))
    _running = True

    async def job():
        global _running
        try:
            need_pc = "{purchase_count}" in (draft.get("text") or "")

            async def send_one(u):
                v = await ui.build_vars(dict(u), pc=need_pc)
                await ui.send_rendered(bot, u["id"], draft, v)

            sent, failed = await mass_send(users, send_one)
            await db.finish_broadcast(bid, sent, failed)
            await bot.send_message(admin_id, f"✅ РАССЫЛКА ЗАВЕРШЕНА\n\nОтправлено:\n{sent}\n\nОшибок:\n{failed}")
        except Exception:
            log.exception("broadcast crashed")
        finally:
            _running = False

    asyncio.create_task(job())
    return True


async def announce_new_product(bot, pid: int):
    """Уведомление о новом товаре всем, у кого включены уведомления."""
    p = await db.get_product(pid)
    if not p or p["status"] != "active":
        return
    users = await db.broadcast_users(only_notify=True)

    async def send_one(u):
        lang = u["lang"]
        v = await ui.build_vars(dict(u), p, pc=False)
        rows = [[btn(t(lang, "btn_view_product"), f"p:{pid}:n")]]
        await ui.send_rendered(bot, u["id"], await ui.get_msg("new_product", lang), v, rows, ui.product_media(p))

    await mass_send(users, send_one)
