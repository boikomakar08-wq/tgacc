"""Редактор сообщений (текст/фото/GIF/Premium Emoji/кнопки + предпросмотр) и рассылка /bd."""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import broadcast
import db
import texts
import tpl
import ui
from filters import IsAdmin
from ui import btn, show_text

log = logging.getLogger(__name__)
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def A(text, data):
    return btn(text, "a:" + data)


class ME(StatesGroup):
    content = State()
    buttons = State()


class BD(StatesGroup):
    content = State()
    buttons = State()


BUTTONS_HELP = ("🔘 Кнопки (необязательно)\n\nФормат: «Текст - ссылка». Несколько кнопок в ряд — через « | », "
                "новый ряд — с новой строки. В ссылках можно использовать переменные, например {support_link}.\n\n"
                "Пример:\nСайт - https://example.com | Поддержка - {support_link}\n\nОтправьте «-», чтобы не добавлять кнопки.")


def _ents(es):
    return [e.model_dump(exclude_none=True, mode="json") for e in es] if es else []


def collect(m: Message):
    """Забирает контент вместе с Telegram entities (в т.ч. Premium Emoji)."""
    if m.photo:
        return {"text": m.caption or "", "entities": _ents(m.caption_entities), "photo_id": m.photo[-1].file_id, "gif_id": None}
    if m.animation:
        return {"text": m.caption or "", "entities": _ents(m.caption_entities), "photo_id": None, "gif_id": m.animation.file_id}
    if m.text:
        return {"text": m.text, "entities": _ents(m.entities), "photo_id": None, "gif_id": None}
    return None


async def _fresh(event):
    """Удаляет сообщение-кнопку (если это callback) и возвращает (bot, chat_id)."""
    bot, cid, edit = ui._target(event)
    if edit is not None:
        try:
            await edit.delete()
        except Exception:
            pass
    return bot, cid


VARS_BTN = [A("📋 Доступные переменные", "vars")]


@router.callback_query(F.data == "a:vars")
async def variables(cb: CallbackQuery):
    await cb.answer()
    lines = ["📋 ДОСТУПНЫЕ ПЕРЕМЕННЫЕ\n"]
    for group, items in texts.VARIABLES.items():
        lines.append(f"{group}:")
        lines += [f"{{{k}}} — {v}" for k, v in items.items()]
        lines.append("")
    lines.append("Переменные работают во всех настраиваемых сообщениях и сохраняют Premium Emoji.")
    await ui.send_rendered(cb.bot, cb.message.chat.id, {"text": "\n".join(lines)}, {}, [[A("✖️ Закрыть", "close")]])


