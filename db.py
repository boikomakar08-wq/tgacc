"""PostgreSQL: пул, схема и все транзакционные операции.

Защита от двойной продажи (три уровня):
  1. Резервирование: UPDATE ... WHERE свободен — атомарно на уровне строки.
  2. Финализация: SELECT ... FOR UPDATE по пользователю и товару (всегда в одном порядке:
     users -> products, чтобы исключить взаимные блокировки) внутри одной транзакции.
  3. Уникальный частичный индекс purchases(product_id) WHERE status='paid'.
Идемпотентность платежей Telegram — UNIQUE(transactions.charge_id).
"""
import json
import pathlib
import datetime as dt

import asyncpg

import config

pool: asyncpg.Pool | None = None
_cache: dict | None = None

SETTING_DEFAULTS = {
    "shop_name": config.DEFAULT_SHOP_NAME,
    "support_username": config.DEFAULT_SUPPORT,
    "bonus_enabled": "1",       # бонус за пополнение
    "bonus_per": "100",         # за каждые N ⭐
    "bonus_amount": "10",       # +M ⭐
    "notify_auto": "1",         # глобальный рубильник уведомлений о новых товарах
    "reserve_minutes": "5",
}
PRODUCT_FIELDS = {"title", "short_desc", "description", "data", "photo_id", "gif_id", "category_id", "badge", "price"}


async def _init_conn(c):
    for ty in ("jsonb", "json"):
        await c.set_type_codec(ty, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=15, init=_init_conn)
    async with pool.acquire() as c:
        await c.execute(pathlib.Path(__file__).with_name("schema.sql").read_text("utf-8"))
        # Миграция старой таблицы messages (где ключ был уникальным без языка).
        cols = await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='messages' AND table_schema='public'")
        colnames = {r["column_name"] for r in cols}
        if "lang" not in colnames:
            await c.execute("ALTER TABLE messages ADD COLUMN lang TEXT NOT NULL DEFAULT 'ru'")
        await c.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_pkey")
        await c.execute("ALTER TABLE messages ADD CONSTRAINT messages_pkey PRIMARY KEY (key, lang)")
        for k, v in SETTING_DEFAULTS.items():
            await c.execute("INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING", k, v)


async def close():
    if pool:
        await pool.close()


def _d(r):
    return dict(r) if r is not None else None


# ───────────────────────── настройки / сообщения / логи ─────────────────────────
async def settings() -> dict:
    global _cache
    if _cache is None:
        _cache = {r["key"]: r["value"] for r in await pool.fetch("SELECT key,value FROM settings")}
    return _cache


async def set_setting(key: str, value: str):
    global _cache
    await pool.execute("INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=$2", key, value)
    _cache = None


async def get_message(key, lang="ru"):
    return _d(await pool.fetchrow("SELECT * FROM messages WHERE key=$1 AND lang=$2", key, lang))


async def save_message(key, lang, d):
    await pool.execute(
        """INSERT INTO messages(key,lang,text,entities,photo_id,gif_id,buttons,updated_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,now())
           ON CONFLICT(key,lang) DO UPDATE SET text=$3,entities=$4,photo_id=$5,gif_id=$6,buttons=$7,updated_at=now()""",
        key, lang, d.get("text"), d.get("entities") or [], d.get("photo_id"), d.get("gif_id"), d.get("buttons") or [])


async def reset_message(key, lang=None):
    if lang:
        await pool.execute("DELETE FROM messages WHERE key=$1 AND lang=$2", key, lang)
    else:
        await pool.execute("DELETE FROM messages WHERE key=$1", key)


async def refresh_labels():
    import texts
    rows = await pool.fetch("SELECT key,lang,text FROM button_labels")
    texts.OVERRIDES.clear()
    texts.OVERRIDES.update({(r["key"], r["lang"]): r["text"] for r in rows})


async def set_label(key, lang, text):
    await pool.execute("INSERT INTO button_labels(key,lang,text) VALUES($1,$2,$3) ON CONFLICT(key,lang) DO UPDATE SET text=$3", key, lang, text)
    await refresh_labels()


