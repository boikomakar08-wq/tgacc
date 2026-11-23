"""Пользовательская часть: меню, каталог, карточки, покупка, баланс, избранное, язык, промокоды."""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message

import config
import db
import delivery
import tpl
import ui
from texts import LANGS, t
from ui import btn, nav, show, show_text

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")


class TopupCustom(StatesGroup):
    amount = State()


class PromoInput(StatesGroup):
    code = State()


# ───────────────────────── меню ─────────────────────────
async def main_rows(lang):
    return [
        [btn(t(lang, "btn_catalog"), "cat")],
        [btn(t(lang, "btn_purchases"), "mybuys:0"), btn(t(lang, "btn_balance"), "balance")],
        [btn(t(lang, "btn_topup"), "topup")],
        [btn(t(lang, "btn_favs"), "favs:0"), btn(t(lang, "btn_notif"), "notif")],
        [btn(t(lang, "btn_lang"), "lang"), btn(t(lang, "btn_support"), url=await ui.support_url())],
    ]


async def send_menu(event, user, lang):
    await show(event, "main_menu", lang, await ui.build_vars(user), await main_rows(lang))


def language_rows(lang="en"):
    return [[btn(t(lang, "btn_welcome_ru"), "setlang:ru"), btn(t(lang, "btn_welcome_uk"), "setlang:uk")],
            [btn(t(lang, "btn_welcome_en"), "setlang:en")],
            [btn(t(lang, "btn_open_menu"), "menu")]]


async def send_welcome(event, user, lang, new_user=False):
    # Для нового пользователя первый экран всегда на английском.
    welcome_lang = "en" if new_user else lang
    if new_user:
        return await show(event, "welcome", welcome_lang, await ui.build_vars(user), language_rows(welcome_lang))
    await show(event, "welcome", welcome_lang, await ui.build_vars(user),
               [[btn(t(welcome_lang, "btn_open_menu"), "menu")]])


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, user, lang, is_new_user=False):
    await state.clear()
    await send_welcome(m, user, lang, is_new_user)


@router.callback_query(F.data.startswith("setlang:"))
async def set_language(cb: CallbackQuery, user, lang):
    new_lang = cb.data.split(":")[1]
    if new_lang not in LANGS:
        return await cb.answer()
    await db.set_lang(user["id"], new_lang)
    user = await db.get_user(user["id"])
    await cb.answer("Язык сохранён")
    await show(cb, "welcome", new_lang, await ui.build_vars(user),
               [[btn(t(welcome_lang, "btn_open_menu"), "menu")]])


@router.message(Command("menu"))
async def menu_cmd(m: Message, state: FSMContext, user, lang):
    await state.clear()
    await send_menu(m, user, lang)


@router.message(Command("support"))
async def support_cmd(m: Message, user, lang):
    rows = [[btn(t(lang, "btn_support"), url=await ui.support_url())], [btn(t(lang, "btn_menu"), "menu")]]
    await show(m, "support", lang, await ui.build_vars(user), rows)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, user, lang):
    await cb.answer()
    await send_menu(cb, user, lang)


# ───────────────────────── каталог ─────────────────────────
@router.callback_query(F.data == "cat")
async def catalog(cb: CallbackQuery, user, lang):
    await cb.answer()
    rows = [[btn(f"📁 {c['name']} ({c['n']})", f"c:{c['id']}:0")] for c in await db.categories_with_counts()]
    n = await db.uncategorized_count()
    if n:
        rows.append([btn(f"🗂 {t(lang, 'uncategorized')} ({n})", "c:0:0")])
    rows.append([btn(t(lang, "btn_back"), "menu")])
    await show(cb, "catalog", lang, await ui.build_vars(user), rows)


