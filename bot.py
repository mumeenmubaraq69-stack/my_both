# === Telegram Admin Panel Bot (unified single-file) ===
# Works with python-telegram-bot==20.3
# Paste into Pydroid 3, set your TOKEN below, run, then in Telegram:
# /claimadmin 1234   (or your changed PIN)  --> opens Admin Panel
#
# Features:
# - Admin Panel: Add/Remove Balance, Set Currency, Set Min/Max Withdraw, Set Channels/View Channels,
#                Ban/Unban User, Broadcast, Toggle Withdraw ON/OFF
# - Users: /start (with referral), Daily Bonus (/bonus button), Check/Join Channels, Withdraw request
# - SQLite database; safe to run on Android
#
# Notes:
# - For Join-Check to work, add your bot as ADMIN to each channel you set.
# - Referral bonus triggers once when a referred user passes the join-check for the first time.
# - All settings are editable from the Admin Panel.

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

# ========= CONFIG =========
TOKEN = "8073458119:AAHOAjdwxh_Bv8wJLRZaf6l89-nZVrUTncY"  # <-- REPLACE with your BotFather token (keep quotes)
OWNER_CLAIM_PIN = "1234"             # <-- Change this to your secret PIN for /claimadmin
DB_PATH = "bot.db"

# Defaults (change inside Admin Panel anytime)
DEFAULT_SETTINGS = {
    "currency": "NGN",
    "min_withdraw": "1000",
    "max_withdraw": "500000",
    "withdraw_open": "1",             # 1 = ON, 0 = OFF
    "daily_bonus_amount": "50",
    "referral_bonus_amount": "100",
    "channels": "[]",                 # JSON list of channel usernames, e.g. ["@mych1","@mych2"]
    "admin_id": ""                    # set after /claimadmin
}

