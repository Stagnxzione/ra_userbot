# regular_bot.py
from __future__ import annotations

import asyncio
import os
import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
from dotenv import load_dotenv
from html import escape as _html_escape
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# =========================
# Конфиг / окружение
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Чат для диспетчера (куда шлём при «Требуется эвакуатор»)
DISPATCH_CHAT_ID = int(os.getenv("DISPATCH_CHAT_ID", "0"))

# Jira базовые
JIRA_BASE_URL    = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL       = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN   = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "")  # ключ проекта (например, RA)

# Тип верхнеуровневой задачи
JIRA_ISSUE_TYPE_MAIN_ID = os.getenv("JIRA_ISSUE_TYPE_MAIN_ID", "")
JIRA_ISSUE_TYPE_MAIN    = os.getenv("JIRA_ISSUE_TYPE_MAIN", "Task")

# Тип подзадачи (для RA и «Дежмеха»)
JIRA_SUBTASK_TYPE_ID    = os.getenv("JIRA_SUBTASK_TYPE_ID", "")           # например "10000"
JIRA_SUBTASK_TYPE       = os.getenv("JIRA_SUBTASK_TYPE", "Sub-task")      # или "Подзадача" в русской локали

# Тип линка (если будем линковать верхнеуровневые задачи)
JIRA_LINK_TYPE = os.getenv("JIRA_LINK_TYPE", "Relates")

# Кастомные поля (ID вида customfield_XXXXX) + виды данных
JIRA_CF_INCIDENT_TYPE       = os.getenv("JIRA_CF_INCIDENT_TYPE")
JIRA_CF_INCIDENT_TYPE_KIND  = os.getenv("JIRA_CF_INCIDENT_TYPE_KIND", "select")
JIRA_CF_BRAND               = os.getenv("JIRA_CF_BRAND")
JIRA_CF_BRAND_KIND          = os.getenv("JIRA_CF_BRAND_KIND", "select")

JIRA_CF_PLATE_VATS          = os.getenv("JIRA_CF_PLATE_VATS")
JIRA_CF_PLATE_VATS_KIND     = os.getenv("JIRA_CF_PLATE_VATS_KIND", "text")
JIRA_CF_PLATE_REF           = os.getenv("JIRA_CF_PLATE_REF")
JIRA_CF_PLATE_REF_KIND      = os.getenv("JIRA_CF_PLATE_REF_KIND", "text")
JIRA_CF_LOCATION            = os.getenv("JIRA_CF_LOCATION")
JIRA_CF_LOCATION_KIND       = os.getenv("JIRA_CF_LOCATION_KIND", "text")
JIRA_CF_PROBLEM_DESC        = os.getenv("JIRA_CF_PROBLEM_DESC")
JIRA_CF_PROBLEM_DESC_KIND   = os.getenv("JIRA_CF_PROBLEM_DESC_KIND", "text")
JIRA_CF_NOTES               = os.getenv("JIRA_CF_NOTES")
JIRA_CF_NOTES_KIND          = os.getenv("JIRA_CF_NOTES_KIND", "text")

JIRA_CF_INCIDENT_DATE       = os.getenv("JIRA_CF_INCIDENT_DATE")
JIRA_CF_INCIDENT_DATE_KIND  = os.getenv("JIRA_CF_INCIDENT_DATE_KIND", "date")    # "date"|"text"
JIRA_CF_INCIDENT_TIME       = os.getenv("JIRA_CF_INCIDENT_TIME")
JIRA_CF_INCIDENT_TIME_KIND  = os.getenv("JIRA_CF_INCIDENT_TIME_KIND", "time")    # "time"|"datetime"|"text"

# Флаги Да/Нет (select)
JIRA_CF_FLAG_REQUIRE_MECH        = os.getenv("JIRA_CF_FLAG_REQUIRE_MECH")
JIRA_CF_FLAG_REQUIRE_MECH_KIND   = os.getenv("JIRA_CF_FLAG_REQUIRE_MECH_KIND", "select")
JIRA_CF_FLAG_PROBLEM_SOLVED      = os.getenv("JIRA_CF_FLAG_PROBLEM_SOLVED")
JIRA_CF_FLAG_PROBLEM_SOLVED_KIND = os.getenv("JIRA_CF_FLAG_PROBLEM_SOLVED_KIND", "select")
JIRA_CF_FLAG_REQUIRE_RA          = os.getenv("JIRA_CF_FLAG_REQUIRE_RA")
JIRA_CF_FLAG_REQUIRE_RA_KIND     = os.getenv("JIRA_CF_FLAG_REQUIRE_RA_KIND", "select")

# Значения опций для Да/Нет
JIRA_OPT_YES = os.getenv("JIRA_OPT_YES", "Да")
JIRA_OPT_NO  = os.getenv("JIRA_OPT_NO",  "Нет")

# Маппинги подписей select для наших кодов
INCIDENT_TYPE_OPTION_MAP = {
    "DTP":   os.getenv("JIRA_OPT_INCIDENT_TYPE__DTP",   "ДТП"),
    "BREAK": os.getenv("JIRA_OPT_INCIDENT_TYPE__BREAK", "Поломка"),
}
BRAND_OPTION_MAP = {
    "KIA_CEED": os.getenv("JIRA_OPT_BRAND__KIA_CEED", "Kia Ceed"),
    "SITRAK":   os.getenv("JIRA_OPT_BRAND__SITRAK",   "Sitrak"),
}

# =========================
# Утилиты
# =========================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)

def short_id(n: int = 8) -> str:
    import secrets, string
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

async def safe_edit_message_text(query, *, text, reply_markup=None, parse_mode=ParseMode.HTML):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

async def safe_edit_reply_markup(query, *, reply_markup):
    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

def format_jira_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")

def format_jira_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")

def format_jira_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

# =========================
# Нормализация госномеров (только порядок/кол-во)
# =========================

ANY_LETTERS_CLASS = "A-ZА-ЯЁ"

LAT_TO_CYR = str.maketrans({
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М",
    "H": "Н", "O": "О", "P": "Р", "C": "С", "T": "Т",
    "Y": "У", "X": "Х",
})

PLATE_RE_34 = re.compile(rf"^([{ANY_LETTERS_CLASS}])(\d{{3,4}})([{ANY_LETTERS_CLASS}]{{2}})(\d{{2,3}})$")
PLATE_RE_3  = re.compile(rf"^([{ANY_LETTERS_CLASS}])(\d{{3}})([{ANY_LETTERS_CLASS}]{{2}})(\d{{2,3}})$")
REF_COMPACT_RE = re.compile(rf"^([{ANY_LETTERS_CLASS}]{{2}})(\d{{4}})(\d{{2,3}})$")

