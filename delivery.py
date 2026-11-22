"""Выдача товара после успешной покупки + уведомление администраторов."""
import logging

import config
import db
import ui
from texts import t
from ui import btn

log = logging.getLogger(__name__)


async def notify_admins(bot, text, rows=None):
    from aiogram.types import InlineKeyboardMarkup
    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    for aid in config.ADMIN_IDS:
        try:
            await bot.send_message(aid, text, reply_markup=markup)
        except Exception as e:  # админ мог не запускать бота
            log.warning("admin notify failed %s: %s", aid, e)


async def deliver(bot, uid: int, pur: dict):
    user = await db.get_user(uid)
    lang = user["lang"]
    product = await db.get_product(pur["product_id"])
    v = await ui.build_vars(user, product, pur)
    rows = [
        [btn(t(lang, "btn_purchases"), "mybuys:0")],
        [btn(t(lang, "btn_support"), url=await ui.support_url())],
        [btn(t(lang, "btn_catalog"), "cat")],
    ]
    await ui.send_rendered(bot, uid, await ui.get_msg("purchase_success", lang), v, rows)
    who = f"@{user['username']}" if user.get("username") else "—"
    await notify_admins(
        bot,
        f"💰 ПРОДАЖА #{pur['id']}\n\nТовар: {pur['product_title']}\nЦена: ⭐{pur['price']}\n"
        f"Покупатель: {who} (ID {uid})\nОплата: {'Stars' if pur['method'] == 'stars' else 'баланс'}")
