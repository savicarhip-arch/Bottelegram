import asyncio
import sqlite3
import logging
import html
import uuid
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
)

# --- НАСТРОЙКИ (вписаны прямо в код) ---
BOT_TOKEN = "8622460028:AAGXOwLID4FzG8nueE4H5VSlEAGZUjE-4aQ"
ADMIN_ID = 6949049864
BYTECOIN_API_KEY = "bc_live_NILsKMSyZ_2Q75Y_4G9FEyCWDH5HEy8ktxAX9ORYTks"
AXIONNA_API_KEY = "AX-JzhYfPNwTHXiBy3TXHG0CWuM"

# Статичные настройки
REVIEWS_LINK = "https://t.me/+BT6XOcxTInMwOTZi"
SUPPORT_USERNAME = "@Klysha_Tag"
REFERRAL_REWARD = 500
AD_VIEW_REWARD = 100
CHECK_SUBS_INTERVAL = 300
AXIONNA_VIEWS_URL = "https://axionna.org/api/views"
BYTECOIN_API_URL = "https://bytecoin.space/api/public/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

MENU_BUTTONS = [
    "💸 Заработок", "📺 Смотреть рекламу", "💰 Общий баланс", "👥 Рефералы", "📝 Отзывы",
    "⚙️ Админ-панель",
    "➕ Добавить задание", "📋 Список заданий",
    "📥 Заявки на задания", "💸 Заявки на вывод",
    "💰 Баланс Bytecoin", "🧪 Проверить канал", "🧪 Тест Axionna",
    "◀️ В главное меню", "◀️ Отмена"
]

PENDING_TASK = {}
BOT_USERNAME_CACHE = None


def esc(text):
    return html.escape(str(text if text is not None else ""))


async def get_bot_username():
    global BOT_USERNAME_CACHE
    if BOT_USERNAME_CACHE is None:
        me = await bot.get_me()
        BOT_USERNAME_CACHE = me.username
    return BOT_USERNAME_CACHE


# ==================================================
# --- BYTECOIN API ---
# ==================================================
async def bc_get_service_info():
    if not BYTECOIN_API_KEY:
        return -1, {"error": "API-ключ не установлен"}
    headers = {"X-API-Key": BYTECOIN_API_KEY}
    url = f"{BYTECOIN_API_URL}/service/info"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                raw = await resp.text()
                try:
                    data = await resp.json()
                except Exception:
                    return resp.status, {"error": f"Не JSON: {raw[:200]}"}
                return resp.status, data
    except asyncio.TimeoutError:
        return -2, {"error": "Timeout"}
    except aiohttp.ClientConnectorError as e:
        return -3, {"error": f"Нет соединения: {e}"}
    except Exception as e:
        return -4, {"error": f"{type(e).__name__}: {e}"}


async def bc_transfer(user_id: int, amount: float):
    if not BYTECOIN_API_KEY:
        return -1, {"status": "error", "error": "API-ключ не установлен"}
    headers = {
        "X-API-Key": BYTECOIN_API_KEY,
        "Idempotency-Key": f"payout-{user_id}-{uuid.uuid4()}",
        "Content-Type": "application/json"
    }
    sum_str = f"{float(amount):.8f}"
    payload = {"user_id": int(user_id), "sum": sum_str}
    url = f"{BYTECOIN_API_URL}/service/transfer"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                raw = await resp.text()
                try:
                    data = await resp.json()
                except Exception:
                    return resp.status, {"status": "error", "error": f"Не JSON: {raw[:200]}"}
                return resp.status, data
    except asyncio.TimeoutError:
        return -2, {"status": "error", "error": "Timeout"}
    except Exception as e:
        return -4, {"status": "error", "error": f"{type(e).__name__}: {e}"}


# ==================================================
# --- AXIONNA API ---
# ==================================================
async def show_axionna_ad(chat_id: int, greeting: bool = False):
    headers = {
        "Authorization": f"Bearer {AXIONNA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"chat_id": chat_id, "greeting": greeting, "impression_id": str(uuid.uuid4())}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AXIONNA_VIEWS_URL, json=payload,
                                    headers=headers, timeout=20) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    return False, f"❌ Сервер вернул не JSON (HTTP {resp.status})"
                status = data.get("status", "")
                delivered = data.get("delivered", False)
                if status == "ok" and delivered:
                    return True, "✅ Реклама показана"
                if status == "not_ok" or not delivered:
                    return False, "📭 Реклама временно недоступна. Попробуйте позже."
                result = data.get("result") or ""
                errors_map = {
                    "AdLimited": "⏳ Лимит рекламы достигнут.",
                    "NoAds": "📭 Реклама временно недоступна.",
                    "RevokedTokenError": "❌ Неверный API-ключ!",
                    "UserForbiddenError": "🚫 Вы заблокировали бота.",
                    "TooManyRequestsError": "⏳ Слишком много запросов.",
                    "BotIsNotEnabled": "❌ Бот не включён в Axionna!",
                    "Banned": "🚫 Бот заблокирован.",
                    "InReview": "⏳ Бот на модерации.",
                }
                if result in errors_map:
                    return False, errors_map[result]
                return False, f"❌ {data}"
    except asyncio.TimeoutError:
        return False, "⌛ Превышено время ожидания."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


