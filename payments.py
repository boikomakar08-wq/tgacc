"""Платежи Telegram Stars: pre_checkout, successful_payment, идемпотентная обработка."""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, Message, PreCheckoutQuery

import db
import delivery
import ui
from texts import t
from ui import btn, show

log = logging.getLogger(__name__)
router = Router()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    lang = await db.get_lang(q.from_user.id)
    try:
        kind, *a = q.invoice_payload.split(":")
        if kind == "buy":
            pid, promo_id = int(a[0]), int(a[1]) or None
            expected = await db.expected_price(pid, promo_id)
            if expected is None or q.total_amount < expected:
                return await q.answer(False, error_message=t(lang, "err_price"))
            # продлеваем резерв на время завершения оплаты; False — товар уже занят/продан
            if not await db.reserve(pid, q.from_user.id, 3):
                return await q.answer(False, error_message=t(lang, "err_gone"))
        elif kind != "topup":
            return await q.answer(False, error_message="Unknown invoice")
        await q.answer(True)
    except Exception:
        log.exception("pre_checkout failed")
        await q.answer(False, error_message="Error, try again")


@router.message(F.successful_payment)
async def successful_payment(m: Message, user, lang):
    sp = m.successful_payment
    uid = m.from_user.id
    log.info("payment uid=%s payload=%s amount=%s charge=%s", uid, sp.invoice_payload, sp.total_amount, sp.telegram_payment_charge_id)
    kind, *a = sp.invoice_payload.split(":")

    if kind == "topup":
        status, bal, bonus = await db.credit_topup(uid, sp.total_amount, sp.telegram_payment_charge_id)
        if status == "dup":
            return
        bp = t(lang, "bonus_part", bonus=bonus) if bonus else ""
        await m.answer(t(lang, "topup_done", amount=sp.total_amount, bonus_part=bp, balance=bal),
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn(t(lang, "btn_catalog"), "cat")], [btn(t(lang, "btn_menu"), "menu")]]))
        who = f"@{user['username']}" if user.get("username") else "—"
        await delivery.notify_admins(m.bot, f"⭐ ПОПОЛНЕНИЕ\n\n{who} (ID {uid})\nСумма: ⭐{sp.total_amount}\nБонус: ⭐{bonus}")

    elif kind == "buy":
        pid, promo_id = int(a[0]), int(a[1]) or None
        status, res = await db.finalize_star_purchase(uid, pid, sp.total_amount, sp.telegram_payment_charge_id, promo_id)
        if status == "dup":
            return
        if status == "ok":
            await delivery.deliver(m.bot, uid, res)
        else:   # credited
            v = await ui.build_vars(await db.get_user(uid), error=t(lang, "credited", amount=sp.total_amount))
            await show(m, "payment_error", lang, v, [[btn(t(lang, "btn_to_catalog"), "cat")]])
            await delivery.notify_admins(m.bot, f"⚠️ Оплата за уже недоступный товар #{pid}: ⭐{sp.total_amount} зачислены на баланс ID {uid}.")


@router.message(Command("paysupport"))
async def paysupport(m: Message, user, lang):
    """Telegram требует, чтобы бот с платежами отвечал на /paysupport."""
    rows = [[btn(t(lang, "btn_support"), url=await ui.support_url())]]
    await show(m, "support", lang, await ui.build_vars(user), rows)
