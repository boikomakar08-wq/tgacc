"""Админ-панель /adm: товары, добавление, цена, удаление, статистика, пользователи,
балансы, категории, промокоды, настройки. Все хендлеры доступны только ADMIN_IDS."""
import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import broadcast
import config
import db
import texts
import tpl
import ui
from filters import IsAdmin
from ui import btn, nav, show_text

log = logging.getLogger(__name__)
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def A(text, data):
    return btn(text, "a:" + data)


HOME = [
    [A("📦 Товары", "ps:0"), A("➕ Добавить товар", "ap:new")],
    [A("📊 Статистика", "st"), A("👥 Пользователи", "us")],
    [A("💰 Балансы", "bal"), A("📢 Рассылка", "bd")],
    [A("🎟️ Промокоды", "promo"), A("📝 Сообщения", "msgs")],
    [A("⚙️ Настройки", "set")],
]
HOME_ROW = [A("🏠 Админ-панель", "home")]


class AP(StatesGroup):
    step = State()


class EF(StatesGroup):
    value = State()


class PC(StatesGroup):
    price = State()


class US(StatesGroup):
    query = State()


class BA(StatesGroup):
    amount = State()


class PN(StatesGroup):
    code = State()
    kind = State()
    value = State()
    uses = State()
    days = State()


class ST(StatesGroup):
    value = State()


class CN(StatesGroup):
    name = State()


# ───────────────────────── главная ─────────────────────────
@router.message(Command("adm"))
async def adm(m: Message, state: FSMContext):
    await state.clear()
    await show_text(m, "⚙️ ADMIN PANEL\n━━━━━━━━━━━━━━", HOME)


@router.callback_query(F.data == "a:home")
async def home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await show_text(cb, "⚙️ ADMIN PANEL\n━━━━━━━━━━━━━━", HOME)


# ───────────────────────── товары ─────────────────────────
def p_text(p) -> str:
    st = {"active": "🟢 В продаже", "sold": "✅ Продан", "deleted": "🗑 Удалён"}[p["status"]]
    return (f"📦 {p['title']}\n\nЦена:\n⭐{p['price']}\n\nКатегория:\n{p.get('category_name') or '—'}\n\n"
            f"Статус:\n{st} · {texts.BADGE_NAME[p['badge']]}\n\nID: {p['id']}")


@router.callback_query(F.data.startswith("a:ps:"))
async def products_list(cb: CallbackQuery):
    await cb.answer()
    items = await db.admin_products("active")
    chunk, page, pages = tpl.paginate(items, int(cb.data.split(":")[2]), config.PER_PAGE)
    rows = [[A(f"{texts.BADGE_ICON[p['badge']]} #{p['id']} {p['title'][:30]} · ⭐{p['price']}", f"p:{p['id']}")] for p in chunk]
    rows += nav("a:ps", page, pages)
    rows += [[A("🗂 Категории", "cats"), A("✅ Проданные", "sold:0")], [A("➕ Добавить товар", "ap:new")], HOME_ROW]
    await show_text(cb, f"📦 ТОВАРЫ В ПРОДАЖЕ: {len(items)}", rows)


@router.callback_query(F.data.startswith("a:sold:"))
async def sold_list(cb: CallbackQuery):
    await cb.answer()
    items = await db.admin_products("sold")
    chunk, page, pages = tpl.paginate(items, int(cb.data.split(":")[2]), config.PER_PAGE)
    rows = [[A(f"✅ #{p['id']} {p['title'][:34]} · ⭐{p['price']}", f"p:{p['id']}")] for p in chunk]
    rows += nav("a:sold", page, pages)
    rows.append([A("⬅️ Назад", "ps:0")])
    await show_text(cb, f"✅ ПРОДАННЫЕ ТОВАРЫ: {len(items)}", rows)


async def product_card(event, pid):
    p = await db.get_product(pid)
    if not p:
        return await show_text(event, "❌ Товар не найден.", [[A("⬅️ Назад", "ps:0")]])
    rows = []
    if p["status"] == "active":
        rows += [[A("✏️ Изменить", f"pe:{pid}"), A("💰 Цена", f"pp:{pid}")],
                 [A("🗑️ Удалить", f"pd:{pid}"), A("📊 Статистика", f"pst:{pid}")]]
    else:
        rows.append([A("📊 Статистика", f"pst:{pid}")])
    rows += [[A("👁️ Просмотреть", f"pv:{pid}")], [A("⬅️ Назад", "ps:0")]]
    await show_text(event, p_text(p), rows)