async def reset_label(key, lang):
    await pool.execute("DELETE FROM button_labels WHERE key=$1 AND lang=$2", key, lang)
    await refresh_labels()


async def log(admin_id: int, action: str, **details):
    await pool.execute("INSERT INTO admin_logs(admin_id,action,details) VALUES($1,$2,$3)", admin_id, action, details)


# ───────────────────────── пользователи ─────────────────────────
async def upsert_user(tu) -> tuple[dict, bool]:
    code = (tu.language_code or "en")[:2]
    # New users always start in English; language is selected explicitly on the welcome screen.
    detected_lang = "en"
    existed = await pool.fetchval("SELECT 1 FROM users WHERE id=$1", tu.id)
    r = await pool.fetchrow(
        """INSERT INTO users(id,username,first_name,last_name,lang) VALUES($1,$2,$3,$4,$5)
           ON CONFLICT(id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name,
               last_name=EXCLUDED.last_name, is_blocked=FALSE
           RETURNING *""", tu.id, tu.username, tu.first_name, tu.last_name, detected_lang)
    return dict(r), not bool(existed)


async def get_user(uid):
    return _d(await pool.fetchrow("SELECT * FROM users WHERE id=$1", uid))


async def get_lang(uid) -> str:
    return await pool.fetchval("SELECT lang FROM users WHERE id=$1", uid) or "ru"


async def set_lang(uid, lang):
    await pool.execute("UPDATE users SET lang=$2 WHERE id=$1", uid, lang)


async def toggle_notify(uid) -> bool:
    return await pool.fetchval("UPDATE users SET notify_new = NOT notify_new WHERE id=$1 RETURNING notify_new", uid)


async def find_user(q: str):
    q = q.strip()
    if q.lstrip("-").isdigit():
        return await get_user(int(q))
    return _d(await pool.fetchrow("SELECT * FROM users WHERE lower(username)=lower($1)", q.lstrip("@")))


async def purchase_count(uid) -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM purchases WHERE user_id=$1 AND status='paid'", uid)


async def user_stats(uid):
    r = await pool.fetchrow(
        """SELECT (SELECT COUNT(*) FROM purchases WHERE user_id=$1 AND status='paid') AS purchases,
                  (SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=$1 AND type='deposit') AS deposits,
                  (SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=$1 AND type='bonus') AS bonuses,
                  (SELECT COUNT(*) FROM favorites WHERE user_id=$1) AS favs""", uid)
    last = await pool.fetch("SELECT type,amount,created_at FROM transactions WHERE user_id=$1 ORDER BY id DESC LIMIT 8", uid)
    return dict(r), last


# ───────────────────────── каталог ─────────────────────────
async def categories():
    return await pool.fetch("SELECT * FROM categories WHERE is_active ORDER BY position,id")


async def categories_with_counts():
    return await pool.fetch(
        """SELECT c.id, c.name, COUNT(p.id) AS n FROM categories c
           LEFT JOIN products p ON p.category_id=c.id AND p.status='active'
           WHERE c.is_active GROUP BY c.id ORDER BY c.position, c.id""")


async def uncategorized_count() -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM products WHERE status='active' AND category_id IS NULL")


async def category_name(cid):
    return await pool.fetchval("SELECT name FROM categories WHERE id=$1", cid)


async def add_category(name):
    return await pool.fetchval("INSERT INTO categories(name, position) VALUES($1, COALESCE((SELECT MAX(position)+1 FROM categories),1)) RETURNING id", name)


async def delete_category(cid):
    await pool.execute("DELETE FROM categories WHERE id=$1", cid)


async def products_in(cid, uid):
    return await pool.fetch(
        """SELECT p.*, COALESCE(p.reserved_until > now() AND p.reserved_by <> $2, false) AS busy
           FROM products p WHERE p.status='active' AND p.category_id IS NOT DISTINCT FROM $1::int
           ORDER BY p.id DESC""", cid, uid)


async def get_product(pid):
    return _d(await pool.fetchrow(
        "SELECT p.*, c.name AS category_name FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=$1", pid))


