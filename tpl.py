"""Шаблонизатор сообщений: подстановка переменных с пересчётом Telegram entities.

Важно: offset/length у entities считаются в UTF-16 code units, поэтому при замене
{переменной} на значение другой длины все entities (включая Premium Emoji) сдвигаются.
"""
import re

VAR_RE = re.compile(r"\{(\w+)\}")
CODE_VARS = {"product_data"}          # значение оборачивается в `code` (копируется нажатием)


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def render_plain(text, vars_: dict) -> str:
    return VAR_RE.sub(lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), text or "")


def render(text, entities, vars_: dict):
    text = text or ""
    shifts, out, last = [], [], 0
    for m in VAR_RE.finditer(text):
        key = m.group(1)
        if key not in vars_:
            continue
        val = str(vars_[key])
        s = u16(text[:m.start()])
        e = s + u16(m.group(0))
        shifts.append((s, e, u16(val) - (e - s), key, val))
        out.append(text[last:m.start()])
        out.append(val)
        last = m.end()
    out.append(text[last:])
    result = "".join(out)

    ents = []
    for en in entities or []:
        o, ln = en["offset"], en["length"]
        end = o + ln
        new_o = o + sum(d for _, e, d, _, _ in shifts if e <= o)
        new_l = ln + sum(d for s, e, d, _, _ in shifts if s >= o and e <= end)
        ents.append({**en, "offset": new_o, "length": new_l})
    for i, (s, _, _, key, val) in enumerate(shifts):
        if key in CODE_VARS and val:
            ents.append({"type": "code", "offset": s + sum(x[2] for x in shifts[:i]), "length": u16(val)})
    return result, ents


def strip_custom(ents):
    return [e for e in (ents or []) if e.get("type") != "custom_emoji"]


_BTN_RE = re.compile(r"^(.+?)\s+[-–—]\s+(\S+)$")


def parse_buttons(raw: str):
    """«Текст - ссылка», несколько кнопок в строке через « | », строки = ряды."""
    raw = (raw or "").strip()
    if raw in ("", "-"):
        return []
    rows = []
    for n, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split("|"):
            m = _BTN_RE.match(part.strip())
            if not m:
                raise ValueError(f"Строка {n}: ожидается формат «Текст - ссылка»")
            text, url = m.group(1).strip(), m.group(2).strip()
            if not (url.startswith(("http://", "https://", "tg://")) or url.startswith("{")):
                raise ValueError(f"Строка {n}: ссылка должна начинаться с https:// (или быть переменной)")
            row.append({"text": text[:64], "url": url})
        rows.append(row[:3])
    if len(rows) > 8:
        raise ValueError("Максимум 8 рядов кнопок")
    return rows


def paginate(items, page: int, per: int = 8):
    items = list(items)
    pages = max(1, -(-len(items) // per))
    page = max(0, min(page, pages - 1))
    return items[page * per:(page + 1) * per], page, pages