# ==================================================
# --- БАЗА ДАННЫХ ---
# ==================================================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    username TEXT,
                    blocked INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    tx_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    task_type TEXT DEFAULT 'auto',
                    channel_id TEXT,
                    link TEXT,
                    description TEXT,
                    reward INTEGER DEFAULT 2500,
                    max_completions INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS completed (
                    user_id INTEGER,
                    task_id INTEGER,
                    PRIMARY KEY (user_id, task_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    task_id INTEGER,
                    proof TEXT,
                    proof_type TEXT,
                    status TEXT DEFAULT 'pending')''')
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_id INTEGER,
                    invited_id INTEGER UNIQUE,
                    paid INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS active_subs (
                    user_id INTEGER,
                    task_id INTEGER,
                    reward INTEGER,
                    PRIMARY KEY (user_id, task_id))''')
    conn.commit()

    migrations = [
        ("task_type", "TEXT DEFAULT 'auto'"),
        ("description", "TEXT DEFAULT ''"),
        ("max_completions", "INTEGER DEFAULT 0"),
        ("completed_count", "INTEGER DEFAULT 0"),
        ("channel_id", "TEXT"),
        ("link", "TEXT"),
        ("reward", "INTEGER DEFAULT 2500"),
    ]
    for col, definition in migrations:
        try:
            c.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        c.execute("ALTER TABLE withdrawals ADD COLUMN tx_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()


def get_balance(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    conn.close()
    return int(r[0]) if r and r[0] is not None else 0


def update_balance(user_id, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (int(amount), user_id))
    conn.commit()
    conn.close()


def is_blocked(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    conn.close()
    return bool(r and r[0])


def block_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_all_user_ids():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id != ? AND blocked = 0", (ADMIN_ID,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def save_active_sub(user_id, task_id, reward):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO active_subs (user_id, task_id, reward) VALUES (?, ?, ?)",
              (user_id, task_id, int(reward)))
    conn.commit()
    conn.close()


def remove_active_sub(user_id, task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("DELETE FROM active_subs WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    conn.commit()
    conn.close()


def get_all_active_subs():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT user_id, task_id, reward FROM active_subs")
    rows = c.fetchall()
    conn.close()
    return rows


def save_referral(inviter_id, invited_id):
    if inviter_id == invited_id:
        return False
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM referrals WHERE invited_id = ?", (invited_id,))
    if c.fetchone():
        conn.close()
        return False
    try:
        c.execute("INSERT INTO referrals (inviter_id, invited_id, paid) VALUES (?, ?, 0)",
                  (inviter_id, invited_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False
    conn.close()
    return True


def get_inviter(invited_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT inviter_id FROM referrals WHERE invited_id = ?", (invited_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None


def is_referral_paid(invited_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT paid FROM referrals WHERE invited_id = ?", (invited_id,))
    r = c.fetchone()
    conn.close()
    return bool(r and r[0])


def mark_referral_paid(invited_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE referrals SET paid = 1 WHERE invited_id = ?", (invited_id,))
    conn.commit()
    conn.close()


def get_referral_stats(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (user_id,))
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ? AND paid = 1", (user_id,))
    paid = c.fetchone()[0]
    conn.close()
    return {"total": total, "paid": paid, "pending": total - paid, "earned": paid * REFERRAL_REWARD}


def count_user_completed(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM completed WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else 0


def check_referral_qualification(user_id):
    inviter = get_inviter(user_id)
    if not inviter:
        return None
    if is_referral_paid(user_id):
        return None
    if get_next_task_for_user(user_id):
        return None
    if count_user_completed(user_id) == 0:
        return None
    update_balance(inviter, REFERRAL_REWARD)
    mark_referral_paid(user_id)
    return inviter


def add_task(name, task_type, channel_id, link, description, reward, max_completions):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("""INSERT INTO tasks
                 (name, task_type, channel_id, link, description, reward, max_completions, completed_count)
                 VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
              (name, task_type, channel_id, link, description, int(reward), int(max_completions)))
    conn.commit()
    conn.close()


def get_all_tasks():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("""SELECT id, name, task_type, channel_id, link, description, reward,
                        CAST(max_completions AS INTEGER), CAST(completed_count AS INTEGER)
                 FROM tasks""")
    tasks = c.fetchall()
    conn.close()
    return tasks


def get_next_task_for_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''
        SELECT id, name, task_type, channel_id, link, description, reward,
               CAST(max_completions AS INTEGER), CAST(completed_count AS INTEGER)
        FROM tasks
        WHERE id NOT IN (SELECT task_id FROM completed WHERE user_id = ?)
        AND (CAST(max_completions AS INTEGER) = 0
             OR CAST(completed_count AS INTEGER) < CAST(max_completions AS INTEGER))
        ORDER BY id ASC LIMIT 1
    ''', (user_id,))
    task = c.fetchone()
    conn.close()
    return task


def get_task(task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("""SELECT id, name, task_type, channel_id, link, description, reward,
                        CAST(max_completions AS INTEGER), CAST(completed_count AS INTEGER)
                 FROM tasks WHERE id = ?""", (task_id,))
    t = c.fetchone()
    conn.close()
    return t


def delete_task(task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    c.execute("DELETE FROM active_subs WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def mark_user_task_done(user_id, task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO completed (user_id, task_id) VALUES (?, ?)", (user_id, task_id))
    conn.commit()
    conn.close()


def unmark_user_task_done(user_id, task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("DELETE FROM completed WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    conn.commit()
    conn.close()


def is_task_done(user_id, task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM completed WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    r = c.fetchone()
    conn.close()
    return r is not None


def increment_task_counter(task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed_count = CAST(completed_count AS INTEGER) + 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def decrement_task_counter(task_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed_count = MAX(0, CAST(completed_count AS INTEGER) - 1) WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def mark_completed(user_id, task_id):
    mark_user_task_done(user_id, task_id)
    increment_task_counter(task_id)


def create_submission(user_id, task_id, proof, proof_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO submissions (user_id, task_id, proof, proof_type) VALUES (?, ?, ?, ?)",
              (user_id, task_id, proof, proof_type))
    conn.commit()
    sid = c.lastrowid
    conn.close()
    return sid


def get_submission(sid):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT id, user_id, task_id, proof, proof_type, status FROM submissions WHERE id = ?", (sid,))
    row = c.fetchone()
    conn.close()
    return row


def set_submission_status(sid, status):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE submissions SET status = ? WHERE id = ?", (status, sid))
    conn.commit()
    conn.close()


def get_pending_submissions():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT id, user_id, task_id, proof, proof_type FROM submissions WHERE status = 'pending' ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return rows


def create_withdrawal(user_id, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO withdrawals (user_id, amount) VALUES (?, ?)", (user_id, int(amount)))
    conn.commit()
    wid = c.lastrowid
    conn.close()
    return wid


def get_withdrawal(wid):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT id, user_id, amount, status, tx_id FROM withdrawals WHERE id = ?", (wid,))
    row = c.fetchone()
    conn.close()
    return row


def get_pending_withdrawals():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT id, user_id, amount FROM withdrawals WHERE status = 'pending' ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return rows


def set_withdrawal_status(wid, status, tx_id=None):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    if tx_id:
        c.execute("UPDATE withdrawals SET status = ?, tx_id = ? WHERE id = ?", (status, tx_id, wid))
    else:
        c.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, wid))
    conn.commit()
    conn.close()


async def is_subscribed(user_id, channel_id):
    channel_id = (channel_id or "").strip()
    if not channel_id:
        return False, "❌ Канал не указан."
    if not channel_id.startswith("@") and not channel_id.startswith("-"):
        channel_id = "@" + channel_id
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        s = str(member.status).lower()
        if s in ("creator", "administrator", "member"):
            return True, "ok"
        return False, f"status={s}"
    except Exception as e:
        err = str(e)
        if "chat not found" in err.lower():
            return False, "❌ Канал не найден."
        if "not enough rights" in err.lower() or "member list is inaccessible" in err.lower():
            return False, "❌ Бот не администратор канала."
        if "user not found" in err.lower():
            return False, "❌ Пользователь не найден."
        return False, f"❌ Ошибка: {err}"


async def check_subscriptions_loop():
    await asyncio.sleep(30)
    while True:
        try:
            subs = get_all_active_subs()
            if subs:
                logging.info(f"[SUBS-CHECK] Проверяем {len(subs)} подписок...")
            for user_id, task_id, reward in subs:
                try:
                    task = get_task(task_id)
                    if not task:
                        remove_active_sub(user_id, task_id)
                        continue
                    channel_id = task[3]
                    if not channel_id:
                        remove_active_sub(user_id, task_id)
                        continue
                    is_sub, _ = await is_subscribed(user_id, channel_id)
                    if not is_sub:
                        logging.info(f"[SUBS-CHECK] Отписка: user={user_id} task={task_id}")
                        update_balance(user_id, -int(reward))
                        unmark_user_task_done(user_id, task_id)
                        decrement_task_counter(task_id)
                        remove_active_sub(user_id, task_id)
                        try:
                            await bot.send_message(
                                user_id,
                                f"⚠️ <b>Вы отписались от канала!</b>\n\n"
                                f"📌 Задание: <b>{esc(task[1])}</b>\n"
                                f"💸 Списано: <b>-{reward} BC</b>\n\n"
                                f"Подпишитесь снова и выполните задание заново.",
                                parse_mode="HTML",
                                reply_markup=main_menu_kb(user_id == ADMIN_ID)
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logging.error(f"[SUBS-CHECK] Ошибка: {e}")
        except Exception as e:
            logging.error(f"[SUBS-CHECK] Общая ошибка: {e}", exc_info=True)
        await asyncio.sleep(CHECK_SUBS_INTERVAL)


async def broadcast_new_task(task_name: str, reward: int, task_type: str):
    user_ids = get_all_user_ids()
    if not user_ids:
        return 0
    ttype_ru = "🔹 Подписка" if task_type == "auto" else "🔸 Ручная проверка"
    text = (
        f"🆕 <b>Появилось новое задание!</b>\n\n"
        f"📌 <b>{esc(task_name)}</b>\n"
        f"🔖 Тип: {ttype_ru}\n"
        f"💰 Награда: <b>{reward} BC</b>\n\n"
        f"👉 Нажмите «💸 Заработок», чтобы начать!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Перейти к заданиям", callback_data="goto_earning")]
    ])
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    return sent


def main_menu_kb(is_admin=False):
    kb = [
        [KeyboardButton(text="💸 Заработок"), KeyboardButton(text="📺 Смотреть рекламу")],
        [KeyboardButton(text="💰 Общий баланс"), KeyboardButton(text="👥 Рефералы")],
        [KeyboardButton(text="📝 Отзывы")]
    ]
    if is_admin:
        kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить задание"), KeyboardButton(text="📋 Список заданий")],
            [KeyboardButton(text="📥 Заявки на задания"), KeyboardButton(text="💸 Заявки на вывод")],
            [KeyboardButton(text="💰 Баланс Bytecoin"), KeyboardButton(text="🧪 Тест Axionna")],
            [KeyboardButton(text="🧪 Проверить канал")],
            [KeyboardButton(text="◀️ В главное меню")]
        ],
        resize_keyboard=True
    )


def task_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔹 Авто (подписка)", callback_data="ttype_auto")],
        [InlineKeyboardButton(text="🔸 Ручное (проверка вручную)", callback_data="ttype_manual")]
    ])


def completions_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♾ Бесконечно", callback_data="comp_inf")],
        [InlineKeyboardButton(text="🔢 Ввести число", callback_data="comp_num")]
    ])


def balance_menu_kb(balance):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Вывод (Баланс: {balance} BC)", callback_data="withdraw")]
    ])


def task_menu_kb(link, task_type, task_id):
    rows = []
    if link:
        rows.append([InlineKeyboardButton(text="🔗 Перейти", url=link)])
    if task_type == "manual":
        rows.append([InlineKeyboardButton(text="📤 Отправить доказательство", callback_data=f"send_proof_{task_id}")])
    else:
        rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_task_{task_id}")])
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip_task_{task_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reviews_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Открыть отзывы", url=REVIEWS_LINK)]
    ])


def referral_kb(link):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={link}&text=Зарабатывай%20BC%20вместе%20со%20мной!")],
        [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data="copy_ref_link")]
    ])


def withdrawal_review_kb(wid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выплатить", callback_data=f"wpay_{wid}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"wrej_{wid}")
        ],
        [
            InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"wblock_{wid}")
        ]
    ])


def submission_review_kb(sid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Оплатить", callback_data=f"spay_{sid}"),
            InlineKeyboardButton(text="❌ Отказать в выплате", callback_data=f"srej_{sid}")
        ]
    ])


def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="◀️ Отмена")]], resize_keyboard=True)


class WithdrawState(StatesGroup):
    amount = State()


class AdminTaskState(StatesGroup):
    name = State()
    task_type = State()
    channel_id = State()
    link = State()
    description = State()
    reward = State()
    completions = State()


class AdminCheckState(StatesGroup):
    channel_id = State()


class ProofState(StatesGroup):
    waiting = State()


@dp.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    PENDING_TASK.pop(message.from_user.id, None)
    user_id = message.from_user.id
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
              (user_id, message.from_user.username or "Unknown"))
    conn.commit()
    conn.close()

    if is_blocked(user_id) and user_id != ADMIN_ID:
        await message.answer("🚫 <b>Вы заблокированы.</b>\n\nОбратитесь к администратору.",
                             parse_mode="HTML")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip().startswith("ref_"):
        try:
            inviter_id = int(parts[1].strip().replace("ref_", ""))
            if inviter_id != user_id and save_referral(inviter_id, user_id):
                try:
                    await bot.send_message(inviter_id,
                        f"🎉 <b>Новый друг по вашей ссылке!</b>\n\n"
                        f"💰 Получите <b>{REFERRAL_REWARD} BC</b>, когда он выполнит все задания.",
                        parse_mode="HTML")
                except Exception:
                    pass
        except ValueError:
            pass

    await message.answer(
        f"Привет, {message.from_user.first_name}!\n\n"
        "Это бот для заработка BC. Выполняй задания и получай награду!",
        reply_markup=main_menu_kb(user_id == ADMIN_ID)
    )


async def check_blocked(message: Message) -> bool:
    if message.from_user.id == ADMIN_ID:
        return False
    if is_blocked(message.from_user.id):
        await message.answer("🚫 <b>Вы заблокированы.</b>\n\nОбратитесь к администратору.",
                             parse_mode="HTML")
        return True
    return False


@dp.message(F.text == "💸 Заработок")
async def start_earning(message: Message, state: FSMContext):
    if await check_blocked(message):
        return
    await state.clear()
    PENDING_TASK.pop(message.from_user.id, None)
    await send_next_task_message(message.from_user.id, message.chat.id)


@dp.message(F.text == "📺 Смотреть рекламу")
async def watch_ad_handler(message: Message, state: FSMContext):
    if await check_blocked(message):
        return
    await state.clear()
    user_id = message.from_user.id
    await message.answer("⏳ Загружаем рекламу...")
    success, reason = await show_axionna_ad(chat_id=user_id, greeting=False)
    if success:
        update_balance(user_id, AD_VIEW_REWARD)
        balance = get_balance(user_id)
        await message.answer(
            f"✅ <b>Реклама показана!</b>\n\n"
            f"💰 Зачислено: <b>+{AD_VIEW_REWARD} BC</b>\n"
            f"💳 Баланс: <b>{balance} BC</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb(user_id == ADMIN_ID))
    else:
        await message.answer(f"{reason}", reply_markup=main_menu_kb(user_id == ADMIN_ID))


@dp.message(F.text == "💰 Общий баланс")
async def show_balance(message: Message, state: FSMContext):
    if await check_blocked(message):
        return
    await state.clear()
    balance = get_balance(message.from_user.id)
    await message.answer(f"Ваш баланс: *{balance} BC*",
                         parse_mode="Markdown", reply_markup=balance_menu_kb(balance))


@dp.message(F.text == "👥 Рефералы")
async def referral_handler(message: Message, state: FSMContext):
    if await check_blocked(message):
        return
    await state.clear()
    user_id = message.from_user.id
    stats = get_referral_stats(user_id)
    bot_username = await get_bot_username()
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = (
        f"👥 <b>Реферальная программа</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>За друга:</b> <code>{REFERRAL_REWARD} BC</code>\n"
        f"🎁 <b>Условие:</b> друг должен <b>выполнить все задания</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>Ваша ссылка:</b>\n<code>{esc(link)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Приглашено: <b>{stats['total']}</b>\n"
        f"• Выполнили: <b>{stats['paid']}</b>\n"
        f"• В процессе: <b>{stats['pending']}</b>\n"
        f"• Заработано: <b>{stats['earned']} BC</b>"
    )
    await message.answer(text, parse_mode="HTML",
                         reply_markup=referral_kb(link),
                         disable_web_page_preview=True)


@dp.message(F.text == "📝 Отзывы")
async def reviews_handler(message: Message, state: FSMContext):
    if await check_blocked(message):
        return
    await state.clear()
    await message.answer("📝 *Отзывы*\n\nНажмите кнопку ниже:",
                         parse_mode="Markdown", reply_markup=reviews_kb())


@dp.callback_query(F.data == "goto_earning")
async def goto_earning_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        await callback.message.answer("🚫 Вы заблокированы.")
        return
    await state.clear()
    user_id = callback.from_user.id
    await callback.message.answer("💸 Переходим...", reply_markup=main_menu_kb(user_id == ADMIN_ID))
    await send_next_task_message(user_id, callback.message.chat.id)


@dp.callback_query(F.data == "copy_ref_link")
async def copy_ref_link(callback: CallbackQuery):
    await callback.answer("Скопировано!")
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        return
    user_id = callback.from_user.id
    bot_username = await get_bot_username()
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    await callback.message.answer(f"📋 <code>{esc(link)}</code>", parse_mode="HTML",
                                   disable_web_page_preview=True)


@dp.callback_query(F.data == "withdraw")
async def withdraw_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        await callback.message.answer("🚫 Вы заблокированы.")
        return
    balance = get_balance(callback.from_user.id)
    if balance < 2500:
        await callback.message.answer("❌ Минимум 2500 BC!")
        return
    await callback.message.answer(f"💳 *Вывод*\n\nБаланс: *{balance} BC*\nВведите сумму:",
                                   parse_mode="Markdown")
    await state.set_state(WithdrawState.amount)


@dp.callback_query(F.data.startswith("check_task_"))
async def check_task_handler(callback: CallbackQuery):
    await callback.answer("Проверяем...")
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        return
    try:
        user_id = callback.from_user.id
        task_id = int(callback.data.split("_")[2])
        if is_task_done(user_id, task_id):
            await callback.message.answer("ℹ️ Уже выполнено.")
            return
        task = get_task(task_id)
        if not task:
            await callback.message.edit_text("❌ Задание удалено.")
            return
        _, name, ttype, channel_id, link, description, reward, max_comp, comp_count = task
        reward = int(reward or 0)
        ttype = str(ttype or "auto").replace("'", "").strip()

        subscribed, reason = await is_subscribed(user_id, channel_id)
        if subscribed:
            mark_completed(user_id, task_id)
            update_balance(user_id, reward)
            save_active_sub(user_id, task_id, reward)
            balance = get_balance(user_id)
            next_task = get_next_task_for_user(user_id)
            if next_task:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💸 Следующее", callback_data=f"next_task_{task_id}")]
                ])
                tail = "\n\nЕсть ещё задания! 💸"
            else:
                kb = None
                tail = "\n\n📭 *Заданий сейчас нет*"
            await callback.message.edit_text(
                f"✅ *Подписка подтверждена!*\n\n"
                f"💰 Зачислено: *{reward} BC*\n"
                f"💳 Баланс: *{balance} BC*{tail}\n\n"
                f"⚠️ _Не отписывайтесь — награда будет списана._",
                parse_mode="Markdown", reply_markup=kb)
            await try_pay_referral(user_id)
        else:
            await callback.message.edit_text(
                f"❌ *Не подписаны*\n\n`{reason}`\n\nПодпишитесь: {link}",
                parse_mode="Markdown", reply_markup=task_menu_kb(link, ttype, task_id),
                disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"ОШИБКА check_task: {e}", exc_info=True)


@dp.callback_query(F.data.startswith("send_proof_"))
async def send_proof_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        return
    user_id = callback.from_user.id
    task_id = int(callback.data.split("_")[2])
    if is_task_done(user_id, task_id):
        await callback.message.answer("ℹ️ Уже выполнено.")
        return
    await state.update_data(task_id=task_id)
    await callback.message.answer("📤 Отправьте скриншот или текст:",
                                   reply_markup=cancel_kb())
    await state.set_state(ProofState.waiting)


@dp.message(ProofState.waiting, F.text == "◀️ Отмена")
async def cancel_proof(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu_kb(message.from_user.id == ADMIN_ID))


@dp.message(ProofState.waiting, F.photo | F.document)
async def receive_proof_media(message: Message, state: FSMContext):
    proof = message.photo[-1].file_id if message.photo else message.document.file_id
    await process_proof(message, state, proof=proof, proof_type="photo" if message.photo else "document")


@dp.message(ProofState.waiting, F.text, ~F.text.in_(MENU_BUTTONS))
async def receive_proof_text(message: Message, state: FSMContext):
    await process_proof(message, state, proof=message.text or "", proof_type="text")


async def process_proof(message: Message, state: FSMContext, proof: str, proof_type: str):
    try:
        user_id = message.from_user.id
        if user_id != ADMIN_ID and is_blocked(user_id):
            await state.clear()
            return
        data = await state.get_data()
        task_id = data.get('task_id')
        if not task_id:
            task = get_next_task_for_user(user_id)
            if not task:
                await message.answer("❌ Задание не актуально.")
                await state.clear()
                return
            task_id = task[0]
        task = get_task(task_id)
        if not task:
            await message.answer("❌ Задание удалено.")
            await state.clear()
            return
        _, name, ttype, channel_id, link, description, reward, max_comp, comp_count = task
        reward = int(reward or 0)

        sid = create_submission(user_id, task_id, proof, proof_type)
        mark_user_task_done(user_id, task_id)

        await message.answer(
            f"✅ *Доказательство отправлено!*\n\nОжидайте проверки.\n🆔 Заявка №`{sid}`",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(user_id == ADMIN_ID))

        username = message.from_user.username or "—"
        header = (f"📥 <b>Заявка №{sid}</b>\n\n"
                  f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
                  f"📌 {esc(name)}\n💰 {reward} BC")
        try:
            if proof_type == "photo":
                await bot.send_photo(ADMIN_ID, proof, caption=header, parse_mode="HTML",
                                     reply_markup=submission_review_kb(sid))
            elif proof_type == "document":
                await bot.send_document(ADMIN_ID, proof, caption=header, parse_mode="HTML",
                                        reply_markup=submission_review_kb(sid))
            else:
                await bot.send_message(ADMIN_ID, header + f"\n\n💬 {esc(proof)}",
                                       parse_mode="HTML", reply_markup=submission_review_kb(sid))
        except Exception as e:
            logging.error(f"Не отправлено админу: {e}")
        await state.clear()
    except Exception as e:
        logging.error(f"ОШИБКА process_proof: {e}", exc_info=True)
        await state.clear()


@dp.callback_query(F.data.startswith("skip_task_"))
async def skip_task_handler(callback: CallbackQuery):
    await callback.answer("Пропущено")
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        return
    user_id = callback.from_user.id
    task_id = int(callback.data.split("_")[2])
    mark_user_task_done(user_id, task_id)
    await send_next_task_message(user_id, callback.message.chat.id)
    await try_pay_referral(user_id)


@dp.callback_query(F.data.startswith("next_task_"))
async def next_task_handler(callback: CallbackQuery):
    await callback.answer("Загружаем...")
    if callback.from_user.id != ADMIN_ID and is_blocked(callback.from_user.id):
        return
    user_id = callback.from_user.id
    try:
        mark_user_task_done(user_id, int(callback.data.split("_")[2]))
    except (ValueError, IndexError):
        pass
    await send_next_task_message(user_id, callback.message.chat.id)


async def send_next_task_message(user_id: int, chat_id: int):
    try:
        task = get_next_task_for_user(user_id)
        if not task:
            await bot.send_message(chat_id,
                "📭 *Заданий сейчас нет*\n\nСмотрите рекламу 📺 или заходите позже!",
                parse_mode="Markdown")
            return
        task_id, name, ttype, channel_id, link, description, reward, max_comp, comp_count = task
        reward = int(reward or 0)
        max_comp = int(max_comp or 0)
        comp_count = int(comp_count or 0)
        ttype = str(ttype or "auto").replace("'", "").strip()

        if ttype == "manual":
            text = f"📋 *Задание (ручное):* {name}\n\n"
            if description:
                text += f"📌 {description}\n\n"
            text += (f"1️⃣ Выполните задание\n2️⃣ Нажмите «📤 Отправить доказательство»\n\n"
                     f"💰 Награда: *{reward} BC*")
        else:
            text = (f"📋 *Задание:* {name}\n\n"
                    f"1️⃣ Подпишитесь на канал\n2️⃣ Нажмите «✅ Проверить»\n\n"
                    f"💰 Награда: *{reward} BC*\n"
                    f"⚠️ _Не отписывайтесь — награда будет списана!_")
        if max_comp > 0:
            text += f"\n\n⚡ Осталось: *{max_comp - comp_count}*"
        await bot.send_message(chat_id, text, parse_mode="Markdown",
                               reply_markup=task_menu_kb(link, ttype, task_id))
    except Exception as e:
        logging.error(f"ОШИБКА send_next_task: {e}", exc_info=True)


async def try_pay_referral(user_id: int):
    inviter = check_referral_qualification(user_id)
    if inviter:
        try:
            await bot.send_message(inviter,
                f"🎉 <b>Ваш друг выполнил все задания!</b>\n\n"
                f"💰 Зачислено: <b>{REFERRAL_REWARD} BC</b>",
                parse_mode="HTML")
        except Exception:
            pass


@dp.message(WithdrawState.amount)
async def process_withdraw(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID and is_blocked(message.from_user.id):
        await state.clear()
        return
    try:
        amount = int(message.text)
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    user_id = message.from_user.id
    balance = get_balance(user_id)
    if amount < 2500 or amount > balance:
        await message.answer("❌ Неверная сумма или недостаточно средств.")
        return
    update_balance(user_id, -amount)
    wid = create_withdrawal(user_id, amount)
    await message.answer(
        f"✅ *Заявка №{wid} создана!*\n\n💰 {amount} BC\nОжидайте решения.",
        parse_mode="Markdown", reply_markup=main_menu_kb(user_id == ADMIN_ID))

    username = message.from_user.username or "—"
    header = (f"🔔 <b>НОВАЯ ЗАЯВКА №{wid}</b>\n\n"
              f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
              f"💰 <b>{amount} BC</b>\n\n👇 Выберите:")
    try:
        await bot.send_message(ADMIN_ID, header, parse_mode="HTML",
                               reply_markup=withdrawal_review_kb(wid))
    except Exception as e:
        logging.error(f"Не уведомить админа: {e}")
    await state.clear()


@dp.callback_query(F.data.startswith("wpay_"))
async def withdrawal_pay(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split("_")[1])
    w = get_withdrawal(wid)
    if not w or w[3] != 'pending':
        await callback.answer("Уже обработано", show_alert=True)
        return

    user_id, amount = w[1], w[2]
    await callback.answer("⏳ Отправляю перевод...")

    original_text = callback.message.text or callback.message.caption or ""

    try:
        await callback.message.edit_text(
            original_text + "\n\n⏳ <i>Отправляю перевод в Bytecoin...</i>",
            parse_mode="HTML")
    except Exception as e:
        logging.error(f"edit_text error: {e}")

    status, data = await bc_transfer(user_id, amount)

    if status == 200 and data.get("status") == "ok":
        tx_id = data.get("transaction_id", "—")
        set_withdrawal_status(wid, 'paid', tx_id)

        conn = sqlite3.connect('bot_database.db')
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        username = row[0] if row else "—"

        paid_text = (
            f"🆔 <b>Заявка №{wid}</b>\n\n"
            f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
            f"💰 Сумма: <b>{amount} BC</b>\n\n"
            f"✅ <b>ВЫВЕДЕНО: {amount} BC</b>\n"
            f"🆔 TX: <code>{esc(tx_id)}</code>"
        )
        try:
            await callback.message.edit_text(paid_text, parse_mode="HTML", reply_markup=None)
        except Exception as e:
            logging.error(f"edit_text (paid) error: {e}")

        try:
            await bot.send_message(user_id,
                f"✅ <b>Выплата одобрена!</b>\n\n"
                f"💰 Сумма: <b>{amount} BC</b>\n"
                f"🆔 TX: <code>{esc(tx_id)}</code>\n\n"
                f"Средства отправлены на ваш Bytecoin кошелёк в Telegram.",
                parse_mode="HTML")
        except Exception as e:
            logging.error(f"Уведомить {user_id}: {e}")

        await callback.answer(f"✅ Выведено {amount} BC")
    else:
        error = ""
        if isinstance(data, dict):
            error = data.get("error") or data.get("code") or "Неизвестная ошибка"
        logging.error(f"[BYTECOIN] Ошибка выплаты №{wid}: {status} {error}")

        try:
            await callback.message.edit_text(
                original_text + f"\n\n❌ <b>ОШИБКА ВЫПЛАТЫ</b>\n\n"
                f"HTTP: <code>{status}</code>\n"
                f"Ошибка: <code>{esc(str(error))[:200]}</code>\n\n"
                f"Заявка осталась в <b>pending</b> — попробуйте снова.",
                parse_mode="HTML",
                reply_markup=withdrawal_review_kb(wid))
        except Exception as e:
            logging.error(f"edit_text (error) error: {e}")

        await callback.answer(f"❌ Ошибка: {str(error)[:100]}", show_alert=True)


@dp.callback_query(F.data.startswith("wrej_"))
async def withdrawal_reject(callback: CallbackQuery):
    await callback.answer("Отказано")
    if callback.from_user.id != ADMIN_ID:
        return
    wid = int(callback.data.split("_")[1])
    w = get_withdrawal(wid)
    if not w or w[3] != 'pending':
        return
    user_id, amount = w[1], w[2]
    set_withdrawal_status(wid, 'rejected')

    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    username = row[0] if row else "—"

    rejected_text = (
        f"🆔 <b>Заявка №{wid}</b>\n\n"
        f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
        f"💰 Сумма: <b>{amount} BC</b>\n\n"
        f"❌ <b>ОТКАЗАНО</b> — средства не возвращены."
    )
    try:
        await callback.message.edit_text(rejected_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        pass

    try:
        await bot.send_message(user_id,
            f"❌ <b>Заявка №{wid} отклонена</b>\n\n"
            f"💰 Сумма: <b>{amount} BC</b>\n\n"
            f"Средства не возвращены. Свяжитесь с поддержкой: {SUPPORT_USERNAME}",
            parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("wblock_"))
async def withdrawal_block(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split("_")[1])
    w = get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    user_id, amount = w[1], w[2]
    block_user(user_id)
    if w[3] == 'pending':
        set_withdrawal_status(wid, 'rejected')

    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    username = row[0] if row else "—"

    block_text = (
        f"🆔 <b>Заявка №{wid}</b>\n\n"
        f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
        f"💰 Сумма: <b>{amount} BC</b>\n\n"
        f"🚫 <b>ПОЛЬЗОВАТЕЛЬ ЗАБЛОКИРОВАН</b>"
    )
    try:
        await callback.message.edit_text(block_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        pass

    try:
        await bot.send_message(user_id,
            f"🚫 <b>Вы заблокированы!</b>\n\n"
            f"Обратитесь к администратору: {SUPPORT_USERNAME}",
            parse_mode="HTML")
    except Exception:
        pass

    await callback.answer(f"🚫 Пользователь {user_id} заблокирован", show_alert=True)


@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    PENDING_TASK.pop(message.from_user.id, None)
    await message.answer("⚙️ *Админ-панель*", parse_mode="Markdown", reply_markup=admin_menu_kb())


@dp.message(F.text == "◀️ В главное меню")
async def back_main(message: Message, state: FSMContext):
    await state.clear()
    PENDING_TASK.pop(message.from_user.id, None)
    await message.answer("Меню", reply_markup=main_menu_kb(message.from_user.id == ADMIN_ID))


@dp.message(F.text == "💰 Баланс Bytecoin")
async def admin_bc_balance(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    msg = await message.answer("⏳ Запрашиваю баланс сервиса...")

    try:
        status, data = await bc_get_service_info()
    except Exception as e:
        logging.error(f"[BC-BALANCE] {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ <b>Ошибка</b>\n\n<code>{esc(e)}</code>",
                                parse_mode="HTML", reply_markup=admin_menu_kb())
        except Exception:
            pass
        return

    if status == 200 and isinstance(data, dict) and data.get("status") == "ok":
        info = data.get("data", {})
        balance = info.get("balance", "—")
        in_hold = info.get("in_hold", "—")
        name = info.get("name", "—")
        link = info.get("link", "—")
        maintenance = info.get("maintenance", False)

        text = (
            f"💰 <b>Bytecoin сервис</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📛 Название: <b>{esc(name)}</b>\n"
            f"🔗 Ссылка: {esc(link)}\n\n"
            f"💵 Баланс: <b>{esc(balance)} BC</b>\n"
            f"🔒 В холде: <b>{esc(in_hold)} BC</b>\n\n"
            f"⚙️ Техрежим: {'🔴 Включён' if maintenance else '🟢 Выключен'}"
        )
    else:
        err_text = ""
        if isinstance(data, dict):
            err_text = data.get("error") or data.get("code") or data.get("message") or ""
        if status == -1:
            text = "❌ <b>API-ключ Bytecoin не установлен</b>"
        elif status == -2:
            text = "❌ <b>Timeout</b>"
        elif status == -3:
            text = "❌ <b>Нет соединения</b>"
        else:
            text = f"❌ <b>Ошибка</b>\n\nHTTP: <code>{status}</code>\n<code>{esc(str(err_text))[:300]}</code>"

    try:
        await msg.edit_text(text, parse_mode="HTML", reply_markup=admin_menu_kb())
    except Exception:
        try:
            await message.answer(text, parse_mode="HTML", reply_markup=admin_menu_kb())
        except Exception:
            pass


@dp.message(F.text == "🧪 Тест Axionna")
async def admin_test_axionna(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer(f"🧪 Тестирую Axionna...\nchat_id: <code>{ADMIN_ID}</code>", parse_mode="HTML")
    success, reason = await show_axionna_ad(chat_id=ADMIN_ID, greeting=True)
    await message.answer(f"✅ Успех: <b>{success}</b>\n💬 {reason}", parse_mode="HTML",
                         reply_markup=admin_menu_kb())


@dp.message(F.text == "📥 Заявки на задания")
async def admin_submissions(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    pending = get_pending_submissions()
    if not pending:
        await message.answer("📭 Нет заявок.", reply_markup=admin_menu_kb())
        return
    rows = []
    for sid, user_id, task_id, proof, proof_type in pending:
        conn = sqlite3.connect('bot_database.db')
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        c.execute("SELECT name FROM tasks WHERE id = ?", (task_id,))
        t = c.fetchone()
        conn.close()
        username = row[0] if row else "—"
        task_name = (t[0] if t else "?")[:25]
        rows.append([InlineKeyboardButton(
            text=f"📋 №{sid} • @{username} • {task_name}",
            callback_data=f"subview_{sid}")])
    await message.answer(f"📥 Заявки: <b>{len(pending)}</b>",
                         parse_mode="HTML",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("subview_"))
async def submission_view(callback: CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        sid = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        return
    s = get_submission(sid)
    if not s or s[5] != 'pending':
        return
    user_id, task_id, proof, proof_type = s[1], s[2], s[3], s[4]
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    c.execute("SELECT name, reward FROM tasks WHERE id = ?", (task_id,))
    t = c.fetchone()
    conn.close()
    username = row[0] if row else "—"
    task_name = t[0] if t else "?"
    reward = int(t[1]) if t and t[1] is not None else 0

    header = (f"🆔 <b>Заявка №{sid}</b>\n\n"
              f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
              f"📌 {esc(task_name)}\n💰 {reward} BC")
    kb = submission_review_kb(sid)
    if proof_type == "photo":
        try:
            await callback.message.answer_photo(proof, caption=header, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    if proof_type == "document":
        try:
            await callback.message.answer_document(proof, caption=header, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await callback.message.answer(header + f"\n\n💬 {esc(proof)}", parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("spay_"))
async def submission_accept(callback: CallbackQuery):
    await callback.answer("Оплачено")
    if callback.from_user.id != ADMIN_ID:
        return
    sid = int(callback.data.split("_")[1])
    s = get_submission(sid)
    if not s or s[5] != 'pending':
        return
    user_id, task_id = s[1], s[2]
    task = get_task(task_id)
    if not task:
        return
    reward = int(task[6] or 0)
    max_comp = int(task[7] or 0)
    comp_count = int(task[8] or 0)
    if max_comp > 0 and comp_count >= max_comp:
        set_submission_status(sid, 'rejected')
        return
    increment_task_counter(task_id)
    update_balance(user_id, reward)
    set_submission_status(sid, 'accepted')
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ <b>ОПЛАЧЕНО</b>",
                                                 parse_mode="HTML", reply_markup=None)
        else:
            await callback.message.edit_text((callback.message.text or "") + "\n\n✅ <b>ОПЛАЧЕНО</b>",
                                              parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    try:
        await bot.send_message(user_id,
            f"✅ <b>Задание оплачено!</b>\n\n💰 +{reward} BC",
            parse_mode="HTML")
    except Exception:
        pass
    await try_pay_referral(user_id)


@dp.callback_query(F.data.startswith("srej_"))
async def submission_reject(callback: CallbackQuery):
    await callback.answer("Отказано")
    if callback.from_user.id != ADMIN_ID:
        return
    sid = int(callback.data.split("_")[1])
    s = get_submission(sid)
    if not s or s[5] != 'pending':
        return
    user_id = s[1]
    set_submission_status(sid, 'rejected')
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ <b>ОТКАЗАНО</b>",
                                                 parse_mode="HTML", reply_markup=None)
        else:
            await callback.message.edit_text((callback.message.text or "") + "\n\n❌ <b>ОТКАЗАНО</b>",
                                              parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    try:
        await bot.send_message(user_id, f"❌ Заявка №{sid} отклонена.", parse_mode="HTML")
    except Exception:
        pass


@dp.message(F.text == "💸 Заявки на вывод")
async def admin_withdrawals(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    pending = get_pending_withdrawals()
    if not pending:
        await message.answer("📭 Нет заявок.", reply_markup=admin_menu_kb())
        return
    rows = []
    for wid, user_id, amount in pending:
        conn = sqlite3.connect('bot_database.db')
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        username = row[0] if row else "—"
        rows.append([InlineKeyboardButton(
            text=f"💸 №{wid} • @{username} • {amount} BC",
            callback_data=f"wview_{wid}")])
    await message.answer(f"💸 Заявки: <b>{len(pending)}</b>",
                         parse_mode="HTML",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("wview_"))
async def withdrawal_view(callback: CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        wid = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        return
    w = get_withdrawal(wid)
    if not w or w[3] != 'pending':
        return
    user_id, amount = w[1], w[2]
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    username = row[0] if row else "—"
    await callback.message.answer(
        f"🆔 <b>Заявка №{wid}</b>\n\n"
        f"👤 @{esc(username)} (<code>{user_id}</code>)\n"
        f"💰 <b>{amount} BC</b>\n\n👇 Выберите:",
        parse_mode="HTML",
        reply_markup=withdrawal_review_kb(wid))


@dp.message(F.text == "🧪 Проверить канал")
async def admin_check_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("Введите ID канала:")
    await state.set_state(AdminCheckState.channel_id)


@dp.message(AdminCheckState.channel_id)
async def admin_check_run(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith("@") and not channel_id.startswith("-"):
        channel_id = "@" + channel_id
    try:
        chat = await bot.get_chat(channel_id)
        info = f"✅ <b>{esc(chat.title)}</b>\nID: <code>{chat.id}</code>"
    except Exception as e:
        await message.answer(f"❌ <code>{esc(e)}</code>", parse_mode="HTML")
        await state.clear()
        return
    sub, reason = await is_subscribed(ADMIN_ID, channel_id)
    result = "✅ Подписаны" if sub else f"⚠️ <code>{esc(reason)}</code>"
    await message.answer(f"{info}\n\n{result}", parse_mode="HTML", reply_markup=admin_menu_kb())
    await state.clear()


@dp.message(F.text == "➕ Добавить задание")
async def add_task_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    PENDING_TASK[message.from_user.id] = {}
    await message.answer("Введите *название*:", parse_mode="Markdown")
    await state.set_state(AdminTaskState.name)


@dp.message(AdminTaskState.name)
async def add_task_name(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    PENDING_TASK.setdefault(uid, {})['name'] = message.text
    await state.set_state(AdminTaskState.task_type)
    await message.answer("Тип задания:", reply_markup=task_type_kb())


@dp.callback_query(F.data.startswith("ttype_"))
async def add_task_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    uid = callback.from_user.id
    ttype = callback.data.split("_")[1]
    PENDING_TASK.setdefault(uid, {})['task_type'] = ttype
    try:
        if ttype == "auto":
            await callback.message.edit_text("🔹 *Авто*\n\nВведите ID канала:")
        else:
            await callback.message.edit_text("🔸 *Ручное*\n\nВведите ID канала или `-`:")
    except Exception:
        pass
    await state.set_state(AdminTaskState.channel_id)


@dp.message(AdminTaskState.channel_id)
async def add_task_channel(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    data = PENDING_TASK.get(uid, {})
    ttype = data.get('task_type', 'auto')
    text = message.text.strip()
    if ttype == "manual" and text == "-":
        PENDING_TASK[uid]['channel_id'] = ""
        await state.set_state(AdminTaskState.link)
        await message.answer("Ссылка (или `-`):")
        return
    channel_id = text if text.startswith("@") or text.startswith("-") else "@" + text
    if ttype == "auto":
        try:
            chat = await bot.get_chat(channel_id)
            PENDING_TASK[uid]['channel_id'] = channel_id
            await state.set_state(AdminTaskState.link)
            await message.answer(f"✅ {esc(chat.title)}\n\nСсылка:")
        except Exception as e:
            await message.answer(f"❌ <code>{esc(e)}</code>", parse_mode="HTML")
            await state.clear()
            PENDING_TASK.pop(uid, None)
    else:
        PENDING_TASK[uid]['channel_id'] = channel_id
        await state.set_state(AdminTaskState.link)
        await message.answer("Ссылка (или `-`):")


@dp.message(AdminTaskState.link)
async def add_task_link(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    link = message.text.strip()
    PENDING_TASK[uid]['link'] = "" if link == "-" else link
    if PENDING_TASK.get(uid, {}).get('task_type') == "manual":
        await state.set_state(AdminTaskState.description)
        await message.answer("Описание задания:")
    else:
        PENDING_TASK[uid]['description'] = ""
        await state.set_state(AdminTaskState.reward)
        await message.answer("Награда в BC:")


@dp.message(AdminTaskState.description)
async def add_task_description(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    PENDING_TASK[uid]['description'] = message.text
    await state.set_state(AdminTaskState.reward)
    await message.answer("Награда в BC:")


@dp.message(AdminTaskState.reward)
async def add_task_reward(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    try:
        PENDING_TASK[uid]['reward'] = int(message.text)
    except ValueError:
        PENDING_TASK[uid]['reward'] = 2500
    await state.set_state(AdminTaskState.completions)
    await message.answer("Лимит выполнений:", reply_markup=completions_kb())


@dp.callback_query(F.data == "comp_inf")
async def add_task_comp_inf(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    uid = callback.from_user.id
    if not PENDING_TASK.get(uid, {}).get('name'):
        await callback.message.answer("⚠️ Сессия истекла.")
        await state.clear()
        return
    PENDING_TASK[uid]['max_completions'] = 0
    await finalize_task(uid, state)
    PENDING_TASK.pop(uid, None)


@dp.callback_query(F.data == "comp_num")
async def add_task_comp_num(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    uid = callback.from_user.id
    if not PENDING_TASK.get(uid, {}).get('name'):
        await callback.message.answer("⚠️ Сессия истекла.")
        await state.clear()
        return
    await callback.message.edit_text("Введите число:")


@dp.message(AdminTaskState.completions)
async def add_task_comp_value(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.text in MENU_BUTTONS:
        await state.clear()
        PENDING_TASK.pop(uid, None)
        await message.answer("Отменено.", reply_markup=admin_menu_kb())
        return
    try:
        mc = int(message.text)
        if mc < 1:
            mc = 0
    except ValueError:
        mc = 0
    PENDING_TASK.setdefault(uid, {})['max_completions'] = mc
    await finalize_task(uid, state)
    PENDING_TASK.pop(uid, None)


async def finalize_task(user_id: int, state: FSMContext):
    data = PENDING_TASK.get(user_id, {})
    if not data.get('name'):
        await bot.send_message(user_id, "⚠️ Данные потеряны.")
        await state.clear()
        return
    try:
        add_task(data.get('name', ''), data.get('task_type', 'auto'),
                 data.get('channel_id', ''), data.get('link', ''),
                 data.get('description', ''), data.get('reward', 2500),
                 data.get('max_completions', 0))
    except Exception as e:
        await bot.send_message(user_id, f"❌ <code>{esc(e)}</code>", parse_mode="HTML")
        await state.clear()
        return
    ttype_ru = "🔹 Авто" if data.get('task_type') == 'auto' else "🔸 Ручное"
    await bot.send_message(user_id,
        f"✅ <b>Задание добавлено!</b>\n\n"
        f"📌 {esc(data.get('name'))}\n🔖 {ttype_ru}\n"
        f"💰 {data.get('reward', 2500)} BC\n\n📢 Рассылаю...",
        parse_mode="HTML", reply_markup=admin_menu_kb())
    try:
        sent = await broadcast_new_task(data.get('name', ''), data.get('reward', 2500),
                                        data.get('task_type', 'auto'))
        await bot.send_message(user_id, f"✅ Доставлено: <b>{sent}</b>", parse_mode="HTML")
    except Exception as e:
        await bot.send_message(user_id, f"⚠️ <code>{esc(e)}</code>", parse_mode="HTML")
    await state.clear()


@dp.message(F.text == "📋 Список заданий")
async def list_tasks(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    PENDING_TASK.pop(message.from_user.id, None)
    tasks = get_all_tasks()
    if not tasks:
        await message.answer("Список пуст.", reply_markup=admin_menu_kb())
        return
    for task_id, name, ttype, channel_id, link, description, reward, max_comp, comp_count in tasks:
        reward = int(reward or 0)
        max_comp = int(max_comp or 0)
        comp_count = int(comp_count or 0)
        ttype_ru = "🔹 Авто" if ttype == 'auto' else "🔸 Ручное"
        comp_text = f"♾ ({comp_count})" if max_comp == 0 else f"🔢 {comp_count}/{max_comp}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{task_id}")]])
        await message.answer(
            f"📌 <b>{esc(name)}</b> ({ttype_ru})\n"
            f"📢 <code>{esc(channel_id or '—')}</code>\n"
            f"💰 {reward} BC\n⚡ {comp_text}",
            parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("del_"))
async def delete_task_handler(callback: CallbackQuery):
    await callback.answer("Удалено")
    if callback.from_user.id != ADMIN_ID:
        return
    task_id = int(callback.data.split("_")[1])
    delete_task(task_id)
    try:
        await callback.message.edit_text("🗑 Удалено.", reply_markup=None)
    except Exception:
        pass


async def main():
    init_db()
    try:
        global BOT_USERNAME_CACHE
        me = await bot.get_me()
        BOT_USERNAME_CACHE = me.username
        logging.info(f"✅ Бот @{me.username} запущен!")
    except Exception:
        logging.info("✅ Бот запущен!")

    asyncio.create_task(check_subscriptions_loop())
    logging.info(f"✅ Проверка отписок запущена (каждые {CHECK_SUBS_INTERVAL} сек)")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())