@router.callback_query(F.data.startswith("a:p:"))
async def product_open(cb: CallbackQuery):
    await cb.answer()
    await product_card(cb, int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("a:pv:"))
async def product_view(cb: CallbackQuery, user):
    await cb.answer()
    pid = int(cb.data.split(":")[2])
    p = await db.get_product(pid)
    if not p:
        return
    bot, cid, _ = ui._target(cb)
    await ui.send_rendered(bot, cid, await ui.get_msg("product_card", "ru"), await ui.build_vars(user, p),
                           None, ui.product_media(p))
    await show_text(cb.message, f"🔐 Выдаваемые данные:\n\n{p['data']}", [[A("⬅️ К товару", f"p:{pid}")]])


@router.callback_query(F.data.startswith("a:pst:"))
async def product_stat(cb: CallbackQuery):
    await cb.answer()
    pid = int(cb.data.split(":")[2])
    p = await db.get_product(pid)
    s = await db.product_stats(pid)
    lines = [f"📊 СТАТИСТИКА ТОВАРА #{pid}\n", p["title"], f"\nЦена: ⭐{p['price']}", f"В избранном: {s['favs']}",
             f"Создан: {ui._fmt_dt(p['created_at'])[0]}"]
    if s["purchase_id"]:
        lines.append(f"\n✅ Продан: покупка #{s['purchase_id']}\nПокупатель ID: {s['buyer']}\nОплачено: ⭐{s['paid']} ({s['method']})")
    await show_text(cb, "\n".join(lines), [[A("⬅️ К товару", f"p:{pid}")]])


# ── изменение полей ──
FIELDS = {"title": "название", "short_desc": "короткое описание", "description": "подробное описание",
          "data": "данные товара", "photo_id": "фото", "gif_id": "GIF"}


@router.callback_query(F.data.startswith("a:pe:"))
async def edit_menu(cb: CallbackQuery):
    await cb.answer()
    pid = int(cb.data.split(":")[2])
    rows = [[A(f"✏️ {name.capitalize()}", f"pf:{pid}:{key}")] for key, name in FIELDS.items()]
    rows += [[A("🗂 Категория", f"pc:{pid}"), A("🏷 Статус", f"pb:{pid}")], [A("⬅️ Назад", f"p:{pid}")]]
    await show_text(cb, "✏️ Что изменить?", rows)