@router.callback_query(F.data == "a:close")
async def close(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass


# ───────────────────────── редактор сообщений ─────────────────────────
@router.callback_query(F.data == "a:msgs")
async def msgs(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    rows = [[A(label, f"m:{key}")] for key, label in texts.MESSAGE_KEYS.items()]
    rows += [[A("🔘 Тексты кнопок", "lb")], VARS_BTN, [A("🏠 Админ-панель", "home")]]
    await show_text(cb, "📝 СООБЩЕНИЯ\nВыберите сообщение (шаблон, меню или страницу раздела) для редактирования:", rows)


@router.callback_query(F.data.startswith("a:m:"))
async def msg_open(cb: CallbackQuery, user):
    key = cb.data.split(":")[2]
    if key not in texts.MESSAGE_KEYS:
        return await cb.answer()
    await cb.answer()
    bot, cid = await _fresh(cb)
    custom = await db.get_message(key)
    m = await ui.get_msg(key, "ru")
    await ui.send_rendered(bot, cid, m, await ui.sample_vars(user))
    status = "✏️ изменено администратором" if custom else "по умолчанию"
    rows = [[A("✏️ Изменить", f"me:{key}")], [A("♻️ Вернуть по умолчанию", f"mr:{key}")], VARS_BTN, [A("⬅️ Назад", "msgs")]]
    await ui.send_rendered(bot, cid, {"text": f"{texts.MESSAGE_KEYS[key]}\nСтатус: {status}\n\nВыше — текущий вид (с примерными данными)."}, {}, rows)


@router.callback_query(F.data.startswith("a:mr:"))
async def msg_reset(cb: CallbackQuery, user):
    key = cb.data.split(":")[2]
    if key in texts.MESSAGE_KEYS:
        await db.reset_message(key)
        await db.log(user["id"], "message_reset", key=key)
    await cb.answer("♻️ Сброшено")
    await show_text(cb, "♻️ Сообщение возвращено к значению по умолчанию.", [[A("📝 Сообщения", "msgs")]])


@router.callback_query(F.data.startswith("a:me:"))
async def msg_edit(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in texts.MESSAGE_KEYS:
        return await cb.answer()
    await cb.answer()
    await state.clear()
    await state.set_state(ME.content)
    await state.update_data(key=key)
    await show_text(cb, f"✏️ {texts.MESSAGE_KEYS[key]}\n\nОтправьте новое сообщение: текст, фото или GIF с подписью. "
                        "Premium Emoji, форматирование и переменные сохранятся как есть.",
                    [VARS_BTN, [A("❌ Отмена", "msgs")]])


@router.message(ME.content)
async def msg_content(m: Message, state: FSMContext):
    d = collect(m)
    if not d:
        return await m.answer("Отправьте текст, фото или GIF.")
    await state.update_data(draft={**d, "buttons": []})
    await state.set_state(ME.buttons)
    await show_text(m, BUTTONS_HELP, [[A("⏭ Без кнопок", "mskip")], [A("❌ Отмена", "msgs")]])


async def _msg_preview(event, state, user):
    d = await state.get_data()
    bot, cid = await _fresh(event)
    await ui.send_rendered(bot, cid, d["draft"], await ui.sample_vars(user))
    rows = [[A("👁️ Предпросмотр", "mp")], [A("💾 Сохранить", "ms"), A("✏️ Изменить", f"me:{d['key']}")], [A("❌ Отмена", "msgs")]]
    await ui.send_rendered(bot, cid, {"text": f"👁️ Предпросмотр: {texts.MESSAGE_KEYS[d['key']]}"}, {}, rows)


@router.message(ME.buttons)
async def msg_buttons(m: Message, state: FSMContext, user):
    try:
        rows = tpl.parse_buttons(m.text or "")
    except ValueError as e:
        return await m.answer(f"❌ {e}")
    d = await state.get_data()
    d["draft"]["buttons"] = rows
    await state.update_data(draft=d["draft"])
    await _msg_preview(m, state, user)


@router.callback_query(ME.buttons, F.data == "a:mskip")
async def msg_skip(cb: CallbackQuery, state: FSMContext, user):
    await cb.answer()
    await _msg_preview(cb, state, user)


@router.callback_query(F.data == "a:mp")
async def msg_preview_again(cb: CallbackQuery, state: FSMContext, user):
    if "draft" not in await state.get_data():
        return await cb.answer("Нет черновика", show_alert=True)
    await cb.answer()
    await _msg_preview(cb, state, user)


@router.callback_query(F.data == "a:ms")
async def msg_save(cb: CallbackQuery, state: FSMContext, user):
    d = await state.get_data()
    if "draft" not in d or "key" not in d:
        return await cb.answer("Нет черновика", show_alert=True)
    await db.save_message(d["key"], d["draft"])
    await db.log(user["id"], "message_save", key=d["key"])
    await state.clear()
    await cb.answer("💾 Сохранено")
    await show_text(cb, "✅ Сообщение сохранено.", [[A("📝 Сообщения", "msgs")], [A("🏠 Админ-панель", "home")]])


# ───────────────────────── /bd — массовая рассылка ─────────────────────────
@router.message(Command("bd"))
@router.callback_query(F.data == "a:bd")
async def bd_start(event, state: FSMContext):
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.clear()
    await state.set_state(BD.content)
    prompt = (await ui.get_msg("broadcast", "ru"))["text"]
    await show_text(event, prompt, [VARS_BTN, [A("❌ Отмена", "bdc")]])


@router.message(BD.content)
async def bd_content(m: Message, state: FSMContext):
    d = collect(m)
    if not d:
        return await m.answer("Отправьте текст, фото или GIF.")
    await state.update_data(draft={**d, "buttons": []})
    await state.set_state(BD.buttons)
    await show_text(m, BUTTONS_HELP, [[A("⏭ Без кнопок", "bdskip")], [A("❌ Отмена", "bdc")]])


async def _bd_preview(event, state, user):
    d = await state.get_data()
    bot, cid = await _fresh(event)
    await ui.send_rendered(bot, cid, d["draft"], await ui.sample_vars(user))
    n = len(await db.broadcast_users())
    rows = [[A("🚀 Отправить всем", "bdgo")], [A("✏️ Изменить", "bd"), A("❌ Отмена", "bdc")]]
    await ui.send_rendered(bot, cid, {"text": f"📢 ПРЕДПРОСМОТР\n\n👥 Получателей:\n{n}"}, {}, rows)


@router.message(BD.buttons)
async def bd_buttons(m: Message, state: FSMContext, user):
    try:
        rows = tpl.parse_buttons(m.text or "")
    except ValueError as e:
        return await m.answer(f"❌ {e}")
    d = await state.get_data()
    d["draft"]["buttons"] = rows
    await state.update_data(draft=d["draft"])
    await _bd_preview(m, state, user)


@router.callback_query(BD.buttons, F.data == "a:bdskip")
async def bd_skip(cb: CallbackQuery, state: FSMContext, user):
    await cb.answer()
    await _bd_preview(cb, state, user)


@router.callback_query(F.data == "a:bdc")
async def bd_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    await show_text(cb, "❌ Рассылка отменена.", [[A("🏠 Админ-панель", "home")]])


@router.callback_query(F.data == "a:bdgo")
async def bd_go(cb: CallbackQuery, state: FSMContext, user):
    d = await state.get_data()
    if "draft" not in d:
        return await cb.answer("Нет черновика", show_alert=True)
    if broadcast.is_running():
        return await cb.answer("Другая рассылка ещё выполняется", show_alert=True)
    await state.clear()
    if not await broadcast.start(cb.bot, user["id"], d["draft"]):
        return await cb.answer("Другая рассылка ещё выполняется", show_alert=True)
    await db.log(user["id"], "broadcast_start")
    await cb.answer("🚀")
    await show_text(cb, "🚀 Рассылка запущена. По завершении я пришлю отчёт.", [[A("🏠 Админ-панель", "home")]])


# ───────────────────────── тексты кнопок (только надпись) ─────────────────────────
import re  # noqa: E402


class BL(StatesGroup):
    value = State()


LANG_NAMES = {"all": "🌐 Все языки", **{k: v for k, v in texts.LANGS.items()}}


def _label_keys():
    return {k for _, keys in texts.BUTTON_SECTIONS for k in keys}


@router.callback_query(F.data == "a:lb")
async def lb_sections(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    rows = [[A(name, f"lbs:{i}")] for i, (name, _) in enumerate(texts.BUTTON_SECTIONS)]
    rows.append([A("⬅️ Назад", "msgs")])
    await show_text(cb, "🔘 ТЕКСТЫ КНОПОК\nВыберите раздел. Меняется только надпись на кнопке — действие остаётся прежним.", rows)


@router.callback_query(F.data.startswith("a:lbs:"))
async def lb_section(cb: CallbackQuery):
    await cb.answer()
    i = int(cb.data.split(":")[2])
    name, keys = texts.BUTTON_SECTIONS[i]
    rows = []
    for k in keys:
        changed = any((k, lg) in texts.OVERRIDES for lg in ("all", "ru", "uk", "en"))
        rows.append([A(("✏️ " if changed else "") + texts.t("ru", k), f"lbk:{k}")])
    rows.append([A("⬅️ Назад", "lb")])
    await show_text(cb, f"{name}\nВыберите кнопку (✏️ — текст уже изменён):", rows)


@router.callback_query(F.data.startswith("a:lbk:"))
async def lb_key(cb: CallbackQuery):
    key = cb.data.split(":")[2]
    if key not in _label_keys():
        return await cb.answer()
    await cb.answer()
    lines = [f"Стандартно (RU): {texts.L['ru'][key]}", ""]
    for lg in ("all", "ru", "uk", "en"):
        v = texts.OVERRIDES.get((key, lg))
        lines.append(f"{LANG_NAMES[lg]}: {v if v else '—'}")
    lines.append("\nПриоритет: текст для конкретного языка важнее «Все языки».")
    rows = [[A(name, f"lbl:{key}:{lg}")] for lg, name in LANG_NAMES.items()] + [[A("⬅️ Назад", "lb")]]
    await show_text(cb, "Для какого языка менять текст?\n\n" + "\n".join(lines), rows)


@router.callback_query(F.data.startswith("a:lbl:"))
async def lb_edit(cb: CallbackQuery, state: FSMContext):
    _, _, key, lg = cb.data.split(":")
    if key not in _label_keys() or lg not in LANG_NAMES:
        return await cb.answer()
    await cb.answer()
    await state.set_state(BL.value)
    await state.update_data(key=key, lg=lg)
    ph = sorted(set(re.findall(r"\{(\w+)\}", texts.L["ru"][key])))
    ph_note = f"\nМожно использовать: {', '.join('{' + p + '}' for p in ph)}" if ph else ""
    await show_text(cb, f"Отправьте новый текст кнопки ({LANG_NAMES[lg]}), до 40 символов.{ph_note}\n"
                        "Обычные emoji можно, Premium Emoji в кнопках Telegram не поддерживает.",
                    [[A("♻️ Сбросить", f"lbr:{key}:{lg}")], [A("❌ Отмена", f"lbk:{key}")]])


@router.message(BL.value)
async def lb_value(m: Message, state: FSMContext, user):
    d = await state.get_data()
    key, lg = d["key"], d["lg"]
    v = (m.text or "").strip()
    if not v or len(v) > 40:
        return await m.answer("Текст кнопки: от 1 до 40 символов.")
    allowed = set(re.findall(r"\{(\w+)\}", texts.L["ru"][key]))
    bad = set(re.findall(r"\{(\w+)\}", v)) - allowed
    if bad:
        return await m.answer("Недопустимые переменные: " + ", ".join(sorted(bad)))
    await db.set_label(key, lg, v)
    await db.log(user["id"], "button_label", key=key, lang=lg)
    await state.clear()
    await show_text(m, f"✅ Сохранено: {v}", [[A("🔘 Тексты кнопок", "lb")], [A("🏠 Админ-панель", "home")]])


@router.callback_query(F.data.startswith("a:lbr:"))
async def lb_reset(cb: CallbackQuery, state: FSMContext, user):
    _, _, key, lg = cb.data.split(":")
    if key in _label_keys() and lg in LANG_NAMES:
        await db.reset_label(key, lg)
        await db.log(user["id"], "button_label_reset", key=key, lang=lg)
    await state.clear()
    await cb.answer("♻️ Сброшено")
    await show_text(cb, "♻️ Текст кнопки сброшен к стандартному.", [[A("🔘 Тексты кнопок", "lb")]])