@router.callback_query(F.data.startswith("c:"))
async def category(cb: CallbackQuery, user, lang):
    await cb.answer()
    _, cid, page = cb.data.split(":")
    cid = int(cid) or None
    items = await db.products_in(cid, user["id"])
    chunk, page, pages = tpl.paginate(items, int(page), config.PER_PAGE)
    name = (await db.category_name(cid)) if cid else t(lang, "uncategorized")
    if not items:
        text = f"📁 {name}\n━━━━━━━━━━━━━━\n{t(lang, 'cat_empty')}"
    else:
        text = f"📁 {name}\n━━━━━━━━━━━━━━\n{t(lang, 'pick_product')}"
    rows = [[btn(ui.product_label(dict(p)), f"p:{p['id']}:{cid or 0}")] for p in chunk]
    rows += nav(f"c:{cid or 0}", page, pages)
    rows.append([btn(t(lang, "btn_back"), "cat")])
    await show_text(cb, text, rows)


def _back_target(cid: str) -> str:
    if cid == "f":
        return "favs:0"
    if cid == "n":
        return "cat"
    return f"c:{cid}:0"


async def render_product(cb, user, lang, pid, cid):
    p = await db.get_product(pid)
    if not p or p["status"] != "active":
        return await show_text(cb, f"🚫 {t(lang, 'err_gone')}", [[btn(t(lang, "btn_to_catalog"), "cat")]])
    fav = await db.is_fav(user["id"], pid)
    rows = [
        [btn(t(lang, "btn_buy"), f"buy:{pid}")],
        [btn(t(lang, "btn_fav_del" if fav else "btn_fav_add"), f"fav:{pid}:{cid}")],
        [btn(t(lang, "btn_back"), _back_target(cid))],
    ]
    await show(cb, "product_card", lang, await ui.build_vars(user, p), rows, ui.product_media(p))


@router.callback_query(F.data.startswith("p:"))
async def product(cb: CallbackQuery, user, lang):
    await cb.answer()
    _, pid, cid = cb.data.split(":")
    await render_product(cb, user, lang, int(pid), cid)


@router.callback_query(F.data.startswith("fav:"))
async def fav_toggle(cb: CallbackQuery, user, lang):
    _, pid, cid = cb.data.split(":")
    added = await db.toggle_fav(user["id"], int(pid))
    await cb.answer("❤️" if added else "💔")
    await render_product(cb, user, lang, int(pid), cid)


# ───────────────────────── избранное ─────────────────────────
@router.callback_query(F.data.startswith("favs:"))
async def favs(cb: CallbackQuery, user, lang):
    await cb.answer()
    items = await db.list_favs(user["id"])
    if not items:
        return await show_text(cb, t(lang, "favs_empty"), [[btn(t(lang, "btn_to_catalog"), "cat")], [btn(t(lang, "btn_back"), "menu")]])
    chunk, page, pages = tpl.paginate(items, int(cb.data.split(":")[1]), config.PER_PAGE)
    rows = []
    for p in chunk:
        p = dict(p)
        if p["status"] == "active":
            rows.append([btn(ui.product_label(p), f"p:{p['id']}:f")])
        else:
            rows.append([btn(f"🚫 {p['title'][:40]} ({t(lang, 'sold_label')})", f"fr:{p['id']}")])
    rows += nav("favs", page, pages)
    rows.append([btn(t(lang, "btn_back"), "menu")])
    await show(cb, "favs_title", lang, await ui.build_vars(user), rows)


@router.callback_query(F.data.startswith("fr:"))
async def fav_remove(cb: CallbackQuery, user, lang):
    await db.remove_fav(user["id"], int(cb.data.split(":")[1]))
    cb.data = "favs:0"
    await favs(cb, user, lang)


# ───────────────────────── покупка ─────────────────────────
async def fail(cb, user, lang, key):
    v = await ui.build_vars(user, error=t(lang, key))
    await cb.answer()
    await show(cb, "payment_error", lang, v, [[btn(t(lang, "btn_to_catalog"), "cat")], [btn(t(lang, "btn_menu"), "menu")]])