async def create_product(p: dict) -> int:
    return await pool.fetchval(
        """INSERT INTO products(title,short_desc,description,price,data,photo_id,gif_id,category_id,badge)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
        p["title"], p["short_desc"], p["description"], p["price"], p["data"],
        p.get("photo_id"), p.get("gif_id"), p.get("category_id"), p.get("badge", "none"))


async def update_product(pid, field, value) -> bool:
    if field not in PRODUCT_FIELDS:
        raise ValueError("bad field")
    r = await pool.fetchval(f"UPDATE products SET {field}=$2 WHERE id=$1 AND status='active' RETURNING id", pid, value)
    return r is not None


async def delete_product(pid) -> bool:
    """Мягкое удаление: проданные товары и история покупок остаются нетронутыми."""
    r = await pool.fetchval(
        "UPDATE products SET status='deleted', reserved_by=NULL, reserved_until=NULL WHERE id=$1 AND status='active' RETURNING id", pid)
    return r is not None


async def admin_products(status):
    return await pool.fetch("SELECT p.*, c.name AS category_name FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.status=$1 ORDER BY p.id DESC", status)


async def product_stats(pid):
    r = await pool.fetchrow(
        """SELECT (SELECT COUNT(*) FROM favorites WHERE product_id=$1) AS favs,
                  pu.id AS purchase_id, pu.user_id AS buyer, pu.price AS paid, pu.created_at AS bought_at, pu.method
           FROM (SELECT 1) x LEFT JOIN purchases pu ON pu.product_id=$1 AND pu.status='paid'""", pid)
    return dict(r)


# ───────────────────────── резервирование ─────────────────────────
async def reserve(pid, uid, minutes) -> bool:
    r = await pool.fetchval(
        """UPDATE products SET reserved_by=$2, reserved_until=now()+make_interval(mins => $3)
           WHERE id=$1 AND status='active' AND (reserved_until IS NULL OR reserved_until < now() OR reserved_by=$2)
           RETURNING id""", pid, uid, int(minutes))
    return r is not None


async def release_expired():
    await pool.execute("UPDATE products SET reserved_by=NULL, reserved_until=NULL WHERE reserved_until < now()")


# ───────────────────────── цены / промокоды ─────────────────────────
def _disc(price: int, promo) -> int:
    return price if not promo else max(1, price - price * promo["value"] // 100)


_PROMO_VALID = ("is_active AND (expires_at IS NULL OR expires_at > now()) "
                "AND (max_uses = 0 OR used_count < max_uses)")


async def _active_promo(c, u):
    if not u["active_promo_id"]:
        return None
    return await c.fetchrow(f"SELECT * FROM promo_codes WHERE id=$1 AND kind='discount' AND {_PROMO_VALID}", u["active_promo_id"])


async def quote(uid, pid):
    """(итоговая цена, id применённого промокода|None)."""
    async with pool.acquire() as c:
        u = await c.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        price = await c.fetchval("SELECT price FROM products WHERE id=$1", pid)
        promo = await _active_promo(c, u)
        return _disc(price, promo), (promo["id"] if promo else None)


async def expected_price(pid, promo_id):
    async with pool.acquire() as c:
        price = await c.fetchval("SELECT price FROM products WHERE id=$1", pid)
        if price is None:
            return None
        promo = await c.fetchrow(f"SELECT * FROM promo_codes WHERE id=$1 AND kind='discount'", promo_id) if promo_id else None
        return _disc(price, promo)


async def apply_promo(uid, code):
    """('bonus'|'discount', promo) либо (код ошибки, None)."""
    async with pool.acquire() as c, c.transaction():
        await c.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
        p = await c.fetchrow(
            "SELECT *, (expires_at IS NOT NULL AND expires_at < now()) AS expired FROM promo_codes WHERE upper(code)=upper($1) FOR UPDATE",
            code.strip())
        if not p or not p["is_active"]:
            return "notfound", None
        if p["expired"]:
            return "expired", None
        if p["max_uses"] and p["used_count"] >= p["max_uses"]:
            return "used_up", None
        if await c.fetchval("SELECT 1 FROM promo_uses WHERE promo_id=$1 AND user_id=$2", p["id"], uid):
            return "already", None
        if p["kind"] == "bonus":
            await c.execute("INSERT INTO promo_uses(promo_id,user_id) VALUES($1,$2)", p["id"], uid)
            await c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE id=$1", p["id"])
            bal = await c.fetchval("UPDATE users SET balance=balance+$2 WHERE id=$1 RETURNING balance", uid, p["value"])
            await c.execute("INSERT INTO transactions(user_id,type,amount,balance_after,ref) VALUES($1,'promo',$2,$3,$4)",
                            uid, p["value"], bal, f"promo:{p['code']}")
            return "bonus", dict(p)
        await c.execute("UPDATE users SET active_promo_id=$2 WHERE id=$1", uid, p["id"])
        return "discount", dict(p)


# ───────────────────────── покупки ─────────────────────────
async def _sale(c, uid, p, price, ttype, charge_id, promo_id, from_balance):
    if from_balance:
        await c.execute("UPDATE users SET balance=balance-$2, total_spent=total_spent+$2 WHERE id=$1", uid, price)
    else:
        await c.execute("UPDATE users SET total_spent=total_spent+$2 WHERE id=$1", uid, price)
    await c.execute("UPDATE products SET status='sold', sold_at=now(), reserved_by=NULL, reserved_until=NULL WHERE id=$1", p["id"])
    pur = await c.fetchrow(
        """INSERT INTO purchases(user_id,product_id,product_title,price,product_data,status,method,charge_id)
           VALUES($1,$2,$3,$4,$5,'paid',$6,$7) RETURNING *""",
        uid, p["id"], p["title"], price, p["data"], "balance" if from_balance else "stars", charge_id)
    bal = await c.fetchval("SELECT balance FROM users WHERE id=$1", uid)
    await c.execute(
        "INSERT INTO transactions(user_id,type,amount,balance_after,ref,charge_id) VALUES($1,$2,$3,$4,$5,$6)",
        uid, ttype, -price, bal, f"purchase:{pur['id']}", charge_id)
    if promo_id:
        await c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE id=$1", promo_id)
        await c.execute("INSERT INTO promo_uses(promo_id,user_id,purchase_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", promo_id, uid, pur["id"])
        await c.execute("UPDATE users SET active_promo_id=NULL WHERE id=$1 AND active_promo_id=$2", uid, promo_id)
    return pur


_FREE = "(status='active' AND (reserved_until IS NULL OR reserved_until < now() OR reserved_by=$2)) AS free"


async def buy_with_balance(uid, pid):
    """('ok', purchase) | ('gone'|'busy'|'funds', None)."""
    try:
        async with pool.acquire() as c, c.transaction():
            u = await c.fetchrow("SELECT * FROM users WHERE id=$1 FOR UPDATE", uid)
            p = await c.fetchrow(f"SELECT *, {_FREE} FROM products WHERE id=$1 FOR UPDATE", pid, uid)
            if not p or p["status"] != "active":
                return "gone", None
            if not p["free"]:
                return "busy", None
            promo = await _active_promo(c, u)
            price = _disc(p["price"], promo)
            if u["balance"] < price:
                return "funds", None
            pur = await _sale(c, uid, p, price, "purchase_balance", None, promo["id"] if promo else None, True)
            return "ok", dict(pur)
    except asyncpg.UniqueViolationError:
        return "gone", None


async def finalize_star_purchase(uid, pid, paid, charge_id, promo_id):
    """Идемпотентная фиксация покупки после successful_payment.
    ('ok', purchase) | ('dup', None) | ('credited', новый_баланс)."""
    try:
        async with pool.acquire() as c, c.transaction():
            if await c.fetchval("SELECT 1 FROM transactions WHERE charge_id=$1", charge_id):
                return "dup", None
            await c.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
            p = await c.fetchrow(f"SELECT *, {_FREE} FROM products WHERE id=$1 FOR UPDATE", pid, uid)
            if p and p["free"]:
                pur = await _sale(c, uid, p, paid, "purchase_stars", charge_id, promo_id, False)
                return "ok", dict(pur)
            # Товар успели продать/удалить: деньги не теряем — зачисляем Stars на баланс
            bal = await c.fetchval("UPDATE users SET balance=balance+$2 WHERE id=$1 RETURNING balance", uid, paid)
            await c.execute(
                "INSERT INTO transactions(user_id,type,amount,balance_after,ref,charge_id) VALUES($1,'refund_credit',$2,$3,$4,$5)",
                uid, paid, bal, f"product:{pid}", charge_id)
            return "credited", bal
    except asyncpg.UniqueViolationError:
        return "dup", None


async def credit_topup(uid, amount, charge_id):
    """('ok', баланс, бонус) | ('dup', None, 0)."""
    s = await settings()
    try:
        async with pool.acquire() as c, c.transaction():
            if await c.fetchval("SELECT 1 FROM transactions WHERE charge_id=$1", charge_id):
                return "dup", None, 0
            await c.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", uid)
            bonus = 0
            if s["bonus_enabled"] == "1" and int(s["bonus_per"]) > 0:
                bonus = (amount // int(s["bonus_per"])) * int(s["bonus_amount"])
            bal = await c.fetchval(
                "UPDATE users SET balance=balance+$2, total_deposited=total_deposited+$3 WHERE id=$1 RETURNING balance",
                uid, amount, amount)
            await c.execute("INSERT INTO transactions(user_id,type,amount,balance_after,ref,charge_id) VALUES($1,'deposit',$2,$3,'topup',$4)",
                            uid, amount, bal, charge_id)
            if bonus:
                bal = await c.fetchval("UPDATE users SET balance=balance+$2 WHERE id=$1 RETURNING balance", uid, bonus)
                await c.execute("INSERT INTO transactions(user_id,type,amount,balance_after,ref) VALUES($1,'bonus',$2,$3,'topup_bonus')",
                                uid, bonus, bal)
            return "ok", bal, bonus
    except asyncpg.UniqueViolationError:
        return "dup", None, 0


async def adjust_balance(admin_id, uid, delta, note="manual"):
    async with pool.acquire() as c, c.transaction():
        u = await c.fetchrow("SELECT * FROM users WHERE id=$1 FOR UPDATE", uid)
        if not u or u["balance"] + delta < 0:
            return None
        bal = u["balance"] + delta
        await c.execute("UPDATE users SET balance=$2 WHERE id=$1", uid, bal)
        await c.execute("INSERT INTO transactions(user_id,type,amount,balance_after,ref,note) VALUES($1,'admin_adjust',$2,$3,$4,$5)",
                        uid, delta, bal, f"admin:{admin_id}", note)
        await c.execute("INSERT INTO admin_logs(admin_id,action,details) VALUES($1,'balance_adjust',$2)",
                        admin_id, {"user_id": uid, "delta": delta, "balance_after": bal})
        return bal


async def list_purchases(uid):
    return await pool.fetch("SELECT * FROM purchases WHERE user_id=$1 ORDER BY id DESC", uid)


async def get_purchase(pid, uid=None):
    """При uid проверяется владелец покупки."""
    if uid is None:
        return _d(await pool.fetchrow("SELECT * FROM purchases WHERE id=$1", pid))
    return _d(await pool.fetchrow("SELECT * FROM purchases WHERE id=$1 AND user_id=$2", pid, uid))


# ───────────────────────── избранное ─────────────────────────
async def toggle_fav(uid, pid) -> bool:
    if await pool.fetchval("DELETE FROM favorites WHERE user_id=$1 AND product_id=$2 RETURNING 1", uid, pid):
        return False
    await pool.execute("INSERT INTO favorites(user_id,product_id) VALUES($1,$2) ON CONFLICT DO NOTHING", uid, pid)
    return True


async def is_fav(uid, pid) -> bool:
    return bool(await pool.fetchval("SELECT 1 FROM favorites WHERE user_id=$1 AND product_id=$2", uid, pid))


async def remove_fav(uid, pid):
    await pool.execute("DELETE FROM favorites WHERE user_id=$1 AND product_id=$2", uid, pid)


async def list_favs(uid):
    return await pool.fetch(
        "SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id WHERE f.user_id=$1 ORDER BY f.created_at DESC", uid)


# ───────────────────────── поддержка ─────────────────────────
async def create_ticket(user, purchase) -> int:
    return await pool.fetchval(
        "INSERT INTO support_tickets(user_id,username,purchase_id,subject) VALUES($1,$2,$3,$4) RETURNING id",
        user["id"], user.get("username"), purchase["id"], purchase["product_title"])


# ───────────────────────── промокоды (админ) ─────────────────────────
async def list_promos():
    return await pool.fetch("SELECT * FROM promo_codes ORDER BY id DESC LIMIT 40")


async def create_promo(code, kind, value, max_uses, days):
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days) if days else None
    return await pool.fetchval(
        "INSERT INTO promo_codes(code,kind,value,max_uses,expires_at) VALUES($1,$2,$3,$4,$5) RETURNING id",
        code, kind, value, max_uses, exp)


async def promo_exists(code) -> bool:
    return bool(await pool.fetchval("SELECT 1 FROM promo_codes WHERE upper(code)=upper($1)", code))


async def toggle_promo(pid):
    await pool.execute("UPDATE promo_codes SET is_active = NOT is_active WHERE id=$1", pid)


async def delete_promo(pid):
    await pool.execute("UPDATE users SET active_promo_id=NULL WHERE active_promo_id=$1", pid)
    await pool.execute("DELETE FROM promo_codes WHERE id=$1", pid)


# ───────────────────────── статистика ─────────────────────────
async def stats() -> dict:
    tz = config.TIMEZONE
    r = await pool.fetchrow(
        """SELECT (SELECT COUNT(*) FROM users) AS users,
                  (SELECT COUNT(*) FROM purchases WHERE status='paid') AS sales,
                  (SELECT COALESCE(SUM(price),0) FROM purchases WHERE status='paid') AS revenue,
                  (SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='deposit') AS deposits,
                  (SELECT COALESCE(SUM(total_spent),0) FROM users) AS spent,
                  (SELECT COUNT(*) FROM products WHERE status='active') AS active_products""")
    out = dict(r)
    periods = {
        "today": ("created_at >= date_trunc('day', now() AT TIME ZONE $1) AT TIME ZONE $1", True),
        "week": ("created_at >= now() - interval '7 days'", False),
        "month": ("created_at >= now() - interval '30 days'", False),
    }
    for name, (cond, needs_tz) in periods.items():
        q = f"SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM purchases WHERE status='paid' AND {cond}"
        row = await (pool.fetchrow(q, tz) if needs_tz else pool.fetchrow(q))
        out[name] = (row["n"], row["s"])
    return out


async def top_balances():
    total = await pool.fetchval("SELECT COALESCE(SUM(balance),0) FROM users")
    top = await pool.fetch("SELECT id,username,balance FROM users WHERE balance>0 ORDER BY balance DESC LIMIT 10")
    adj = await pool.fetch("SELECT * FROM admin_logs WHERE action='balance_adjust' ORDER BY id DESC LIMIT 8")
    return total, top, adj


async def product_overview():
    return await pool.fetch(
        """SELECT p.id, p.title, p.price, p.status, (SELECT COUNT(*) FROM favorites f WHERE f.product_id=p.id) AS favs
           FROM products p ORDER BY p.id DESC LIMIT 25""")


# ───────────────────────── рассылки ─────────────────────────
async def broadcast_users(only_notify=False):
    q = "SELECT * FROM users WHERE NOT is_blocked"
    if only_notify:
        q += " AND notify_new"
    return await pool.fetch(q)


async def mark_blocked(ids):
    if ids:
        await pool.execute("UPDATE users SET is_blocked=TRUE WHERE id = ANY($1::bigint[])", list(ids))


async def create_broadcast(admin_id, payload, total) -> int:
    return await pool.fetchval("INSERT INTO broadcasts(admin_id,payload,total) VALUES($1,$2,$3) RETURNING id", admin_id, payload, total)


async def finish_broadcast(bid, sent, failed):
    await pool.execute("UPDATE broadcasts SET sent=$2, failed=$3, status='done', finished_at=now() WHERE id=$1", bid, sent, failed)