def normalize_vats_plate(text: str, *, brand: Optional[str]) -> Optional[str]:
    if not text:
        return None
    s = "".join(ch for ch in (text or "").upper() if ch.isalnum()).translate(LAT_TO_CYR)
    rx = PLATE_RE_3 if brand == "KIA_CEED" else PLATE_RE_34
    m = rx.match(s)
    if not m:
        return None
    l1, d, l2, reg = m.groups()
    return f"{l1}{d}{l2}{reg}"

def normalize_ref_plate(text: str) -> Optional[str]:
    if not text:
        return None
    s = "".join(ch for ch in (text or "").upper() if ch.isalnum()).translate(LAT_TO_CYR)
    m = REF_COMPACT_RE.match(s)
    if not m:
        letters = "".join(ch for ch in s if ch.isalpha())
        digits  = "".join(ch for ch in s if ch.isdigit())
        if len(letters) < 2 or len(digits) < 6:
            return None
        l2  = letters[:2]
        d4  = digits[:4]
        reg = digits[4:7]
        if len(reg) < 2:
            return None
        s2 = f"{l2}{d4}{reg}"
        m = REF_COMPACT_RE.match(s2)
        if not m:
            return None
    l2, d4, reg = m.groups()
    return f"{l2}{d4}{reg}"

def format_vats_display(compact: Optional[str]) -> str:
    if not compact:
        return "—"
    m = PLATE_RE_34.match(compact) or PLATE_RE_3.match(compact)
    if not m:
        return compact
    l1, d, l2, reg = m.groups()
    return f"{l1}{d}{l2} {reg}"

def format_ref_display(compact: Optional[str]) -> str:
    if not compact:
        return "—"
    m = REF_COMPACT_RE.match(compact)
    if not m:
        return compact
    l2, d4, reg = m.groups()
    return f"{l2}{d4} {reg}"

# =========================
# Анкета и статусы
# =========================

ALL_STEP_KEYS = [
    "incident_type",
    "brand",
    "plate_vats",
    "plate_ref",
    "location",
    "problem_desc",
    "notes",
]

QUESTION_LABELS = {
    "incident_type": "Выберите тип происшествия:",
    "brand": "Выберите тип ВАТС:",
    "plate_vats": "Укажите госномер ВАТС (буква + 3/4 цифры + 2 буквы + 2/3 цифры)",
    "plate_ref": "Укажите госномер рефа/пп (2 буквы + 4 цифры + 2/3 цифры)",
    "location": "Местоположение ВАТС (координаты/ориентиры)",
    "problem_desc": "Напишите подробности проблемы",
    "notes": "Особые отметки, примечание",
}

STEP_INPUT_KIND = {
    "incident_type": "choice",
    "brand": "choice",
    "plate_vats": "plate",
    "plate_ref": "plate_ref",
    "location": "text",
    "problem_desc": "text",
    "notes": "text",
}

CHOICE_OPTIONS = {
    "incident_type": [("ДТП", "DTP"), ("Поломка", "BREAK")],
    "brand": [("Kia Ceed", "KIA_CEED"), ("Sitrak", "SITRAK")],
}

HUMANIZE_VALUE = {
    "incident_type": {"DTP": "ДТП", "BREAK": "Поломка"},
    "brand": {"KIA_CEED": "Kia Ceed", "SITRAK": "Sitrak"},
}

STATUS_FLOW = [
    ("arrive",     "⬜️ RA прибыл на место",             "RA прибыл на место"),
    ("inspect",    "⬜️ RA провел осмотр ВАТС",           "RA провел осмотр ВАТС"),
    ("decision",   "⬜️ Приняли решение о работах",       "Приняли решение о работах"),
    ("repair",     "⬜️ Ремонт завершен",                 "Ремонт завершен"),
    ("evacuation", "⬜️ Эвакуировали ВАТС",               "Эвакуировали ВАТС"),
    ("resume",     "⬜️ Движение возобновлено",           "Движение возобновлено"),
]

@dataclass
class Ticket:
    id: str
    user_id: int
    username: Optional[str]
    created_at: str
    incident_type: Optional[str] = None
    brand: Optional[str] = None
    plate_vats: Optional[str] = None
    plate_ref: Optional[str] = None
    location: Optional[str] = None
    problem_desc: Optional[str] = None
    notes: Optional[str] = None
    status_done_at: Dict[str, str] = field(default_factory=dict)
    closed_at: Optional[str] = None
    jira_main: Optional[str] = None
    jira_mech: Optional[str] = None  # сабтаск
    jira_ra: Optional[str] = None    # сабтаск

# =========================
# Хранилище (Postgres)
# =========================