@router.callback_query(F.data.startswith("buy:"))
async def buy_confirm(cb: CallbackQuery, user, lang):
    pid = int(cb.data.split(":")[1])
    p = await db.get_product(pid)
    if not p or p["status"] != "active":
        return await fail(cb, user, lang, "err_gone")
    price, _ = await db.quote(user["id"], pid)
    await cb.answer()
    rows = [[btn(t(lang, "btn_pay_stars", price=price), f"payS:{pid}")]]
    if user["balance"] >= price:
        rows.append([btn(t(lang, "btn_pay_balance", balance=user["balance"]), f"payB:{pid}")])
    rows.append([btn(t(lang, "btn_cancel"), f"p:{pid}:n")])
    await show(cb, "payment", lang, await ui.build_vars(user, p, price=price), rows, ui.product_media(p))


@router.callback_query(F.data.startswith("payS:"))
async def pay_stars(cb: CallbackQuery, user, lang):
    pid = int(cb.data.split(":")[1])
    s = await db.settings()
    p = await db.get_product(pid)
    if not p or p["status"] != "active":
        return await fail(cb, user, lang, "err_gone")
    minutes = int(s["reserve_minutes"])
    if not await db.reserve(pid, user["id"], minutes):
        return await fail(cb, user, lang, "err_busy")
    price, promo_id = await db.quote(user["id"], pid)
    await cb.answer()
    desc = f"{p['short_desc'] or p['title']}\n{t(lang, 'reserved_note', minutes=minutes)}"
    await cb.bot.send_invoice(
        chat_id=user["id"], title=p["title"][:32], description=desc[:255],
        payload=f"buy:{pid}:{promo_id or 0}", provider_token="", currency="XTR",
        prices=[LabeledPrice(label=p["title"][:32], amount=price)])


@router.callback_query(F.data.startswith("payB:"))
async def pay_balance(cb: CallbackQuery, user, lang):
    pid = int(cb.data.split(":")[1])
    status, pur = await db.buy_with_balance(user["id"], pid)
    if status != "ok":
        return await fail(cb, user, lang, {"gone": "err_gone", "busy": "err_busy", "funds": "err_funds"}[status])
    await cb.answer("✅")
    try:
        await cb.message.delete()
    except Exception:
        pass
    await delivery.deliver(cb.bot, user["id"], pur)


# ───────────────────────── мои покупки ─────────────────────────
@router.callback_query(F.data.startswith("mybuys:"))
async def my_purchases(cb: CallbackQuery, user, lang):
    await cb.answer()
    items = await db.list_purchases(user["id"])
    if not items:
        return await show_text(cb, t(lang, "purchases_empty"),
                               [[btn(t(lang, "btn_goto_catalog"), "cat")], [btn(t(lang, "btn_back"), "menu")]])
    chunk, page, pages = tpl.paginate(items, int(cb.data.split(":")[1]), config.PER_PAGE)
    rows = []
    for p in chunk:
        d, _ = ui._fmt_dt(p["created_at"])
        label = f"📦 {p['product_title']} · ⭐{p['price']}"
        tail = f" · {d[:5]}"
        label = label[:64 - len(tail)] + tail
        rows.append([btn(label, f"pv:{p['id']}")])
    rows += nav("mybuys", page, pages)
    rows.append([btn(t(lang, "btn_back"), "menu")])
    await show(cb, "purchases_title", lang, await ui.build_vars(user), rows)


async def _own_purchase(cb, user, pid):
    pur = await db.get_purchase(pid, user["id"])      # проверка владельца
    if not pur:
        await cb.answer("❌", show_alert=True)
    return pur


@router.callback_query(F.data.startswith("pv:"))
async def purchase_card(cb: CallbackQuery, user, lang):
    pur = await _own_purchase(cb, user, int(cb.data.split(":")[1]))
    if not pur:
        return
    await cb.answer()
    rows = [
        [btn(t(lang, "btn_show_data"), f"pd:{pur['id']}")],
        [btn(t(lang, "btn_problem"), f"pi:{pur['id']}")],
        [btn(t(lang, "btn_to_catalog"), "cat"), btn(t(lang, "btn_support"), url=await ui.support_url())],
        [btn(t(lang, "btn_back"), "mybuys:0")],
    ]
    v = await ui.build_vars(user, purchase=pur)
    await show(cb, "purchase_card", lang, v, rows)


