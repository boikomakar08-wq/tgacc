CREATE TABLE IF NOT EXISTS users (
    id               BIGINT PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    last_name        TEXT,
    lang             TEXT    NOT NULL DEFAULT 'ru',
    balance          BIGINT  NOT NULL DEFAULT 0 CHECK (balance >= 0),
    total_deposited  BIGINT  NOT NULL DEFAULT 0,
    total_spent      BIGINT  NOT NULL DEFAULT 0,
    notify_new       BOOLEAN NOT NULL DEFAULT TRUE,
    is_blocked       BOOLEAN NOT NULL DEFAULT FALSE,
    active_promo_id  INT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_username_idx ON users (lower(username));

CREATE TABLE IF NOT EXISTS categories (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL,
    position  INT  NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS products (
    id             BIGSERIAL PRIMARY KEY,
    title          TEXT NOT NULL,
    short_desc     TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    price          INT  NOT NULL CHECK (price > 0),
    data           TEXT NOT NULL,
    photo_id       TEXT,
    gif_id         TEXT,
    category_id    INT REFERENCES categories(id) ON DELETE SET NULL,
    badge          TEXT NOT NULL DEFAULT 'none',
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','sold','deleted')),
    reserved_by    BIGINT,
    reserved_until TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    sold_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS products_status_cat_idx ON products (status, category_id);

CREATE TABLE IF NOT EXISTS purchases (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id),
    product_id    BIGINT NOT NULL REFERENCES products(id),
    product_title TEXT   NOT NULL,
    price         INT    NOT NULL,
    product_data  TEXT   NOT NULL,
    status        TEXT   NOT NULL DEFAULT 'paid',
    method        TEXT   NOT NULL,
    charge_id     TEXT UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS purchases_user_idx ON purchases (user_id, id DESC);
-- Страховка от двойной продажи на уровне схемы: один товар — одна оплаченная покупка
CREATE UNIQUE INDEX IF NOT EXISTS purchases_one_paid_per_product
    ON purchases (product_id) WHERE status = 'paid';

-- Финансовые записи никогда не удаляются
CREATE TABLE IF NOT EXISTS transactions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id),
    type          TEXT   NOT NULL,
    amount        BIGINT NOT NULL,
    balance_after BIGINT NOT NULL,
    ref           TEXT,
    charge_id     TEXT UNIQUE,   -- идемпотентность платежей Telegram
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transactions_user_idx ON transactions (user_id, id DESC);

CREATE TABLE IF NOT EXISTS favorites (
    user_id    BIGINT NOT NULL REFERENCES users(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS promo_codes (
    id         SERIAL PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('bonus','discount')),
    value      INT  NOT NULL,
    max_uses   INT  NOT NULL DEFAULT 0,      -- 0 = без ограничений
    used_count INT  NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS promo_uses (
    id          BIGSERIAL PRIMARY KEY,
    promo_id    INT    NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    purchase_id BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (promo_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    key        TEXT PRIMARY KEY,
    text       TEXT,
    entities   JSONB,
    photo_id   TEXT,
    gif_id     TEXT,
    buttons    JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id          BIGSERIAL PRIMARY KEY,
    admin_id    BIGINT NOT NULL,
    payload     JSONB,
    total       INT NOT NULL DEFAULT 0,
    sent        INT NOT NULL DEFAULT 0,
    failed      INT NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id         BIGSERIAL PRIMARY KEY,
    admin_id   BIGINT NOT NULL,
    action     TEXT NOT NULL,
    details    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    username    TEXT,
    purchase_id BIGINT,
    subject     TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO categories (name, position)
SELECT x.name, x.pos FROM (VALUES ('Telegram',1),('Игровые аккаунты',2),('Цифровые товары',3),('Другое',4)) AS x(name,pos)
WHERE NOT EXISTS (SELECT 1 FROM categories);