class Store:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self):
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            async with self.pool.acquire() as con:
                await con.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                  id                TEXT PRIMARY KEY,
                  user_id           BIGINT NOT NULL,
                  username          TEXT,
                  created_at        TIMESTAMPTZ NOT NULL,
                  incident_type     TEXT,
                  brand             TEXT,
                  plate_vats        TEXT,
                  plate_ref         TEXT,
                  location          TEXT,
                  problem_desc      TEXT,
                  notes             TEXT,
                  closed_at         TIMESTAMPTZ,
                  jira_main         TEXT,
                  jira_mech         TEXT,
                  jira_ra           TEXT
                );
                """)
                await con.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_main TEXT;")
                await con.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_mech TEXT;")
                await con.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_ra TEXT;")
                await con.execute("""
                CREATE TABLE IF NOT EXISTS status_history (
                  id         BIGSERIAL PRIMARY KEY,
                  ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                  status_key TEXT NOT NULL,
                  ts         TIMESTAMPTZ NOT NULL
                );
                """)
                await con.execute("""
                CREATE TABLE IF NOT EXISTS status_done (
                  ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                  status_key TEXT NOT NULL,
                  ts         TIMESTAMPTZ NOT NULL,
                  PRIMARY KEY (ticket_id, status_key)
                );
                """)
                await con.execute("""
                CREATE TABLE IF NOT EXISTS input_history (
                  id         BIGSERIAL PRIMARY KEY,
                  ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                  field_key  TEXT NOT NULL,
                  value_text TEXT,
                  ts         TIMESTAMPTZ NOT NULL
                );
                """)

    async def _ensure_pool(self):
        if self.pool is None:
            await self.init()

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def create_ticket(self, t: Ticket) -> None:
        await self._ensure_pool()
        async with self.pool.acquire() as con:  # type: ignore
            await con.execute("""
            INSERT INTO tickets(
              id, user_id, username, created_at,
              incident_type, brand, plate_vats, plate_ref,
              location, problem_desc, notes,
              closed_at, jira_main, jira_mech, jira_ra
            ) VALUES(
              $1,$2,$3,$4,
              $5,$6,$7,$8,
              $9,$10,$11,
              $12,$13,$14,$15
            )
            """,
            t.id, t.user_id, t.username, from_iso(t.created_at),
            t.incident_type, t.brand, t.plate_vats, t.plate_ref,
            t.location, t.problem_desc, t.notes,
            None, t.jira_main, t.jira_mech, t.jira_ra)

    async def save_field(self, ticket_id: str, field: str, value: Any) -> None:
        await self._ensure_pool()
        async with self.pool.acquire() as con:  # type: ignore
            await con.execute(f"UPDATE tickets SET {field}=$1 WHERE id=$2", value, ticket_id)

    async def set_status_done(self, ticket_id: str, key: str, ts: datetime) -> None:
        await self._ensure_pool()
        async with self.pool.acquire() as con:  # type: ignore
            await con.execute("""
                INSERT INTO status_done(ticket_id, status_key, ts)
                VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
            """, ticket_id, key, ts)
            await con.execute("""
                INSERT INTO status_history(ticket_id, status_key, ts)
                VALUES ($1,$2,$3)
            """, ticket_id, key, ts)

    async def close_ticket(self, ticket_id: str, closed_ts: datetime) -> None:
        await self.save_field(ticket_id, "closed_at", closed_ts)

    async def log_input(self, ticket_id: str, field_key: str, value_text: Optional[str], ts: datetime) -> None:
        await self._ensure_pool()
        async with self.pool.acquire() as con:  # type: ignore
            await con.execute(
                "INSERT INTO input_history(ticket_id, field_key, value_text, ts) VALUES ($1,$2,$3,$4)",
                ticket_id, field_key, value_text, ts
            )

store = Store(DATABASE_URL)

# =========================
# Клавиатуры / рендеры
# =========================

def get_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return context.user_data.setdefault("draft", {})

def active_steps(context: ContextTypes.DEFAULT_TYPE) -> List[str]:
    draft = context.user_data.get("draft", {})
    ticket: Ticket = draft.get("ticket")
    steps = [*ALL_STEP_KEYS]
    if ticket and ticket.brand == "KIA_CEED":
        steps = [k for k in steps if k != "plate_ref"]
    return steps

def current_step_key(context: ContextTypes.DEFAULT_TYPE) -> str:
    draft = context.user_data.setdefault("draft", {})
    idx = draft.get("step_idx", 0)
    steps = active_steps(context)
    idx = max(0, min(idx, len(steps) - 1))
    draft["step_idx"] = idx
    return steps[idx]

def set_step_idx(context: ContextTypes.DEFAULT_TYPE, idx: int) -> None:
    draft = context.user_data.setdefault("draft", {})
    steps = active_steps(context)
    draft["step_idx"] = max(0, min(idx, len(steps) - 1))

def goto_next_step(context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data.setdefault("draft", {})
    steps = active_steps(context)
    draft["step_idx"] = min(draft.get("step_idx", 0) + 1, len(steps) - 1)

def goto_prev_step(context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data.setdefault("draft", {})
    draft["step_idx"] = max(draft.get("step_idx", 0) - 1, 0)

def is_last_step(context: ContextTypes.DEFAULT_TYPE) -> bool:
    draft = context.user_data.setdefault("draft", {})
    steps = active_steps(context)
    return draft.get("step_idx", 0) >= len(steps) - 1

def set_field_local(ticket: Ticket, key: str, val: Optional[str]) -> None:
    setattr(ticket, key, val)

def kb_choice(step_key: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for text, val in CHOICE_OPTIONS.get(step_key, []):
        rows.append([InlineKeyboardButton(text, callback_data=f"set|{step_key}|{val}")])
    if step_key != "incident_type":
        rows.append([InlineKeyboardButton("🚫 Не указывать", callback_data=f"nav|skip|{step_key}")])
        rows.append([InlineKeyboardButton("⬅ Назад", callback_data=f"nav|back|{step_key}")])
    return InlineKeyboardMarkup(rows)

def kb_nav(cur_key: str, back: bool = True, skip: bool = True) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if skip:
        rows.append([InlineKeyboardButton("🚫 Не указывать", callback_data=f"nav|skip|{cur_key}")])
    if back:
        rows.append([InlineKeyboardButton("⬅ Назад", callback_data=f"nav|back|{cur_key}")])
    return InlineKeyboardMarkup(rows or [])

def kb_summary(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" ✍️ Внести изменения", callback_data="summary|edit")],
        [InlineKeyboardButton("✅ Создать заявку",  callback_data="summary|create")],
    ])

def kb_after_main_created(ticket: Ticket) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Продолжить", callback_data=f"act|cont|{ticket.id}")],
        [InlineKeyboardButton("Требуется помощь дежмеха (сабтаск)", callback_data=f"act|mech|{ticket.id}")],
    ])

def kb_main_actions(ticket: Ticket) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Проблема решена", callback_data=f"act|solved|{ticket.id}")],
        [InlineKeyboardButton("🧰 Требуется RA (сабтаск)", callback_data=f"act|ra|{ticket.id}")],
    ])

def kb_status_with_evac(ticket: Ticket) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🚚 Требуется эвакуатор", callback_data=f"act|evac|{ticket.id}")])
    for key, wait_label, done_label in STATUS_FLOW:
        txt = f"✅ {done_label}" if key in ticket.status_done_at else wait_label
        rows.append([InlineKeyboardButton(txt, callback_data=f"st|{ticket.id}|{key}")])
    rows.append([InlineKeyboardButton("Закрыть заявку", callback_data=f"close|{ticket.id}")])
    return InlineKeyboardMarkup(rows)

def human(val: Optional[str], key: str) -> str:
    if val is None or val == "":
        return "—"
    return HUMANIZE_VALUE.get(key, {}).get(val, val)

def render_preview(ticket: Ticket) -> str:
    def esc(s: Optional[str]) -> str:
        return _html_escape(s or "")
    lines = [
        "⚠️ <b>Проверьте данные ⚠️</b>",
        "",
        f"<b>Заявка #{ticket.id}</b>",
        f"Тип происшествия: <b>{human(ticket.incident_type, 'incident_type')}</b>",
        f"Марка ВАТС: <b>{human(ticket.brand, 'brand')}</b>",
        f"Госномер ВАТС: <b>{format_vats_display(ticket.plate_vats)}</b>",
    ]
    if ticket.brand != "KIA_CEED":
        lines.append(f"Госномер рефа/пп: <b>{format_ref_display(ticket.plate_ref)}</b>")
    lines += [
        f"Местоположение: <b>{esc(ticket.location) or '—'}</b>",
        f"Описание проблемы: <b>{esc(ticket.problem_desc) or '—'}</b>",
        f"Особые отметки: <b>{esc(ticket.notes) or '—'}</b>",
        "",
    ]
    return "\n".join(lines)

def render_status_header(ticket: Ticket) -> str:
    extras = []
    if ticket.jira_main:
        extras.append(f"Jira: {ticket.jira_main}")
    if ticket.jira_mech:
        extras.append(f"Дежмех: {ticket.jira_mech}")
    if ticket.jira_ra:
        extras.append(f"RA: {ticket.jira_ra}")
    extra = f"\n" + " | ".join(extras) if extras else ""
    return f"✅ Заявка #{ticket.id} — статусный экран{extra}"

# =========================
# Jira: утилиты и операции
# =========================

def _adf_text_node(text: str) -> dict:
    return {"type": "text", "text": text}

def _adf_paragraph_node(text: str) -> dict:
    return {"type": "paragraph", "content": [_adf_text_node(text)]} if text else {"type": "paragraph"}

def _adf_doc_from_plain(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    lines = text.splitlines() or [text]
    content = [{"type": "paragraph", "content": [_adf_text_node(ln)]} for ln in lines]
    return {"type": "doc", "version": 1, "content": content}

def _select_value_or_text_or_adf(kind: str, raw_value: Optional[str], display_value: Optional[str] = None):
    if raw_value is None and display_value is None:
        return None
    k = (kind or "").strip().lower()
    if k == "select":
        val = (display_value or raw_value or "").strip()
        return {"value": val} if val else None
    if k == "multiselect":
        val = (display_value or raw_value or "").strip()
        return [{"value": val}] if val else []
    if k == "adf":
        return _adf_doc_from_plain(display_value or raw_value)
    if k == "date":
        return format_jira_date(utc_now())
    if k == "time":
        return format_jira_time(utc_now())
    if k == "datetime":
        return format_jira_datetime(utc_now())
    return display_value or raw_value

def _option_from_code(code: Optional[str], mapping: Dict[str, str]) -> Optional[str]:
    if not code:
        return None
    return mapping.get(code, code)

def render_jira_summary(ticket: Ticket) -> str:
    itype = HUMANIZE_VALUE.get("incident_type", {}).get(ticket.incident_type or "", ticket.incident_type or "")
    brand = HUMANIZE_VALUE.get("brand", {}).get(ticket.brand or "", ticket.brand or "")
    plate = format_vats_display(ticket.plate_vats)
    base = f"[{itype or '-'}] {brand or '-'}"
    if plate and plate != "—":
        base += f" — {plate}"
    return base

def build_fields_main(ticket: Ticket) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": render_jira_summary(ticket),
        "labels": ["ptb", "auto-ticket"],
    }
    if JIRA_ISSUE_TYPE_MAIN_ID:
        fields["issuetype"] = {"id": JIRA_ISSUE_TYPE_MAIN_ID}
    else:
        fields["issuetype"] = {"name": JIRA_ISSUE_TYPE_MAIN}

    if JIRA_CF_INCIDENT_TYPE:
        display = _option_from_code(ticket.incident_type, INCIDENT_TYPE_OPTION_MAP)
        fields[JIRA_CF_INCIDENT_TYPE] = _select_value_or_text_or_adf(JIRA_CF_INCIDENT_TYPE_KIND, ticket.incident_type, display)
    if JIRA_CF_BRAND:
        display = _option_from_code(ticket.brand, BRAND_OPTION_MAP)
        fields[JIRA_CF_BRAND] = _select_value_or_text_or_adf(JIRA_CF_BRAND_KIND, ticket.brand, display)

    if JIRA_CF_PLATE_VATS:
        fields[JIRA_CF_PLATE_VATS] = _select_value_or_text_or_adf(
            JIRA_CF_PLATE_VATS_KIND, ticket.plate_vats, format_vats_display(ticket.plate_vats) if ticket.plate_vats else None
        )
    if JIRA_CF_PLATE_REF and ticket.brand != "KIA_CEED":
        fields[JIRA_CF_PLATE_REF] = _select_value_or_text_or_adf(
            JIRA_CF_PLATE_REF_KIND, ticket.plate_ref, format_ref_display(ticket.plate_ref) if ticket.plate_ref else None
        )
    if JIRA_CF_LOCATION:
        fields[JIRA_CF_LOCATION] = _select_value_or_text_or_adf(JIRA_CF_LOCATION_KIND, ticket.location)
    if JIRA_CF_PROBLEM_DESC:
        fields[JIRA_CF_PROBLEM_DESC] = _select_value_or_text_or_adf(JIRA_CF_PROBLEM_DESC_KIND, ticket.problem_desc)
    if JIRA_CF_NOTES:
        fields[JIRA_CF_NOTES] = _select_value_or_text_or_adf(JIRA_CF_NOTES_KIND, ticket.notes)

    try:
        created_dt = from_iso(ticket.created_at)
    except Exception:
        created_dt = utc_now()

    if JIRA_CF_INCIDENT_DATE:
        if JIRA_CF_INCIDENT_DATE_KIND == "date":
            fields[JIRA_CF_INCIDENT_DATE] = format_jira_date(created_dt)
        else:
            fields[JIRA_CF_INCIDENT_DATE] = _select_value_or_text_or_adf(JIRA_CF_INCIDENT_DATE_KIND, None)
    if JIRA_CF_INCIDENT_TIME:
        kind = (JIRA_CF_INCIDENT_TIME_KIND or "").lower()
        if kind == "time":
            fields[JIRA_CF_INCIDENT_TIME] = format_jira_time(created_dt)
        elif kind == "datetime":
            fields[JIRA_CF_INCIDENT_TIME] = format_jira_datetime(created_dt)
        else:
            fields[JIRA_CF_INCIDENT_TIME] = format_jira_time(created_dt)

    def yesno(v: bool) -> Dict[str, str]:
        return {"value": JIRA_OPT_YES if v else JIRA_OPT_NO}
    if JIRA_CF_FLAG_REQUIRE_MECH and JIRA_CF_FLAG_REQUIRE_MECH_KIND == "select":
        fields[JIRA_CF_FLAG_REQUIRE_MECH] = yesno(False)
    if JIRA_CF_FLAG_PROBLEM_SOLVED and JIRA_CF_FLAG_PROBLEM_SOLVED_KIND == "select":
        fields[JIRA_CF_FLAG_PROBLEM_SOLVED] = yesno(False)
    if JIRA_CF_FLAG_REQUIRE_RA and JIRA_CF_FLAG_REQUIRE_RA_KIND == "select":
        fields[JIRA_CF_FLAG_REQUIRE_RA] = yesno(False)

    return fields

# --- Сабтаски (универсальный билдер) ---

def _issuetype_payload_from(id_value: Optional[str], name_value: Optional[str], prefer_id: bool) -> Dict[str, str]:
    if prefer_id and id_value:
        return {"id": id_value}
    if name_value:
        return {"name": name_value}
    if prefer_id and JIRA_SUBTASK_TYPE_ID:
        return {"id": JIRA_SUBTASK_TYPE_ID}
    return {"name": JIRA_SUBTASK_TYPE or "Sub-task"}

def build_fields_subtask_try(
    summary: str,
    *,
    project_key: str,
    parent_key: Optional[str] = None,
    parent_id: Optional[str] = None,
    issuetype_id: Optional[str] = None,
    issuetype_name: Optional[str] = None,
    prefer_id: bool = True,
    labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not parent_key and not parent_id:
        raise ValueError("Нужен parent_key или parent_id")
    parent = {"key": parent_key} if parent_key else {"id": parent_id}
    return {
        # ВАЖНО: указываем project (ключ проекта родителя), чтобы пройти валидацию
        "project": {"key": project_key},
        "summary": summary,
        "parent": parent,
        "issuetype": _issuetype_payload_from(issuetype_id, issuetype_name, prefer_id),
        "labels": labels or [],
    }

def format_jira_error(status: int, body_text: str) -> str:
    lines = [f"HTTP {status}"]
    t = (body_text or "").strip()
    try:
        data = json.loads(t) if t else {}
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        em = data.get("errorMessages")
        if isinstance(em, list) and em:
            lines.append("errorMessages:")
            for x in em:
                lines.append(f"  - {x}")
        errs = data.get("errors")
        if isinstance(errs, dict) and errs:
            lines.append("field errors:")
            for k, v in errs.items():
                lines.append(f"  - {k}: {v}")
    elif t:
        lines.append("body (text):")
        lines.append(t[:2000])
    return "\n".join(lines)

async def jira_create(fields: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if not (JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN):
        return None, "Не задана конфигурация Jira (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)."
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            logging.info("→ JIRA POST %s fields=%s", url, json.dumps(fields, ensure_ascii=False)[:2000])
            r = await client.post(url, json={"fields": fields}, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return None, f"Сеть/подключение: {e!s}"
    if r.status_code == 201:
        try:
            data = r.json()
            return data.get("key"), None
        except Exception:
            return None, f"201 Created, но не удалось разобрать ответ: {r.text[:500]}"
    return None, format_jira_error(r.status_code, r.text)

async def jira_update_fields(issue_key: str, patch_fields: Dict[str, Any]) -> Optional[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.put(url, json={"fields": patch_fields}, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return f"Сеть/подключение: {e!s}"
    if r.status_code in (204, 200):
        return None
    return format_jira_error(r.status_code, r.text)

async def jira_link_issues(outward_key: str, inward_key: str, link_type: str = JIRA_LINK_TYPE) -> Optional[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issueLink"
    payload = {"type": {"name": link_type},
               "outwardIssue": {"key": outward_key},
               "inwardIssue": {"key": inward_key}}
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(url, json=payload, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return f"Сеть/подключение: {e!s}"
    if r.status_code in (201, 200):
        return None
    return format_jira_error(r.status_code, r.text)

async def jira_get_issue_basic(issue_key: str) -> Tuple[Optional[dict], Optional[str]]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(url, params={"fields": "project"}, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return None, f"Сеть/подключение: {e!s}"
    if r.status_code == 200:
        try:
            return r.json(), None
        except Exception:
            return None, f"200 OK, но не удалось разобрать ответ: {r.text[:500]}"
    return None, format_jira_error(r.status_code, r.text)

async def jira_get_issuetypes() -> Tuple[Optional[List[dict]], Optional[str]]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issuetype"
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(url, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return None, f"Сеть/подключение: {e!s}"
    if r.status_code == 200:
        try:
            return r.json(), None
        except Exception:
            return None, f"200 OK, но не удалось разобрать ответ: {r.text[:500]}"
    return None, format_jira_error(r.status_code, r.text)

async def jira_guess_subtask_type_id() -> Optional[str]:
    types, err = await jira_get_issuetypes()
    if not types:
        logging.warning("⚠️ Не удалось получить список типов задач: %s", err or "")
        return None
    for t in types:
        if t.get("subtask") is True:
            return t.get("id")
    return None

async def jira_get_project_createmeta_for_subtask(project_key: str, subtask_type_id: str) -> Tuple[Optional[dict], Optional[str]]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"
    params = {
        "projectKeys": project_key,
        "issuetypeIds": subtask_type_id,
        "expand": "projects.issuetypes.fields",
    }
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(url, params=params, auth=(JIRA_EMAIL, JIRA_API_TOKEN))
        except httpx.RequestError as e:
            return None, f"Сеть/подключение: {e!s}"
    if r.status_code == 200:
        try:
            return r.json(), None
        except Exception:
            return None, f"200 OK, но не удалось разобрать ответ: {r.text[:800]}"
    return None, format_jira_error(r.status_code, r.text)

def _extract_required_fields_from_createmeta(createmeta: dict) -> List[Tuple[str, dict]]:
    req: List[Tuple[str, dict]] = []
    projects = (createmeta or {}).get("projects") or []
    for p in projects:
        for it in (p.get("issuetypes") or []):
            fields = it.get("fields") or {}
            for fid, fdef in fields.items():
                if fdef.get("required"):
                    req.append((fid, fdef))
    return req

# =========================
# Черновик и шаги
# =========================

async def start_new_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Ticket:
    draft = get_draft(context)
    ticket = Ticket(
        id=short_id(),
        user_id=update.effective_user.id,
        username=update.effective_user.username,
        created_at=iso(utc_now()),
    )
    draft["ticket"] = ticket
    draft["step_idx"] = 0
    draft["editing"] = False
    await store.create_ticket(ticket)
    return ticket

async def ask_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = current_step_key(context)
    prompt = QUESTION_LABELS[key]
    kind = STEP_INPUT_KIND[key]
    if kind == "choice":
        await context.bot.send_message(update.effective_chat.id, prompt, reply_markup=kb_choice(key))
    else:
        await context.bot.send_message(update.effective_chat.id, prompt, reply_markup=kb_nav(cur_key=key, back=True, skip=True))

# =========================
# Хэндлеры
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    await start_new_draft(update, context)
    await context.bot.send_message(update.effective_chat.id, "Пожалуйста, выбери тип происшествия:", reply_markup=kb_choice("incident_type"))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    draft = get_draft(context)
    if "ticket" not in draft:
        await cmd_start(update, context)
        return

    ticket: Ticket = draft["ticket"]
    key = current_step_key(context)
    kind = STEP_INPUT_KIND[key]
    text = (update.message.text or "").strip()

    if kind == "plate":
        norm = normalize_vats_plate(text, brand=ticket.brand)
        if not norm:
            if ticket.brand == "KIA_CEED":
                pattern = "Буква + 3 цифры + 2 буквы + 2–3 цифры"
                example = "A123BC 77"
            else:
                pattern = "Буква + 3–4 цифры + 2 буквы + 2–3 цифры"
                example = "A1234BC 77"
            await context.bot.send_message(
                update.effective_chat.id,
                f"❌ <b>Ошибка в госномере</b> ❌\nОжидается: {pattern}\nПример: {example}\n"
                f"Можно использовать латиницу или кириллицу — важны только количество и порядок.",
                reply_markup=kb_nav(cur_key=key, back=True, skip=True),
            )
            return
        set_field_local(ticket, key, norm)
        await store.save_field(ticket.id, key, norm)
        await store.log_input(ticket.id, key, norm, utc_now())

    elif kind == "plate_ref":
        norm = normalize_ref_plate(text)
        if not norm:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ <b>Неверный формат</b> ❌\nОжидается: 2 буквы + 4 цифры + 2–3 цифры (регион)\n"
                "Пример: AB1234 77\nМожно использовать латиницу или кириллицу.",
                reply_markup=kb_nav(cur_key=key, back=True, skip=True),
            )
            return
        set_field_local(ticket, key, norm)
        await store.save_field(ticket.id, key, norm)
        await store.log_input(ticket.id, key, norm, utc_now())

    elif kind == "text":
        if not text:
            await context.bot.send_message(update.effective_chat.id, "❌<b>Пустое значение</b>❌ \nВведите текст или нажмите <b>«Не указывать»</b>", reply_markup=kb_nav(cur_key=key, back=True, skip=True))
            return
        set_field_local(ticket, key, text)
        await store.save_field(ticket.id, key, text)
        await store.log_input(ticket.id, key, text, utc_now())

    else:
        return

    if draft.get("editing"):
        draft["editing"] = False
        await context.bot.send_message(update.effective_chat.id, render_preview(ticket), reply_markup=kb_summary(ticket.id))
        return

    if is_last_step(context):
        await context.bot.send_message(update.effective_chat.id, render_preview(ticket), reply_markup=kb_summary(ticket.id))
        return

    goto_next_step(context)
    await ask_step(update, context)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    draft = get_draft(context)
    if "ticket" not in draft:
        await cmd_start(update, context)
        return
    ticket: Ticket = draft["ticket"]

    # Навигация по анкете
    if data.startswith("nav|"):
        parts = data.split("|")
        action = parts[1] if len(parts) > 1 else ""
        cur_key = parts[2] if len(parts) > 2 else current_step_key(context)

        if action == "back":
            if draft.get("editing"):
                draft["editing"] = False
                await safe_edit_message_text(query, text=render_preview(ticket), reply_markup=kb_summary(ticket.id))
                return
            steps = active_steps(context)
            if cur_key in steps:
                set_step_idx(context, max(0, steps.index(cur_key) - 1))
            else:
                goto_prev_step(context)
            next_key = current_step_key(context)
            if STEP_INPUT_KIND[next_key] == "choice":
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_choice(next_key))
            else:
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_nav(cur_key=next_key, back=True, skip=True))
            return

        if action == "skip":
            set_field_local(ticket, cur_key, None)
            await store.save_field(ticket.id, cur_key, None)
            await store.log_input(ticket.id, cur_key, None, utc_now())
            if draft.get("editing") or is_last_step(context):
                await safe_edit_message_text(query, text=render_preview(ticket), reply_markup=kb_summary(ticket.id))
                return
            goto_next_step(context)
            next_key = current_step_key(context)
            if STEP_INPUT_KIND[next_key] == "choice":
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_choice(next_key))
            else:
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_nav(cur_key=next_key, back=True, skip=True))
            return

    # Выбор значений (choice)
    if data.startswith("set|"):
        _, field_key, value = data.split("|", 2)
        if field_key not in ALL_STEP_KEYS:
            return
        set_field_local(ticket, field_key, value)
        await store.save_field(ticket.id, field_key, value)
        await store.log_input(ticket.id, field_key, value, utc_now())
        if field_key == "brand":
            steps = active_steps(context)
            cur = current_step_key(context)
            if cur not in steps:
                set_step_idx(context, 0)
        if draft.get("editing"):
            draft["editing"] = False
            await safe_edit_message_text(query, text=render_preview(ticket), reply_markup=kb_summary(ticket.id))
        else:
            if current_step_key(context) == field_key:
                goto_next_step(context)
            if is_last_step(context):
                await safe_edit_message_text(query, text=render_preview(ticket), reply_markup=kb_summary(ticket.id))
                return
            next_key = current_step_key(context)
            if STEP_INPUT_KIND[next_key] == "choice":
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_choice(next_key))
            else:
                await safe_edit_message_text(query, text=QUESTION_LABELS[next_key], reply_markup=kb_nav(cur_key=next_key, back=True, skip=True))
        return

    # Итог / правки / создание основной задачи
    if data == "summary|edit":
        await safe_edit_message_text(query, text="Выберите пункт для изменения:", reply_markup=kb_edit_field_list(context))
        return

    if data == "edit|cancel":
        await safe_edit_message_text(query, text=render_preview(ticket), reply_markup=kb_summary(ticket.id))
        return

    if data.startswith("edit|field|"):
        _, _, field_key = data.split("|", 2)
        if field_key not in active_steps(context):
            return
        draft["editing"] = True
        set_step_idx(context, active_steps(context).index(field_key))
        if STEP_INPUT_KIND[field_key] == "choice":
            await safe_edit_message_text(query, text=QUESTION_LABELS[field_key], reply_markup=kb_choice(field_key))
        else:
            await safe_edit_message_text(query, text=QUESTION_LABELS[field_key], reply_markup=kb_nav(cur_key=field_key, back=True, skip=True))
        return

    if data == "summary|create":
        fields_main = build_fields_main(ticket)
        jira_key, jira_err = await jira_create(fields_main)
        if jira_key:
            ticket.jira_main = jira_key
            await store.save_field(ticket.id, "jira_main", jira_key)
            await safe_edit_message_text(
                query,
                text=f"✅ Заявка #{ticket.id} создана.\nJira: <b>{jira_key}</b>",
                reply_markup=kb_after_main_created(ticket)
            )
        else:
            safe_err = jira_err or "Неизвестная ошибка"
            await safe_edit_message_text(query, text=f"⚠️ Не удалось создать задачу в Jira.\n<pre>{_html_escape(safe_err)}</pre>", parse_mode=ParseMode.HTML)
        return

    # Дальнейшие действия после создания основной
    if data.startswith("act|"):
        _, action, t_id = data.split("|", 2)
        if t_id != ticket.id:
            return

        if action == "cont":
            await safe_edit_message_text(query, text=f"Выберите действие (Jira: {ticket.jira_main or '—'})", reply_markup=kb_main_actions(ticket))
            return

        # --- ДЕЖМЕХ КАК САБЗАДАЧА ---
        if action == "mech":
            if not ticket.jira_main:
                await safe_edit_message_text(query, text="Сначала создайте основную задачу в Jira.")
                return

            parent_basic, basic_err = await jira_get_issue_basic(ticket.jira_main)
            if not parent_basic:
                await safe_edit_message_text(query, text=f"⚠️ Не удалось получить данные родителя {ticket.jira_main}.\n<pre>{_html_escape(basic_err or '')}</pre>", parse_mode=ParseMode.HTML)
                return
            parent_id = parent_basic.get("id")
            project_key = (((parent_basic.get("fields") or {}).get("project") or {}).get("key")) or JIRA_PROJECT_KEY

            effective_subtask_id = JIRA_SUBTASK_TYPE_ID or (await jira_guess_subtask_type_id())

            req_fields: List[Tuple[str, dict]] = []
            if effective_subtask_id:
                cm, _ = await jira_get_project_createmeta_for_subtask(project_key, effective_subtask_id)
                if cm:
                    req_fields = _extract_required_fields_from_createmeta(cm)

            if not ticket.jira_mech:
                summary = f"Дежмех — {render_jira_summary(ticket)}"
                attempts = [
                    ("parent.id + issuetype.id",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_id=parent_id,
                                              issuetype_id=effective_subtask_id,
                                              prefer_id=True,
                                              labels=["ptb", "auto-ticket", "mech"])),
                    ("parent.id + issuetype.name",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_id=parent_id,
                                              issuetype_name=JIRA_SUBTASK_TYPE,
                                              prefer_id=False,
                                              labels=["ptb", "auto-ticket", "mech"])),
                    ("parent.key + issuetype.id",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_key=ticket.jira_main,
                                              issuetype_id=effective_subtask_id,
                                              prefer_id=True,
                                              labels=["ptb", "auto-ticket", "mech"])),
                    ("parent.key + issuetype.name",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_key=ticket.jira_main,
                                              issuetype_name=JIRA_SUBTASK_TYPE,
                                              prefer_id=False,
                                              labels=["ptb", "auto-ticket", "mech"])),
                ]
                last_errs: List[str] = []
                created_key: Optional[str] = None
                for label, fields_try in attempts:
                    key_try, err_try = await jira_create(fields_try)
                    if key_try:
                        created_key = key_try
                        break
                    last_errs.append(f"[{label}]\n{err_try or '(нет текста)'}")

                if not created_key:
                    msg = ["Не удалось создать подзадачу «Дежмех». Отчёт по попыткам:"]
                    msg.extend(last_errs)
                    if req_fields == []:
                        msg += [
                            "",
                            "Проверь настройки проекта в Jira:",
                            f"— Схема типов проекта «{project_key}» должна содержать тип «Подзадача».",
                            "— Проверь правильность JIRA_SUBTASK_TYPE_ID / JIRA_SUBTASK_TYPE в .env.",
                        ]
                    await safe_edit_message_text(query, text=f"⚠️ <pre>{_html_escape('\\n\\n'.join(msg))}</pre>", parse_mode=ParseMode.HTML)
                    return

                ticket.jira_mech = created_key
                await store.save_field(ticket.id, "jira_mech", created_key)

                if JIRA_CF_FLAG_REQUIRE_MECH and JIRA_CF_FLAG_REQUIRE_MECH_KIND == "select":
                    err2 = await jira_update_fields(ticket.jira_main, {JIRA_CF_FLAG_REQUIRE_MECH: {"value": JIRA_OPT_YES}})
                    if err2:
                        await context.bot.send_message(update.effective_chat.id, f"⚠️ Не удалось обновить флаг «Требуется дежмех»: {err2}")

            await safe_edit_message_text(query, text=f"Дежмех (сабтаск) создан: {ticket.jira_mech}. Выберите действие:", reply_markup=kb_main_actions(ticket))
            return

        if action == "solved":
            if not ticket.jira_main:
                await safe_edit_message_text(query, text="Сначала создайте основную задачу.")
                return
            if JIRA_CF_FLAG_PROBLEM_SOLVED and JIRA_CF_FLAG_PROBLEM_SOLVED_KIND == "select":
                err = await jira_update_fields(ticket.jira_main, {JIRA_CF_FLAG_PROBLEM_SOLVED: {"value": JIRA_OPT_YES}})
                if err:
                    await safe_edit_message_text(query, text=f"⚠️ Не удалось выставить «Проблема решена»: {err}")
                    return
            await safe_edit_message_text(query, text="✅ Отмечено как «Проблема решена».")
            return

        # --- RA как сабтаск ---
        if action == "ra":
            if not ticket.jira_main:
                await safe_edit_message_text(query, text="Сначала создайте основную задачу (нет родителя для RA).")
                return

            parent_basic, basic_err = await jira_get_issue_basic(ticket.jira_main)
            if not parent_basic:
                await safe_edit_message_text(query, text=f"⚠️ Не удалось получить данные родителя {ticket.jira_main}.\n<pre>{_html_escape(basic_err or '')}</pre>", parse_mode=ParseMode.HTML)
                return
            parent_id = parent_basic.get("id")
            project_key = (((parent_basic.get("fields") or {}).get("project") or {}).get("key")) or JIRA_PROJECT_KEY

            effective_subtask_id = JIRA_SUBTASK_TYPE_ID or (await jira_guess_subtask_type_id())

            req_fields: List[Tuple[str, dict]] = []
            if effective_subtask_id:
                cm, _ = await jira_get_project_createmeta_for_subtask(project_key, effective_subtask_id)
                if cm:
                    req_fields = _extract_required_fields_from_createmeta(cm)

            if not ticket.jira_ra:
                summary = f"RA — {render_jira_summary(ticket)}"
                attempts = [
                    ("parent.id + issuetype.id",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_id=parent_id,
                                              issuetype_id=effective_subtask_id,
                                              prefer_id=True,
                                              labels=["ptb", "auto-ticket", "ra"])),
                    ("parent.id + issuetype.name",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_id=parent_id,
                                              issuetype_name=JIRA_SUBTASK_TYPE,
                                              prefer_id=False,
                                              labels=["ptb", "auto-ticket", "ra"])),
                    ("parent.key + issuetype.id",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_key=ticket.jira_main,
                                              issuetype_id=effective_subtask_id,
                                              prefer_id=True,
                                              labels=["ptb", "auto-ticket", "ra"])),
                    ("parent.key + issuetype.name",
                     build_fields_subtask_try(summary,
                                              project_key=project_key,
                                              parent_key=ticket.jira_main,
                                              issuetype_name=JIRA_SUBTASK_TYPE,
                                              prefer_id=False,
                                              labels=["ptb", "auto-ticket", "ra"])),
                ]
                last_errs: List[str] = []
                created_key: Optional[str] = None
                for label, fields_try in attempts:
                    key_try, err_try = await jira_create(fields_try)
                    if key_try:
                        created_key = key_try
                        break
                    last_errs.append(f"[{label}]\n{err_try or '(нет текста)'}")

                if not created_key:
                    msg = ["Не удалось создать подзадачу RA. Отчёт по попыткам:"]
                    msg.extend(last_errs)
                    if req_fields == []:
                        msg += [
                            "",
                            "Проверь настройки проекта в Jira:",
                            f"— Схема типов проекта «{project_key}» должна содержать тип «Подзадача».",
                            "— Проверь правильность JIRA_SUBTASK_TYPE_ID / JIRA_SUBTASK_TYPE в .env.",
                        ]
                    await safe_edit_message_text(query, text=f"⚠️ <pre>{_html_escape('\\n\\n'.join(msg))}</pre>", parse_mode=ParseMode.HTML)
                    return

                ticket.jira_ra = created_key
                await store.save_field(ticket.id, "jira_ra", created_key)

                if JIRA_CF_FLAG_REQUIRE_RA and JIRA_CF_FLAG_REQUIRE_RA_KIND == "select":
                    err2 = await jira_update_fields(ticket.jira_main, {JIRA_CF_FLAG_REQUIRE_RA: {"value": JIRA_OPT_YES}})
                    if err2:
                        await context.bot.send_message(update.effective_chat.id, f"⚠️ Не удалось обновить флаг «Требуется RA»: {err2}")

            await safe_edit_message_text(query, text=render_status_header(ticket), reply_markup=kb_status_with_evac(ticket))
            return

        if action == "evac":
            if not DISPATCH_CHAT_ID:
                await safe_edit_message_text(query, text="Не задан DISPATCH_CHAT_ID в .env — некуда отправлять сообщение для диспетчера.")
                return
            invite_link = None
            try:
                link = await context.bot.create_chat_invite_link(chat_id=DISPATCH_CHAT_ID, creates_join_request=False)
                invite_link = link.invite_link
            except TelegramError:
                invite_link = None

            text_msg = f"🚨 Требуется диспетчер по заявке #{ticket.id}. Jira: {ticket.jira_main or '—'}"
            if invite_link:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть беседу", url=invite_link)]])
                await context.bot.send_message(chat_id=DISPATCH_CHAT_ID, text=text_msg, reply_markup=markup)
            else:
                await context.bot.send_message(chat_id=DISPATCH_CHAT_ID, text=text_msg)

            await safe_edit_message_text(query, text="🧷 Запрос диспетчеру отправлен.", reply_markup=kb_status_with_evac(ticket))
            return

    # Статусы
    if data.startswith("st|"):
        _, ticket_id, st_key = data.split("|", 2)
        if ticket_id != ticket.id:
            return
        valid = {k for (k, *_rest) in STATUS_FLOW}
        if st_key not in valid:
            return
        now = utc_now()
        if st_key not in ticket.status_done_at:
            ticket.status_done_at[st_key] = iso(now)
            await store.set_status_done(ticket.id, st_key, now)
        else:
            await store.set_status_done(ticket.id, st_key, now)
        await safe_edit_reply_markup(query, reply_markup=kb_status_with_evac(ticket))
        return

    # Закрыть заявку (локально)
    if data.startswith("close|"):
        _, ticket_id = data.split("|", 1)
        if ticket_id != ticket.id:
            return
        now = utc_now()
        ticket.closed_at = iso(now)
        await store.close_ticket(ticket.id, now)
        await safe_edit_message_text(query, text="✅ Заявка закрыта локально. (В Jira закрытие не выполнялось)")
        return

# =========================
# Error handler и запуск
# =========================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update: %s", update)

def kb_edit_field_list(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    rows = []
    for key in active_steps(context):
        rows.append([InlineKeyboardButton(QUESTION_LABELS[key], callback_data=f"edit|field|{key}")])
    rows.append([InlineKeyboardButton("⬅ Назад к итогу", callback_data="edit|cancel")])
    return InlineKeyboardMarkup(rows)

def build_app() -> Application:
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=70.0,
        write_timeout=30.0,
        pool_timeout=70.0,
    )
    defaults = Defaults(parse_mode=ParseMode.HTML)

    async def _post_init(app: Application) -> None:
        await store.init()
        if not JIRA_SUBTASK_TYPE_ID:
            logger.info("ℹ️ JIRA_SUBTASK_TYPE_ID не задан — попытаемся авто-определить тип сабтаска при первом создании.")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .defaults(defaults)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start, filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app

# ---- запуск
async def _run_with_updater(app: Application) -> None:
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await store.close()
# =========================
# API для Telegram WebApp
# =========================

from fastapi import FastAPI, Request
from telegram import Bot

# создаём API и бота
api = FastAPI()
bot_api = Bot(token=BOT_TOKEN)

@api.post("/api/from_webapp")
async def from_webapp(req: Request):
    """
    Этот маршрут принимает данные из твоего React-приложения (WebApp)
    и отправляет уведомление пользователю в Telegram.
    """
    data = await req.json()
    user_id = data.get("user_id")
    action = data.get("action")

    if user_id:
        msg = f"📩 Пользователь нажал кнопку в WebApp! (действие: {action})"
        try:
            await bot_api.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            return {"status": "error", "details": str(e)}

    return {"status": "ok"}

if __name__ == "__main__":
    if not BOT_TOKEN or not DATABASE_URL:
        raise SystemExit("Заполните .env: BOT_TOKEN, DATABASE_URL")
    application = build_app()
    if getattr(application, "updater", None) is not None:
        asyncio.run(_run_with_updater(application))
    else:
        application.run_polling()