@router.callback_query(F.data.startswith("pd:"))
async def purchase_data(cb: CallbackQuery, user, lang):
    pur = await _own_purchase(cb, user, int(cb.data.split(":")[1]))
    if not pur:
        return
    await cb.answer()
    v = await ui.build_vars(user, purchase=pur)
    await show(cb, "product_data", lang, v, [[btn(t(lang, "btn_back"), f"pv:{pur['id']}")]])


@router.callback_query(F.data.startswith("pi:"))
async def purchase_issue(cb: CallbackQuery, user, lang):
    pur = await _own_purchase(cb, user, int(cb.data.split(":")[1]))
    if not pur:
        return
    await cb.answer()
    tid = await db.create_ticket(user, pur)
    d, tm = ui._fmt_dt(pur["created_at"])
    uname = f"@{user['username']}" if user.get("username") else "—"
    await delivery.notify_admins(
        cb.bot,
        f"🆘 ОБРАЩЕНИЕ #{tid}\n\nПользователь: {uname}\nTelegram ID: {user['id']}\n"
        f"ID покупки: #{pur['id']}\nТовар: {pur['product_title']}\nЦена: ⭐{pur['price']}\nДата покупки: {d} {tm}",
        [[btn("👤 Профиль", url=f"tg://user?id={user['id']}")]])
    v = await ui.build_vars(user, purchase=pur, ticket_id=tid)
    rows = [[btn(t(lang, "btn_support"), url=await ui.support_url())], [btn(t(lang, "btn_back"), f"pv:{pur['id']}")]]
    await show(cb, "problem", lang, v, rows)


# ───────────────────────── баланс / пополнение ─────────────────────────
@router.callback_query(F.data == "balance")
async def balance(cb: CallbackQuery, user, lang):
    await cb.answer()
    rows = [
        [btn(t(lang, "btn_topup_short"), "topup"), btn(t(lang, "btn_promo"), "promo")],
        [btn(t(lang, "btn_history"), "mybuys:0")],
        [btn(t(lang, "btn_back"), "menu")],
    ]
    await show(cb, "balance_page", lang, await ui.build_vars(user), rows)


@router.callback_query(F.data == "topup")
async def topup(cb: CallbackQuery, user, lang):
    await cb.answer()
    rows = [
        [btn("⭐ 50", "top:50"), btn("⭐ 100", "top:100"), btn("⭐ 500", "top:500")],
        [btn(t(lang, "btn_custom"), "top_custom")],
        [btn(t(lang, "btn_promo"), "promo")],
        [btn(t(lang, "btn_back"), "menu")],
    ]
    await show(cb, "topup", lang, await ui.build_vars(user), rows)


