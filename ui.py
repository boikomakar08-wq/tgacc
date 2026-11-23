"""Отправка сообщений: шаблоны + entities (Premium Emoji), медиа, inline-кнопки, переменные."""
import datetime
import logging
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           LinkPreviewOptions, MessageEntity)

import config
import db
import texts
import tpl

log = logging.getLogger(__name__)
LP = LinkPreviewOptions(is_disabled=True)


def btn(text, cb=None, url=None):
    if url is not None:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=cb)


def nav(prefix, page, pages):
    """Ряд пагинации: callback = '{prefix}:{page}'."""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", f"{prefix}:{page - 1}"))
    row.append(btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        row.append(btn("▶️", f"{prefix}:{page + 1}"))
    return [row]


async def support_url() -> str:
    s = await db.settings()
    return "https://t.me/" + s["support_username"].lstrip("@")


def product_label(p, lang="ru") -> str:
    icon = texts.BADGE_ICON.get(p["badge"], "🔹")
    if p.get("status") == "sold" or p.get("status") == "deleted":
        icon = "🚫"
    elif p.get("busy"):
        icon = "⏳"
    suffix = f" · ⭐{p['price']}"
    main = f"{icon} {p['short_desc'] or p['title']}"
    if len(main) + len(suffix) > 64:
        main = main[:64 - len(suffix) - 1] + "…"
    return main + suffix


def _fmt_dt(d):
    if not d:
        return "", ""
    local = d.astimezone(ZoneInfo(config.TIMEZONE))
    return local.strftime("%d.%m.%Y"), local.strftime("%H:%M")


async def build_vars(user=None, product=None, purchase=None, price=None, pc=True, **extra) -> dict:
    s = await db.settings()
    sup = s["support_username"].lstrip("@")
    v = {"shop_name": s["shop_name"], "support_username": "@" + sup, "support_link": f"https://t.me/{sup}"}
    if user:
        fn, ln = user.get("first_name") or "", user.get("last_name") or ""
        v.update(
            username=("@" + user["username"]) if user.get("username") else "—",
            user_id=user["id"], first_name=fn, last_name=ln, full_name=f"{fn} {ln}".strip(),
            balance=user["balance"], total_deposited=user["total_deposited"], total_spent=user["total_spent"])
        if pc:
            v["purchase_count"] = await db.purchase_count(user["id"])
    if product:
        v.update(product_name=product["title"], product_short_description=product["short_desc"],
                 product_description=product["description"], price=product["price"],
                 category=product.get("category_name") or "—", product_data=product["data"])
    if purchase:
        d, tm = _fmt_dt(purchase["created_at"])
        v.update(purchase_id=purchase["id"], date=d, time=tm, price=purchase["price"],
                 product_name=purchase["product_title"], product_data=purchase["product_data"])
    if price is not None:
        v["price"] = price
    v.update(extra)
    return v


async def sample_vars(user) -> dict:
    p = {"title": "Telegram Account — RU", "short_desc": "Аккаунт RU", "description": "Подробное описание товара",
         "price": 450, "category_name": "Telegram", "data": "Логин: +380000000000\nПароль: example"}
    pur = {"id": 1234, "created_at": datetime.datetime.now(datetime.timezone.utc), "price": 450,
           "product_title": p["title"], "product_data": p["data"]}
    return await build_vars(user, p, pur, error="Пример текста ошибки", ticket_id=77, amount=100,
                             state="🔔 включены")


async def get_msg(key: str, lang: str) -> dict:
    row = await db.get_message(key, lang)
    if row and (row["text"] or row["photo_id"] or row["gif_id"]):
        return {"text": row["text"] or "", "entities": row["entities"], "photo_id": row["photo_id"],
                "gif_id": row["gif_id"], "buttons": row["buttons"] or []}
    d = texts.DEFAULTS.get(lang, texts.DEFAULTS["ru"])
    return {"text": d[key], "entities": None, "photo_id": None, "gif_id": None, "buttons": []}


def _to_entities(ents):
    return [MessageEntity(**e) for e in ents] if ents else None


async def send_rendered(bot, chat_id, m: dict, vars_: dict, rows=None, media=None, edit=None):
    """Отправляет (или редактирует) сообщение. Сохраняет entities, включая Premium Emoji.
    Если Telegram отклонил custom emoji — повторяет отправку с обычными emoji (fallback)."""
    text, ents = tpl.render(m.get("text"), m.get("entities"), vars_)
    custom = []
    for row in m.get("buttons") or []:
        r = [InlineKeyboardButton(text=tpl.render_plain(b["text"], vars_), url=tpl.render_plain(b["url"], vars_))
             for b in row if b.get("text") and b.get("url")]
        if r:
            custom.append(r)
    all_rows = (rows or []) + custom
    markup = InlineKeyboardMarkup(inline_keyboard=all_rows) if all_rows else None

    kind = fid = None
    if media:
        kind, fid = media
    elif m.get("gif_id"):
        kind, fid = "animation", m["gif_id"]
    elif m.get("photo_id"):
        kind, fid = "photo", m["photo_id"]
    if not kind and not text:
        text = "…"

    variants = [ents, tpl.strip_custom(ents), None]

    if edit is not None and not kind and edit.text is not None:
        for e in variants:
            try:
                return await edit.edit_text(text, entities=_to_entities(e), reply_markup=markup, link_preview_options=LP)
            except TelegramBadRequest as ex:
                if "not modified" in str(ex):
                    return edit
        # не получилось отредактировать — отправим заново
    if edit is not None:
        try:
            await edit.delete()
        except Exception:
            pass

    async def attempt(e):
        en = _to_entities(e)
        if kind and len(text) <= 1024:
            fn = bot.send_animation if kind == "animation" else bot.send_photo
            return await fn(chat_id, fid, caption=text or None, caption_entities=en, reply_markup=markup)
        if kind:
            fn = bot.send_animation if kind == "animation" else bot.send_photo
            await fn(chat_id, fid)
        return await bot.send_message(chat_id, text, entities=en, reply_markup=markup, link_preview_options=LP)

    last_exc = None
    for e in variants:
        try:
            return await attempt(e)
        except TelegramBadRequest as ex:
            last_exc = ex
            log.warning("send fallback: %s", ex)
    raise last_exc


def _target(event):
    if isinstance(event, CallbackQuery):
        return event.bot, event.message.chat.id, event.message
    return event.bot, event.chat.id, None


async def show(event, key, lang, vars_, rows=None, media=None):
    bot, cid, edit = _target(event)
    return await send_rendered(bot, cid, await get_msg(key, lang), vars_, rows, media, edit)


async def show_text(event, text, rows=None, vars_=None):
    bot, cid, edit = _target(event)
    return await send_rendered(bot, cid, {"text": text}, vars_ or {}, rows, None, edit)


def product_media(p):
    if p.get("gif_id"):
        return ("animation", p["gif_id"])
    if p.get("photo_id"):
        return ("photo", p["photo_id"])
    return None