@router.callback_query(F.data.startswith("a:pf:"))
async def edit_field(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    _, _, pid, field = cb.data.split(":")
    if field not in FIELDS:
        return
    await state.set_state(EF.value)
    await state.update_data(pid=int(pid), field=field)
    hint = " (или «-», чтобы убрать)" if field in ("photo_id", "gif_id") else ""
    await show_text(cb, f"Отправьте новое значение: {FIELDS[field]}{hint}", [[A("❌ Отмена", f"p:{pid}")]])


@router.message(EF.value)
async def edit_value(m: Message, state: FSMContext, user):
    d = await state.get_data()
    field, pid = d["field"], d["pid"]
    if field == "photo_id":
        value = m.photo[-1].file_id if m.photo else (None if (m.text or "").strip() == "-" else False)
    elif field == "gif_id":
        value = m.animation.file_id if m.animation else (None if (m.text or "").strip() == "-" else False)
    else:
        value = (m.text or "").strip() or False
        if value and field in ("title", "short_desc") and len(value) > 100:
            return await m.answer("Максимум 100 символов.")
    if value is False:
        return await m.answer("Неверный формат, попробуйте ещё раз.")
    ok = await db.update_product(pid, field, value)
    await state.clear()
    if ok:
        await db.log(user["id"], "product_edit", product_id=pid, field=field)
    await product_card(m, pid)


@router.callback_query(F.data.startswith("a:pc:"))
async def edit_category(cb: CallbackQuery):
    await cb.answer()
    parts = cb.data.split(":")
    pid = int(parts[2])
    if len(parts) == 4:   # выбор сделан
        await db.update_product(pid, "category_id", int(parts[3]) or None)
        return await product_card(cb, pid)
    rows = [[A(c["name"], f"pc:{pid}:{c['id']}")] for c in await db.categories()] + [[A("Без категории", f"pc:{pid}:0")]]
    await show_text(cb, "🗂 Выберите категорию:", rows)


@router.callback_query(F.data.startswith("a:pb:"))
async def edit_badge(cb: CallbackQuery):
    await cb.answer()
    parts = cb.data.split(":")
    pid = int(parts[2])
    if len(parts) == 4 and parts[3] in texts.BADGE_NAME:
        await db.update_product(pid, "badge", parts[3])
        return await product_card(cb, pid)
    rows = [[A(name, f"pb:{pid}:{k}")] for k, name in texts.BADGE_NAME.items()]
    await show_text(cb, "🏷 Выберите статус:", rows)


# ── цена ──
@router.callback_query(F.data.startswith("a:pp:"))
async def price_start(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    pid = int(cb.data.split(":")[2])
    await state.set_state(PC.price)
    await state.update_data(pid=pid)
    await show_text(cb, "Введите новую цену в ⭐:", [[A("❌ Отмена", f"p:{pid}")]])


@router.message(PC.price)
async def price_value(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if not txt.isdigit() or not (0 < int(txt) <= 100000):
        return await m.answer("Введите целое число ⭐ от 1 до 100000.")
    d = await state.get_data()
    p = await db.get_product(d["pid"])
    if not p or p["status"] != "active":
        await state.clear()
        return await m.answer("Товар недоступен для изменения.")
    await state.update_data(new_price=int(txt))
    rows = [[A("✅ Подтвердить", "ppy"), A("❌ Отмена", f"p:{p['id']}")]]
    await show_text(m, f"💰 ИЗМЕНЕНИЕ ЦЕНЫ\n\nТовар:\n{p['title']}\n\nСтарая цена:\n⭐{p['price']}\n\nНовая цена:\n⭐{txt}", rows)


@router.callback_query(F.data == "a:ppy")
async def price_confirm(cb: CallbackQuery, state: FSMContext, user):
    d = await state.get_data()
    if "new_price" not in d:
        return await cb.answer("Нет данных", show_alert=True)
    await state.clear()
    await cb.answer("✅")
    if await db.update_product(d["pid"], "price", d["new_price"]):
        await db.log(user["id"], "price_change", product_id=d["pid"], new_price=d["new_price"])
    await product_card(cb, d["pid"])


# ── удаление ──
@router.callback_query(F.data.startswith("a:pd:"))
async def delete_ask(cb: CallbackQuery):
    await cb.answer()
    pid = int(cb.data.split(":")[2])
    p = await db.get_product(pid)
    rows = [[A("🗑️ Да, удалить", f"pdy:{pid}"), A("❌ Отмена", f"p:{pid}")]]
    await show_text(cb, f"⚠️ УДАЛЕНИЕ ТОВАРА\n\nВы действительно хотите удалить:\n\n{p['title']}\n\nЦена:\n⭐{p['price']}", rows)


@router.callback_query(F.data.startswith("a:pdy:"))
async def delete_do(cb: CallbackQuery, user):
    pid = int(cb.data.split(":")[2])
    ok = await db.delete_product(pid)
    await cb.answer("🗑 Удалено" if ok else "Нельзя удалить", show_alert=not ok)
    if ok:
        await db.log(user["id"], "product_delete", product_id=pid)
    await show_text(cb, "🗑 Товар удалён из каталога (история покупок сохранена).", [[A("📦 К товарам", "ps:0")], HOME_ROW])


# ───────────────────────── добавление товара (анкета) ─────────────────────────
STEPS = ["title", "short", "desc", "price", "data", "photo", "gif", "category", "badge", "notify"]
PROMPT = {
    "title": "1/10 · Введите название товара:",
    "short": "2/10 · Короткое описание (оно показывается на кнопке товара в каталоге):",
    "desc": "3/10 · Подробное описание (показывается в карточке товара):",
    "price": "4/10 · Цена в ⭐ Telegram Stars (целое число):",
    "data": "5/10 · Данные товара — то, что получит покупатель. Например:\n\nЛогин:\n...\nПароль:\n...\nДополнительная информация:\n...",
    "photo": "6/10 · Отправьте изображение или пропустите:",
    "gif": "7/10 · Отправьте GIF или пропустите:",
    "category": "8/10 · Выберите категорию:",
    "badge": "9/10 · Выберите статус товара:",
    "notify": "10/10 · Уведомить пользователей о новом товаре?",
}
LABELS = {"title": "Название", "short": "Короткое описание", "desc": "Подробное описание", "price": "Цена",
          "data": "Данные", "photo": "Фото", "gif": "GIF", "category": "Категория", "badge": "Статус", "notify": "Уведомление"}


async def ask(target, state):
    d = await state.get_data()
    step = d["step"]
    rows = []
    if step in ("photo", "gif"):
        rows = [[A("⏭ Пропустить", "ap:skip")]]
    elif step == "category":
        rows = [[A(c["name"], f"ap:cat:{c['id']}")] for c in await db.categories()] + [[A("Без категории", "ap:cat:0")]]
    elif step == "badge":
        rows = [[A(name, f"ap:badge:{k}")] for k, name in texts.BADGE_NAME.items()]
    elif step == "notify":
        rows = [[A("🔔 Уведомить пользователей", "ap:notify:1")], [A("🔕 Не уведомлять", "ap:notify:0")]]
    rows.append([A("❌ Отмена", "ap:cancel")])
    await show_text(target, PROMPT[step], rows)


async def preview(target, state):
    d = await state.get_data()
    p = d["p"]
    cat = await db.category_name(int(p["category"])) if p.get("category") else "—"
    text = (f"📦 НОВЫЙ ТОВАР\n\nНазвание:\n{p['title']}\n\nКороткое описание:\n{p['short']}\n\n"
            f"Подробное описание:\n{p['desc']}\n\nЦена:\n⭐{p['price']}\n\nКатегория:\n{cat or '—'}\n\n"
            f"Статус: {texts.BADGE_NAME[p.get('badge', 'none')]}\n\nВыдаваемые данные:\n{p['data']}\n\n"
            f"Фото: {'есть' if p.get('photo') else 'нет'} · GIF: {'есть' if p.get('gif') else 'нет'}\n"
            f"Уведомление: {'🔔 да' if p.get('notify') == '1' else '🔕 нет'}")
    rows = [[A("✅ Опубликовать", "ap:pub")], [A("✏️ Изменить", "ap:edit"), A("❌ Отмена", "ap:cancel")]]
    await show_text(target, text, rows)


async def advance(target, state, value):
    d = await state.get_data()
    p = d["p"]
    p[d["step"]] = value
    if d.get("back"):
        await state.update_data(p=p, back=False)
        return await preview(target, state)
    i = STEPS.index(d["step"]) + 1
    if i >= len(STEPS):
        await state.update_data(p=p)
        return await preview(target, state)
    await state.update_data(p=p, step=STEPS[i])
    await ask(target, state)


@router.callback_query(F.data == "a:ap:new")
async def ap_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await state.set_state(AP.step)
    await state.update_data(step="title", p={}, back=False)
    await ask(cb, state)


@router.message(AP.step)
async def ap_message(m: Message, state: FSMContext):
    s = (await state.get_data())["step"]
    if s in ("title", "short", "desc", "data"):
        txt = (m.text or "").strip()
        if not txt:
            return await m.answer("Отправьте текст.")
        if s in ("title", "short") and len(txt) > 100:
            return await m.answer("Максимум 100 символов.")
        await advance(m, state, txt)
    elif s == "price":
        txt = (m.text or "").strip()
        if not txt.isdigit() or not (0 < int(txt) <= 100000):
            return await m.answer("Введите целое число ⭐ от 1 до 100000.")
        await advance(m, state, int(txt))
    elif s == "photo":
        if not m.photo:
            return await m.answer("Отправьте изображение (как фото) или нажмите «Пропустить».")
        await advance(m, state, m.photo[-1].file_id)
    elif s == "gif":
        if not m.animation:
            return await m.answer("Отправьте GIF или нажмите «Пропустить».")
        await advance(m, state, m.animation.file_id)
    else:
        await m.answer("Выберите вариант кнопкой.")


async def _step_is(state, *names) -> bool:
    return (await state.get_data()).get("step") in names


@router.callback_query(F.data == "a:ap:skip")
async def ap_skip(cb: CallbackQuery, state: FSMContext):
    if not await _step_is(state, "photo", "gif"):
        return await cb.answer()
    await cb.answer()
    await advance(cb, state, None)


@router.callback_query(F.data.startswith("a:ap:cat:"))
async def ap_cat(cb: CallbackQuery, state: FSMContext):
    if not await _step_is(state, "category"):
        return await cb.answer()
    await cb.answer()
    await advance(cb, state, cb.data.split(":")[3])


@router.callback_query(F.data.startswith("a:ap:badge:"))
async def ap_badge(cb: CallbackQuery, state: FSMContext):
    k = cb.data.split(":")[3]
    if not await _step_is(state, "badge") or k not in texts.BADGE_NAME:
        return await cb.answer()
    await cb.answer()
    await advance(cb, state, k)


@router.callback_query(F.data.startswith("a:ap:notify:"))
async def ap_notify(cb: CallbackQuery, state: FSMContext):
    if not await _step_is(state, "notify"):
        return await cb.answer()
    await cb.answer()
    await advance(cb, state, cb.data.split(":")[3])


@router.callback_query(F.data == "a:ap:edit")
async def ap_edit(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    rows = [[A(name, f"ap:f:{k}")] for k, name in LABELS.items()] + [[A("⬅️ К предпросмотру", "ap:back")]]
    await show_text(cb, "✏️ Какое поле изменить?", rows)


@router.callback_query(F.data == "a:ap:back")
async def ap_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await preview(cb, state)


@router.callback_query(F.data.startswith("a:ap:f:"))
async def ap_field(cb: CallbackQuery, state: FSMContext):
    k = cb.data.split(":")[3]
    if k not in STEPS:
        return await cb.answer()
    await cb.answer()
    await state.update_data(step=k, back=True)
    await ask(cb, state)


@router.callback_query(F.data == "a:ap:cancel")
async def ap_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    await show_text(cb, "❌ Создание товара отменено.", HOME)


@router.callback_query(F.data == "a:ap:pub")
async def ap_publish(cb: CallbackQuery, state: FSMContext, user):
    d = await state.get_data()
    p = d.get("p") or {}
    if not all(p.get(k) for k in ("title", "short", "desc", "price", "data")):
        return await cb.answer("Заполните все обязательные поля", show_alert=True)
    await state.clear()       # защита от повторного нажатия
    pid = await db.create_product({
        "title": p["title"], "short_desc": p["short"], "description": p["desc"], "price": p["price"], "data": p["data"],
        "photo_id": p.get("photo"), "gif_id": p.get("gif"),
        "category_id": (int(p["category"]) or None) if p.get("category") else None, "badge": p.get("badge", "none")})
    await db.log(user["id"], "product_create", product_id=pid, price=p["price"])
    await cb.answer("✅ Опубликовано")
    s = await db.settings()
    note = ""
    if p.get("notify") == "1":
        if s["notify_auto"] == "1":
            asyncio.create_task(broadcast.announce_new_product(cb.bot, pid))
            note = "\n🔔 Уведомления пользователям отправляются."
        else:
            note = "\n🔕 Авто-уведомления отключены в настройках."
    await show_text(cb, f"✅ Товар #{pid} опубликован.{note}",
                    [[A("📦 Открыть товар", f"p:{pid}")], [A("➕ Добавить ещё", "ap:new")], HOME_ROW])


# ───────────────────────── категории ─────────────────────────
@router.callback_query(F.data == "a:cats")
async def cats(cb: CallbackQuery):
    await cb.answer()
    rows = [[A(f"🗑 {c['name']} ({c['n']})", f"cd:{c['id']}")] for c in await db.categories_with_counts()]
    rows += [[A("➕ Новая категория", "cn")], [A("⬅️ Назад", "ps:0")]]
    await show_text(cb, "🗂 КАТЕГОРИИ\nНажмите на категорию, чтобы удалить её (товары останутся без категории).", rows)


@router.callback_query(F.data == "a:cn")
async def cat_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(CN.name)
    await show_text(cb, "Введите название категории:", [[A("❌ Отмена", "cats")]])


@router.message(CN.name)
async def cat_name(m: Message, state: FSMContext, user):
    name = (m.text or "").strip()
    if not name or len(name) > 40:
        return await m.answer("Название: 1–40 символов.")
    await state.clear()
    cid = await db.add_category(name)
    await db.log(user["id"], "category_create", category_id=cid, name=name)
    await show_text(m, f"✅ Категория «{name}» создана.", [[A("🗂 Категории", "cats")]])


@router.callback_query(F.data.startswith("a:cd:"))
async def cat_del_ask(cb: CallbackQuery):
    await cb.answer()
    cid = int(cb.data.split(":")[2])
    await show_text(cb, f"Удалить категорию «{await db.category_name(cid)}»?",
                    [[A("🗑️ Да, удалить", f"cdy:{cid}"), A("❌ Отмена", "cats")]])


@router.callback_query(F.data.startswith("a:cdy:"))
async def cat_del(cb: CallbackQuery, user):
    cid = int(cb.data.split(":")[2])
    await db.delete_category(cid)
    await db.log(user["id"], "category_delete", category_id=cid)
    await cb.answer("🗑")
    await cats(cb)


# ───────────────────────── статистика ─────────────────────────
@router.callback_query(F.data == "a:st")
async def stats(cb: CallbackQuery):
    await cb.answer()
    s = await db.stats()
    text = (f"📊 СТАТИСТИКА\n\n👥 Пользователей:\n{s['users']}\n\n🛍️ Продаж:\n{s['sales']}\n\n💰 Оборот:\n⭐{s['revenue']}\n\n"
            f"⭐ Пополнено:\n{s['deposits']}\n\n⭐ Потрачено:\n{s['spent']}\n\n📦 Товаров в продаже:\n{s['active_products']}\n\n"
            f"Продажи сегодня: {s['today'][0]} (⭐{s['today'][1]})\nПродажи за неделю: {s['week'][0]} (⭐{s['week'][1]})\n"
            f"Продажи за месяц: {s['month'][0]} (⭐{s['month'][1]})")
    await show_text(cb, text, [[A("📦 По товарам", "stp")], HOME_ROW])


@router.callback_query(F.data == "a:stp")
async def stats_products(cb: CallbackQuery):
    await cb.answer()
    icon = {"active": "🟢", "sold": "✅", "deleted": "🗑"}
    lines = [f"{icon[r['status']]} #{r['id']} {r['title'][:30]} · ⭐{r['price']} · ❤️{r['favs']}" for r in await db.product_overview()]
    await show_text(cb, "📦 СТАТИСТИКА ТОВАРОВ (последние 25)\n\n" + ("\n".join(lines) or "—"),
                    [[A("⬅️ Назад", "st")]])


# ───────────────────────── пользователи ─────────────────────────
@router.callback_query(F.data == "a:us")
async def users_start(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(US.query)
    await show_text(cb, "👥 Введите @username или Telegram ID пользователя:", [HOME_ROW])


def u_text(u, count):
    return (f"👤 Пользователь\n\nUsername:\n{('@' + u['username']) if u['username'] else '—'}\n\nID:\n{u['id']}\n\n"
            f"Баланс:\n⭐{u['balance']}\n\nПокупок:\n{count}\n\nПополнено:\n⭐{u['total_deposited']}\n\nПотрачено:\n⭐{u['total_spent']}")


async def user_card(event, uid):
    u = await db.get_user(uid)
    if not u:
        return await show_text(event, "❌ Пользователь не найден.", [HOME_ROW])
    rows = [[A("➕ Добавить баланс", f"ub:{uid}:p"), A("➖ Списать баланс", f"ub:{uid}:m")],
            [A("📦 Покупки", f"up:{uid}"), A("📊 Статистика", f"ust:{uid}")], HOME_ROW]
    await show_text(event, u_text(u, await db.purchase_count(uid)), rows)


@router.message(US.query)
async def users_query(m: Message, state: FSMContext):
    u = await db.find_user(m.text or "")
    if not u:
        return await m.answer("❌ Не найден. Введите @username или ID ещё раз.")
    await state.clear()
    await user_card(m, u["id"])


@router.callback_query(F.data.startswith("a:u:"))
async def user_open(cb: CallbackQuery):
    await cb.answer()
    await user_card(cb, int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("a:ub:"))
async def bal_start(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    _, _, uid, sign = cb.data.split(":")
    await state.set_state(BA.amount)
    await state.update_data(uid=int(uid), sign=1 if sign == "p" else -1)
    await show_text(cb, f"Введите сумму ⭐ для {'начисления' if sign == 'p' else 'списания'}:", [[A("❌ Отмена", f"u:{uid}")]])


@router.message(BA.amount)
async def bal_amount(m: Message, state: FSMContext, user):
    txt = (m.text or "").strip()
    if not txt.isdigit() or not (0 < int(txt) <= 1_000_000):
        return await m.answer("Введите целое положительное число.")
    d = await state.get_data()
    await state.clear()
    delta = d["sign"] * int(txt)
    bal = await db.adjust_balance(user["id"], d["uid"], delta)
    if bal is None:
        await m.answer("❌ Недостаточно средств на балансе пользователя.")
    else:
        try:
            await m.bot.send_message(d["uid"], f"💰 Баланс изменён администратором: {delta:+d} ⭐\nТекущий баланс: ⭐{bal}")
        except Exception:
            pass
    await user_card(m, d["uid"])


@router.callback_query(F.data.startswith("a:up:"))
async def user_purchases(cb: CallbackQuery):
    await cb.answer()
    uid = int(cb.data.split(":")[2])
    items = await db.list_purchases(uid)
    lines = [f"#{p['id']} · {p['product_title']} · ⭐{p['price']} · {ui._fmt_dt(p['created_at'])[0]}" for p in items[:30]]
    await show_text(cb, f"📦 Покупки пользователя {uid}\n\n" + ("\n".join(lines) or "—"), [[A("⬅️ Назад", f"u:{uid}")]])


@router.callback_query(F.data.startswith("a:ust:"))
async def user_stat(cb: CallbackQuery):
    await cb.answer()
    uid = int(cb.data.split(":")[2])
    s, last = await db.user_stats(uid)
    lines = [f"{r['type']}: {r['amount']:+d} · {ui._fmt_dt(r['created_at'])[0]}" for r in last]
    text = (f"📊 Статистика {uid}\n\nПокупок: {s['purchases']}\nПополнений: ⭐{s['deposits']}\nБонусов: ⭐{s['bonuses']}\n"
            f"В избранном: {s['favs']}\n\nПоследние операции:\n" + ("\n".join(lines) or "—"))
    await show_text(cb, text, [[A("⬅️ Назад", f"u:{uid}")]])


@router.callback_query(F.data == "a:bal")
async def balances(cb: CallbackQuery):
    await cb.answer()
    total, top, adj = await db.top_balances()
    lines = [f"{('@' + r['username']) if r['username'] else r['id']} — ⭐{r['balance']}" for r in top]
    log_lines = [f"admin {r['admin_id']} → {r['details'].get('user_id')}: {r['details'].get('delta'):+d}" for r in adj]
    await show_text(cb, f"💰 БАЛАНСЫ\n\nСумма на балансах: ⭐{total}\n\nТоп-10:\n" + ("\n".join(lines) or "—") +
                    "\n\nРучные изменения (журнал):\n" + ("\n".join(log_lines) or "—"),
                    [[A("👥 Найти пользователя", "us")], HOME_ROW])


# ───────────────────────── промокоды ─────────────────────────
@router.callback_query(F.data == "a:promo")
async def promos(cb: CallbackQuery):
    await cb.answer()
    rows = []
    lines = []
    for p in await db.list_promos():
        kind = f"💰+{p['value']}⭐" if p["kind"] == "bonus" else f"💸-{p['value']}%"
        lim = f"{p['used_count']}/{p['max_uses'] or '∞'}"
        lines.append(f"{'🟢' if p['is_active'] else '⏸'} {p['code']} · {kind} · {lim}")
        rows.append([A(f"{'⏸' if p['is_active'] else '▶️'} {p['code']}", f"prt:{p['id']}"), A("🗑", f"prd:{p['id']}")])
    rows += [[A("➕ Создать промокод", "prn")], HOME_ROW]
    await show_text(cb, "🎟️ ПРОМОКОДЫ\n\n" + ("\n".join(lines) or "Пока нет промокодов."), rows)


@router.callback_query(F.data.startswith("a:prt:"))
async def promo_toggle(cb: CallbackQuery):
    await db.toggle_promo(int(cb.data.split(":")[2]))
    await promos(cb)


@router.callback_query(F.data.startswith("a:prd:"))
async def promo_delete(cb: CallbackQuery, user):
    pid = int(cb.data.split(":")[2])
    await db.delete_promo(pid)
    await db.log(user["id"], "promo_delete", promo_id=pid)
    await promos(cb)


@router.callback_query(F.data == "a:prn")
async def promo_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(PN.code)
    await state.update_data(pn={})
    await show_text(cb, "🎟️ Введите код (латиница/цифры, 3–32 символа):", [[A("❌ Отмена", "promo")]])


@router.message(PN.code)
async def promo_code(m: Message, state: FSMContext):
    code = (m.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
        return await m.answer("Только A–Z, 0–9, _ и -, длина 3–32.")
    if await db.promo_exists(code):
        return await m.answer("Такой код уже существует.")
    await state.update_data(pn={"code": code})
    await state.set_state(PN.kind)
    await show_text(m, "Тип промокода:", [[A("💰 Бонус к балансу", "prk:bonus")], [A("💸 Скидка на покупку", "prk:discount")]])


@router.callback_query(PN.kind, F.data.startswith("a:prk:"))
async def promo_kind(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    kind = cb.data.split(":")[2]
    d = await state.get_data()
    d["pn"]["kind"] = kind
    await state.update_data(pn=d["pn"])
    await state.set_state(PN.value)
    await show_text(cb, "Размер бонуса в ⭐:" if kind == "bonus" else "Размер скидки в % (1–99):")


@router.message(PN.value)
async def promo_value(m: Message, state: FSMContext):
    d = await state.get_data()
    txt = (m.text or "").strip()
    hi = 100000 if d["pn"]["kind"] == "bonus" else 99
    if not txt.isdigit() or not (1 <= int(txt) <= hi):
        return await m.answer(f"Введите число от 1 до {hi}.")
    d["pn"]["value"] = int(txt)
    await state.update_data(pn=d["pn"])
    await state.set_state(PN.uses)
    await m.answer("Количество использований (0 — без ограничений):")


@router.message(PN.uses)
async def promo_uses(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if not txt.isdigit():
        return await m.answer("Введите целое число (0 — без ограничений).")
    d = await state.get_data()
    d["pn"]["uses"] = int(txt)
    await state.update_data(pn=d["pn"])
    await state.set_state(PN.days)
    await m.answer("Срок действия в днях (0 — бессрочно):")


@router.message(PN.days)
async def promo_days(m: Message, state: FSMContext, user):
    txt = (m.text or "").strip()
    if not txt.isdigit():
        return await m.answer("Введите целое число (0 — бессрочно).")
    pn = (await state.get_data())["pn"]
    await state.clear()
    pid = await db.create_promo(pn["code"], pn["kind"], pn["value"], pn["uses"], int(txt))
    await db.log(user["id"], "promo_create", promo_id=pid, code=pn["code"])
    await show_text(m, f"✅ Промокод {pn['code']} создан.", [[A("🎟️ Промокоды", "promo")], HOME_ROW])


# ───────────────────────── настройки ─────────────────────────
SET_TEXT = {"support_username": "username поддержки (например @cryptcursed)", "shop_name": "название магазина",
            "bonus_per": "за каждые N ⭐ пополнения (целое число)", "bonus_amount": "размер бонуса, ⭐ (целое число)",
            "reserve_minutes": "время резервирования товара, минут (1–60)"}


@router.callback_query(F.data == "a:set")
async def settings_page(cb: CallbackQuery):
    await cb.answer()
    s = await db.settings()
    onoff = lambda k: "🟢 вкл" if s[k] == "1" else "🔴 выкл"
    text = (f"⚙️ НАСТРОЙКИ\n\nПоддержка: {s['support_username']}\nМагазин: {s['shop_name']}\n"
            f"Бонус за пополнение: {onoff('bonus_enabled')} (+{s['bonus_amount']}⭐ за каждые {s['bonus_per']}⭐)\n"
            f"Авто-уведомления о новых товарах: {onoff('notify_auto')}\nРезерв товара: {s['reserve_minutes']} мин.")
    rows = [[A("🎫 Поддержка", "se:support_username"), A("🏪 Название", "se:shop_name")],
            [A(f"🎁 Бонус: {onoff('bonus_enabled')}", "tg:bonus_enabled")],
            [A("Бонус: за N ⭐", "se:bonus_per"), A("Бонус: размер", "se:bonus_amount")],
            [A(f"🔔 Авто-уведомления: {onoff('notify_auto')}", "tg:notify_auto")],
            [A("⏳ Резерв товара", "se:reserve_minutes")], HOME_ROW]
    await show_text(cb, text, rows)


@router.callback_query(F.data.startswith("a:tg:"))
async def setting_toggle(cb: CallbackQuery, user):
    key = cb.data.split(":")[2]
    if key not in ("bonus_enabled", "notify_auto"):
        return await cb.answer()
    s = await db.settings()
    await db.set_setting(key, "0" if s[key] == "1" else "1")
    await db.log(user["id"], "setting_toggle", key=key)
    await settings_page(cb)


@router.callback_query(F.data.startswith("a:se:"))
async def setting_edit(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in SET_TEXT:
        return await cb.answer()
    await cb.answer()
    await state.set_state(ST.value)
    await state.update_data(key=key)
    await show_text(cb, f"Введите: {SET_TEXT[key]}", [[A("❌ Отмена", "set")]])


@router.message(ST.value)
async def setting_value(m: Message, state: FSMContext, user):
    key = (await state.get_data())["key"]
    v = (m.text or "").strip()
    if key == "support_username":
        if not re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", v):
            return await m.answer("Неверный username.")
        v = "@" + v.lstrip("@")
    elif key == "shop_name":
        if not v or len(v) > 60:
            return await m.answer("1–60 символов.")
    else:
        hi = 60 if key == "reserve_minutes" else 100000
        if not v.isdigit() or not (1 <= int(v) <= hi):
            return await m.answer(f"Целое число от 1 до {hi}.")
    await db.set_setting(key, v)
    await db.log(user["id"], "setting_change", key=key, value=v)
    await state.clear()
    s = await db.settings()
    await show_text(m, f"✅ Сохранено: {v}", [[A("⚙️ Настройки", "set")], HOME_ROW])