# ========= DATABASE =========
def db_exec(query: str, params: tuple = ()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur

def init_db():
    db_exec("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            ref_by INTEGER,
            created_at TEXT,
            last_bonus_at TEXT,
            passed_join_check INTEGER DEFAULT 0,
            ref_credit_given INTEGER DEFAULT 0
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS withdraw_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            wallet TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    # load defaults
    for k, v in DEFAULT_SETTINGS.items():
        if get_setting(k) is None:
            set_setting(k, v)

def get_setting(key: str) -> Optional[str]:
    cur = db_exec("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None

def set_setting(key: str, value: str):
    db_exec("REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

def add_user_if_not_exists(user_id: int, ref_by: Optional[int] = None):
    cur = db_exec("SELECT id FROM users WHERE id=?", (user_id,))
    if not cur.fetchone():
        db_exec(
            "INSERT INTO users(id, balance, is_banned, ref_by, created_at, last_bonus_at, passed_join_check, ref_credit_given) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, 0.0, 0, ref_by, datetime.utcnow().isoformat(), None, 0, 0)
        )

def get_balance(user_id: int) -> float:
    cur = db_exec("SELECT balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0

def set_balance(user_id: int, amount: float):
    db_exec("UPDATE users SET balance=? WHERE id=?", (amount, user_id))

def change_balance(user_id: int, delta: float):
    bal = get_balance(user_id)
    set_balance(user_id, bal + delta)

def set_ban(user_id: int, banned: bool):
    db_exec("UPDATE users SET is_banned=? WHERE id=?", (1 if banned else 0, user_id))

def is_banned(user_id: int) -> bool:
    cur = db_exec("SELECT is_banned FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return bool(row[0]) if row else False

def all_user_ids() -> List[int]:
    cur = db_exec("SELECT id FROM users")
    return [r[0] for r in cur.fetchall()]

def set_passed_join_check(user_id: int):
    db_exec("UPDATE users SET passed_join_check=1 WHERE id=?", (user_id,))

def has_passed_join_check(user_id: int) -> bool:
    cur = db_exec("SELECT passed_join_check FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return bool(row[0]) if row else False

def get_ref_by(user_id: int) -> Optional[int]:
    cur = db_exec("SELECT ref_by FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None

def set_ref_by(user_id: int, ref_by: Optional[int]):
    db_exec("UPDATE users SET ref_by=? WHERE id=?", (ref_by, user_id))

def set_ref_credit_given(user_id: int):
    db_exec("UPDATE users SET ref_credit_given=1 WHERE id=?", (user_id,))

def ref_credit_given(user_id: int) -> bool:
    cur = db_exec("SELECT ref_credit_given FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return bool(row[0]) if row else False

def set_last_bonus(user_id: int, dt: datetime):
    db_exec("UPDATE users SET last_bonus_at=? WHERE id=?", (dt.isoformat(), user_id))

def get_last_bonus(user_id: int) -> Optional[datetime]:
    cur = db_exec("SELECT last_bonus_at FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None

# ========= UTILS =========
def admin_id() -> Optional[int]:
    a = get_setting("admin_id")
    return int(a) if a else None

def is_admin(uid: int) -> bool:
    a = admin_id()
    return a == uid if a else False

def parse_channels() -> List[str]:
    raw = get_setting("channels") or "[]"
    try:
        lst = json.loads(raw)
        return [c.strip() for c in lst if c.strip()]
    except Exception:
        return []

def fmt_amount(x: float) -> str:
    curr = get_setting("currency") or "NGN"
    return f"{curr} {x:,.2f}"

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Daily Bonus", callback_data="user:bonus"),
         InlineKeyboardButton("👥 Referral Link", callback_data="user:reflink")],
        [InlineKeyboardButton("📢 Join Channels", callback_data="user:channels"),
         InlineKeyboardButton("💸 Withdraw", callback_data="user:withdraw")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="user:help")]
    ])

def admin_panel_kb() -> InlineKeyboardMarkup:
    wd = "ON" if (get_setting("withdraw_open") == "1") else "OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin:add_balance"),
         InlineKeyboardButton("➖ Remove Balance", callback_data="admin:remove_balance")],
        [InlineKeyboardButton("💱 Set Currency", callback_data="admin:set_currency"),
         InlineKeyboardButton("💬 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton("⬇️ Min Withdraw", callback_data="admin:set_min"),
         InlineKeyboardButton("⬆️ Max Withdraw", callback_data="admin:set_max")],
        [InlineKeyboardButton("📢 Set Channels", callback_data="admin:set_channels"),
         InlineKeyboardButton("👁 View Channels", callback_data="admin:view_channels")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin:ban"),
         InlineKeyboardButton("✅ Unban User", callback_data="admin:unban")],
        [InlineKeyboardButton(f"💸 Withdraw: {wd} (toggle)", callback_data="admin:toggle_wd")],
        [InlineKeyboardButton("⬅️ Close", callback_data="admin:close")]
    ])

async def send_user_home(update: Update, context: ContextTypes.DEFAULT_TYPE, text: Optional[str] = None):
    user = update.effective_user
    bal = get_balance(user.id)
    msg = text or f"Welcome, *{user.first_name}*!\nYour balance: *{fmt_amount(bal)}*"
    if update.message:
        await update.message.reply_text(msg, reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)

# ========= JOIN CHECK =========
async def check_user_joined_all(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    channels = parse_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            status = member.status  # 'creator','administrator','member','restricted','left','kicked'
            if status in ("left", "kicked"):
                return False
        except Exception:
            # Bot not admin or channel invalid; treat as not joined
            return False
    return True

def channels_text() -> str:
    channels = parse_channels()
    if not channels:
        return "No channels set yet."
    lines = [f"• {c}" for c in channels]
    return "Please join all required channels, then press *I've joined*.\n\n" + "\n".join(lines)

def channels_kb() -> InlineKeyboardMarkup:
    channels = parse_channels()
    rows = [[InlineKeyboardButton(c, url=f"https://t.me/{c.lstrip('@')}")] for c in channels]
    rows.append([InlineKeyboardButton("✅ I've joined", callback_data="user:joinedcheck")])
    return InlineKeyboardMarkup(rows)

# ========= COMMANDS =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # parse referral (payload after /start)
    ref_by = None
    if context.args:
        try:
            ref_by = int(context.args[0])
            if ref_by == user.id:
                ref_by = None
        except Exception:
            ref_by = None

    add_user_if_not_exists(user.id, ref_by)
    if is_banned(user.id):
        await update.message.reply_text("You are banned from using this bot.")
        return

    await send_user_home(update, context)

    # If this is a new user, try ref credit after join check
    if ref_by:
        # nothing to do now; bonus happens when they pass join check
        pass

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("You are not an admin.")
        return
    await update.message.reply_text("🛠 *Admin Panel*", reply_markup=admin_panel_kb(), parse_mode=ParseMode.MARKDOWN)

async def cmd_claimadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if admin_id():
        await update.message.reply_text("Admin already set.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /claimadmin <PIN>")
        return
    pin = context.args[0]
    if pin == OWNER_CLAIM_PIN:
        set_setting("admin_id", str(update.effective_user.id))
        await update.message.reply_text("✅ You are now the admin. Use /admin to open the panel.")
    else:
        await update.message.reply_text("❌ Wrong PIN.")

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN)

# ========= USER CALLBACKS =========
async def on_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if is_banned(uid):
        await q.answer("You are banned.", show_alert=True)
        return

    data = q.data
    if data == "user:bonus":
        amt = float(get_setting("daily_bonus_amount") or "50")
        last = get_last_bonus(uid)
        now = datetime.utcnow()
        if last and now - last < timedelta(days=1):
            next_time = last + timedelta(days=1)
            wait_h = int((next_time - now).total_seconds() // 3600) + 1
            await q.answer("Come back later for your next daily bonus.", show_alert=True)
        else:
            change_balance(uid, amt)
            set_last_bonus(uid, now)
            await q.answer(f"🎁 Daily bonus added: {fmt_amount(amt)}", show_alert=True)
        await send_user_home(update, context)

    elif data == "user:reflink":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={uid}"
        txt = ("👥 *Your Referral Link*\n"
               f"{link}\n\n"
               f"Reward per referral: *{fmt_amount(float(get_setting('referral_bonus_amount') or '100'))}*")
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

    elif data == "user:channels":
        await q.edit_message_text(channels_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=channels_kb())

    elif data == "user:joinedcheck":
        ok = await check_user_joined_all(context, uid)
        if ok:
            if not has_passed_join_check(uid):
                set_passed_join_check(uid)
                # handle referral credit once
                if not ref_credit_given(uid):
                    ref = get_ref_by(uid)
                    if ref:
                        bonus = float(get_setting("referral_bonus_amount") or "100")
                        change_balance(ref, bonus)
                    set_ref_credit_given(uid)
            await q.answer("✅ All set. Thanks!", show_alert=True)
            await send_user_home(update, context, "✅ Join-check passed. You're good!")
        else:
            await q.answer("❌ You haven't joined all channels yet.", show_alert=True)

    elif data == "user:withdraw":
        if get_setting("withdraw_open") != "1":
            await q.answer("Withdrawals are currently OFF.", show_alert=True)
            return
        curbal = get_balance(uid)
        mn = float(get_setting("min_withdraw") or "1000")
        mx = float(get_setting("max_withdraw") or "500000")
        txt = (f"💸 *Request Withdrawal*\n"
               f"Balance: *{fmt_amount(curbal)}*\n"
               f"Min: *{fmt_amount(mn)}*  |  Max: *{fmt_amount(mx)}*\n\n"
               "Send your request in this format:\n"
               "`amount wallet_or_account`\n"
               "Example:\n"
               "`2000 0123456789-AccessBank`\n"
               "Or your crypto tag.\n\n"
               "_Type /cancel to abort._")
        context.user_data["await"] = ("withdraw_req",)
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN)

    elif data == "user:help":
        txt = ("*Help*\n"
               "• Use the buttons to get bonus, referral link, channels and withdraw.\n"
               "• Ask admin for support if needed.")
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

# ========= ADMIN PANEL CALLBACKS =========
async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Not admin.", show_alert=True)
        return

    data = q.data
    if data == "admin:close":
        await q.edit_message_text("Closed.")
        return

    if data == "admin:add_balance":
        context.user_data["await"] = ("add_balance",)
        await q.edit_message_text("Send: `user_id amount`\nExample: `123456789 500`", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:remove_balance":
        context.user_data["await"] = ("remove_balance",)
        await q.edit_message_text("Send: `user_id amount`\nExample: `123456789 200`", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:set_currency":
        context.user_data["await"] = ("set_currency",)
        await q.edit_message_text("Send currency code or symbol (e.g. `NGN`, `USD`, `₦`):", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:set_min":
        context.user_data["await"] = ("set_min",)
        await q.edit_message_text("Send *minimum withdraw* amount (number):", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:set_max":
        context.user_data["await"] = ("set_max",)
        await q.edit_message_text("Send *maximum withdraw* amount (number):", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:set_channels":
        context.user_data["await"] = ("set_channels",)
        await q.edit_message_text(
            "Send channel usernames separated by space (e.g. `@chan1 @chan2`).\n"
            "➡️ Make sure the *bot is an admin* in each channel.",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "admin:view_channels":
        chs = parse_channels()
        txt = "Current channels:\n" + ("\n".join([f"• {c}" for c in chs]) if chs else "— none —")
        await q.edit_message_text(txt, reply_markup=admin_panel_kb())

    elif data == "admin:ban":
        context.user_data["await"] = ("ban",)
        await q.edit_message_text("Send: `user_id` to BAN:", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:unban":
        context.user_data["await"] = ("unban",)
        await q.edit_message_text("Send: `user_id` to UNBAN:", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:broadcast":
        context.user_data["await"] = ("broadcast",)
        await q.edit_message_text("Send the *message* to broadcast to all users.\n(_Markdown supported_)", parse_mode=ParseMode.MARKDOWN)

    elif data == "admin:toggle_wd":
        cur = get_setting("withdraw_open") or "1"
        newv = "0" if cur == "1" else "1"
        set_setting("withdraw_open", newv)
        await q.edit_message_text(f"Withdraw toggled to: {'ON' if newv=='1' else 'OFF'}", reply_markup=admin_panel_kb())

# ========= ADMIN/USER TEXT INPUT HANDLER =========
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle awaited inputs for admin or user withdrawal
    awaitable = context.user_data.get("await")
    text = (update.message.text or "").strip()

    # User withdrawal request
    if awaitable and awaitable[0] == "withdraw_req":
        uid = update.effective_user.id
        if is_banned(uid):
            await update.message.reply_text("You are banned.")
            context.user_data.pop("await", None)
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text("Format: `amount wallet_or_account`\nExample: `2000 0123456789-AccessBank`", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            amount = float(parts[0])
        except Exception:
            await update.message.reply_text("Amount must be a number.")
            return
        wallet = parts[1]
        mn = float(get_setting("min_withdraw") or "1000")
        mx = float(get_setting("max_withdraw") or "500000")
        bal = get_balance(uid)
        if amount < mn or amount > mx:
            await update.message.reply_text(f"Amount must be between {fmt_amount(mn)} and {fmt_amount(mx)}.")
            return
        if amount > bal:
            await update.message.reply_text("Insufficient balance.")
            return
        # create request (status=pending); do NOT deduct yet (safer)
        db_exec("INSERT INTO withdraw_requests(user_id, amount, wallet, status, created_at) VALUES(?,?,?,?,?)",
                (uid, amount, wallet, "pending", datetime.utcnow().isoformat()))
        # notify admin
        if admin_id():
            try:
                await context.bot.send_message(
                    chat_id=admin_id(),
                    text=(f"🆕 *Withdraw Request*\nUser: `{uid}`\nAmount: *{fmt_amount(amount)}*\nWallet: `{wallet}`"),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        await update.message.reply_text("✅ Withdrawal request submitted. Admin will review.")
        context.user_data.pop("await", None)
        return

    # Admin awaited operations
    if awaitable and is_admin(update.effective_user.id):
        mode = awaitable[0]

        if mode in ("add_balance", "remove_balance"):
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text("Send exactly: `user_id amount`", parse_mode=ParseMode.MARKDOWN)
                return
            try:
                tgt = int(parts[0]); amt = float(parts[1])
            except Exception:
                await update.message.reply_text("Numbers only. Example: `123456789 500`", parse_mode=ParseMode.MARKDOWN)
                return
            add_user_if_not_exists(tgt)
            change = amt if mode == "add_balance" else -amt
            change_balance(tgt, change)
            await update.message.reply_text(f"✅ Done. New balance for {tgt}: {fmt_amount(get_balance(tgt))}")
            context.user_data.pop("await", None)
            return

        if mode == "set_currency":
            set_setting("currency", text)
            await update.message.reply_text(f"✅ Currency set to: {text}")
            context.user_data.pop("await", None)
            return

        if mode == "set_min":
            try:
                val = float(text)
                set_setting("min_withdraw", str(val))
                await update.message.reply_text(f"✅ Min withdraw set to {fmt_amount(val)}")
                context.user_data.pop("await", None)
            except Exception:
                await update.message.reply_text("Send a number only.")
            return

        if mode == "set_max":
            try:
                val = float(text)
                set_setting("max_withdraw", str(val))
                await update.message.reply_text(f"✅ Max withdraw set to {fmt_amount(val)}")
                context.user_data.pop("await", None)
            except Exception:
                await update.message.reply_text("Send a number only.")
            return

        if mode == "set_channels":
            chans = [c for c in text.split() if c.startswith("@")]
            set_setting("channels", json.dumps(chans))
            await update.message.reply_text(f"✅ Channels set: {' '.join(chans) if chans else '— none —'}\n"
                                            "Remember: add the *bot as ADMIN* in each channel.",
                                            parse_mode=ParseMode.MARKDOWN)
            context.user_data.pop("await", None)
            return

        if mode == "ban":
            try:
                tgt = int(text)
                add_user_if_not_exists(tgt)
                set_ban(tgt, True)
                await update.message.reply_text(f"🚫 User {tgt} banned.")
                context.user_data.pop("await", None)
            except Exception:
                await update.message.reply_text("Send a valid user_id (number).")
            return

        if mode == "unban":
            try:
                tgt = int(text)
                add_user_if_not_exists(tgt)
                set_ban(tgt, False)
                await update.message.reply_text(f"✅ User {tgt} unbanned.")
                context.user_data.pop("await", None)
            except Exception:
                await update.message.reply_text("Send a valid user_id (number).")
            return

        if mode == "broadcast":
            msg = text
            ids = all_user_ids()
            sent, fail = 0, 0
            for uid in ids:
                try:
                    await context.bot.send_message(chat_id=uid, text=msg, parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                except Exception:
                    fail += 1
            await update.message.reply_text(f"📢 Broadcast done. Sent: {sent}, Failed: {fail}")
            context.user_data.pop("await", None)
            return

    # If no awaited action: basic echo/help for normal users (ignore commands handled elsewhere)
    if not is_admin(update.effective_user.id):
        if is_banned(update.effective_user.id):
            await update.message.reply_text("You are banned.")
            return
        await send_user_home(update, context, "Hello! Use the buttons below 👇")

# ========= SETUP & RUN =========
async def on_startup(app):
    # Ensure DB initialized
    init_db()
    me = await app.bot.get_me()
    print(f"Bot @{me.username} is online.")

def main():
    init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("claimadmin", cmd_claimadmin))
    application.add_handler(CommandHandler("myid", cmd_myid))

    application.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^admin:"))
    application.add_handler(CallbackQueryHandler(on_user_callback, pattern=r"^user:"))

    # Text input handler for awaited steps & simple fallback
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot is running... Keep Pydroid open.")
    application.run_polling()

if __name__ == "__main__":
    main()