async def _bonus_for(amount):
    s = await db.settings()
    if s["bonus_enabled"] == "1" and int(s["bonus_per"]) > 0:
        return (amount // int(s["bonus_per"])) * int(s["bonus_amount"])
    return 0


async def _topup_confirm(event, lang, amount):
    bonus = await _bonus_for(amount)
    bonus_line = t(lang, "bonus_line", bonus=bonus) if bonus else ""
    rows = [[btn(t(lang, "btn_topup_pay", amount=amount), f"topay:{amount}")], [btn(t(lang, "btn_back"), "topup")]]
    await show_text(event, t(lang, "topup_confirm", amount=amount, bonus_line=bonus_line), rows)


@router.callback_query(F.data.startswith("top:"))
async def topup_amount(cb: CallbackQuery, lang):
    await cb.answer()
    await _topup_confirm(cb, lang, int(cb.data.split(":")[1]))


@router.callback_query(F.data == "top_custom")
async def topup_custom(cb: CallbackQuery, state: FSMContext, lang):
    await cb.answer()
    await state.set_state(TopupCustom.amount)
    await show_text(cb, t(lang, "topup_custom_prompt", max=config.MAX_TOPUP), [[btn(t(lang, "btn_back"), "topup")]])


@router.message(TopupCustom.amount)
async def topup_custom_value(m: Message, state: FSMContext, lang):
    txt = (m.text or "").strip()
    if not txt.isdigit() or not (1 <= int(txt) <= config.MAX_TOPUP):
        return await m.answer(t(lang, "topup_bad_amount", max=config.MAX_TOPUP))
    await state.clear()
    await _topup_confirm(m, lang, int(txt))


@router.callback_query(F.data.startswith("topay:"))
async def topup_pay(cb: CallbackQuery, lang):
    amount = int(cb.data.split(":")[1])
    if not (1 <= amount <= config.MAX_TOPUP):
        return await cb.answer("❌", show_alert=True)
    await cb.answer()
    bonus = await _bonus_for(amount)
    bp = t(lang, "bonus_part", bonus=bonus) if bonus else ""
    await cb.bot.send_invoice(
        chat_id=cb.from_user.id, title=t(lang, "invoice_topup_title"),
        description=t(lang, "invoice_topup_desc", amount=amount, bonus_part=bp),
        payload=f"topup:{amount}", provider_token="", currency="XTR",
        prices=[LabeledPrice(label=t(lang, "invoice_topup_title"), amount=amount)])


# ───────────────────────── промокоды ─────────────────────────
@router.message(Command("promo"))
@router.callback_query(F.data == "promo")
async def promo_prompt(event, state: FSMContext, user, lang):
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.set_state(PromoInput.code)
    await show(event, "promo", lang, await ui.build_vars(user), [[btn(t(lang, "btn_back"), "balance")]])


@router.message(PromoInput.code)
async def promo_code(m: Message, state: FSMContext, user, lang):
    if not m.text:
        return
    status, p = await db.apply_promo(user["id"], m.text)
    if status in ("bonus", "discount"):
        await state.clear()
        text = t(lang, "promo_ok_bonus" if status == "bonus" else "promo_ok_discount", value=p["value"])
        return await show_text(m, text, [[btn(t(lang, "btn_balance"), "balance")], [btn(t(lang, "btn_catalog"), "cat")]])
    await m.answer(t(lang, "promo_" + status))


# ───────────────────────── уведомления / язык ─────────────────────────
async def _notif_page(cb, user, lang):
    on = user["notify_new"]
    rows = [[btn(("✅ " if on else "") + t(lang, "btn_notif_on"), "notif_t:1"),
             btn(("✅ " if not on else "") + t(lang, "btn_notif_off"), "notif_t:0")],
            [btn(t(lang, "btn_back"), "menu")]]
    await show(cb, "notif_page", lang, await ui.build_vars(user, state=t(lang, "state_on" if on else "state_off")), rows)


@router.callback_query(F.data == "notif")
async def notif(cb: CallbackQuery, user, lang):
    await cb.answer()
    await _notif_page(cb, user, lang)


@router.callback_query(F.data.startswith("notif_t:"))
async def notif_set(cb: CallbackQuery, user, lang):
    want = cb.data.endswith("1")
    if user["notify_new"] != want:
        await db.toggle_notify(user["id"])
    await cb.answer("✅")
    await _notif_page(cb, await db.get_user(user["id"]), lang)


@router.callback_query(F.data == "lang")
async def lang_page(cb: CallbackQuery, lang):
    await cb.answer()
    rows = [[btn(name, f"sl:{code}")] for code, name in LANGS.items()] + [[btn(t(lang, "btn_back"), "menu")]]
    await show_text(cb, t(lang, "lang_title"), rows)


@router.callback_query(F.data.startswith("sl:"))
async def set_lang(cb: CallbackQuery, user):
    code = cb.data.split(":")[1]
    if code not in LANGS:
        return await cb.answer()
    await db.set_lang(user["id"], code)
    await cb.answer("✅")
    fresh = await db.get_user(user["id"])
    await send_menu(cb, fresh, code)
