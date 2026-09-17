import os
import sqlite3
import asyncio
import tempfile
import shutil
from pathlib import Path

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_FILE = "bot.db"
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE = 49 * 1024 * 1024

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    language TEXT DEFAULT 'fa',
    blocked INTEGER DEFAULT 0
)""")
db.execute("""CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT UNIQUE NOT NULL,
    title TEXT,
    username TEXT,
    url TEXT NOT NULL
)""")
db.commit()

def add_user(uid):
    db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    db.commit()

def lang(uid):
    row = db.execute("SELECT language FROM users WHERE user_id=?", (uid,)).fetchone()
    return row[0] if row else "fa"

def set_lang(uid, language):
    add_user(uid)
    db.execute("UPDATE users SET language=? WHERE user_id=?", (language, uid))
    db.commit()

def is_blocked(uid):
    row = db.execute("SELECT blocked FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0])

def channels():
    return db.execute("SELECT id, chat_id, title, username, url FROM channels ORDER BY id").fetchall()

def language_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇦🇫 دری", callback_data="lang_fa"),
         InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")]
    ])

def join_kb():
    rows = []
    for cid, chat_id, title, username, url in channels():
        rows.append([InlineKeyboardButton(text=f"📢 {title or username or 'Channel'}", url=url)])
    rows.append([InlineKeyboardButton(text="✅ تأیید عضویت / Verify", callback_data="verify")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def check_joined(uid):
    for _, chat_id, *_ in channels():
        try:
            m = await bot.get_chat_member(chat_id, uid)
            if m.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True

async def require_join(message: Message):
    if not channels():
        return True
    if await check_joined(message.from_user.id):
        return True
    text = ("اول در کانال زیر عضو شو، سپس روی «تأیید عضویت» بزن."
            if lang(message.from_user.id) == "fa"
            else "Please join the required channel(s), then press Verify.")
    await message.answer(text, reply_markup=join_kb())
    return False

@dp.message(Command("start"))
async def start(message: Message):
    add_user(message.from_user.id)
    if is_blocked(message.from_user.id):
        return await message.answer("🚫 You are blocked.")
    await message.answer("زبان را انتخاب کنید / Choose your language:", reply_markup=language_kb())

@dp.callback_query(F.data.startswith("lang_"))
async def choose_language(call: CallbackQuery):
    language = "fa" if call.data == "lang_fa" else "en"
    set_lang(call.from_user.id, language)
    if await check_joined(call.from_user.id):
        text = "لینک ویدیو را بفرست 🎬" if language == "fa" else "Send the video link 🎬"
        await call.message.edit_text(text)
    else:
        text = ("لطفاً ابتدا در کانال عضو شوید:" if language == "fa"
                else "Please join the required channel(s):")
        await call.message.edit_text(text, reply_markup=join_kb())
    await call.answer()

@dp.callback_query(F.data == "verify")
async def verify(call: CallbackQuery):
    if await check_joined(call.from_user.id):
        text = "✅ عضویت تأیید شد.\nلینک ویدیو را بفرست 🎬" if lang(call.from_user.id) == "fa" else "✅ Membership verified.\nSend the video link 🎬"
        await call.message.edit_text(text)
    else:
        text = "❌ هنوز عضو کانال نیستید." if lang(call.from_user.id) == "fa" else "❌ You are not a member yet."
        await call.answer(text, show_alert=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن کانال", callback_data="a_add"),
         InlineKeyboardButton(text="➖ حذف کانال", callback_data="a_del")],
        [InlineKeyboardButton(text="📋 کانال‌ها", callback_data="a_list"),
         InlineKeyboardButton(text="👥 آمار", callback_data="a_stats")],
        [InlineKeyboardButton(text="🚫 مسدود کردن", callback_data="a_block"),
         InlineKeyboardButton(text="✅ آزاد کردن", callback_data="a_unblock")],
        [InlineKeyboardButton(text="📢 ارسال همگانی", callback_data="a_broadcast")],
    ])

@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 پنل مدیریت", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("a_"))
async def admin_actions(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Not allowed", show_alert=True)
    action = call.data[2:]
    if action == "list":
        rows = channels()
        if not rows:
            return await call.message.answer("هیچ کانال اجباری ثبت نشده.")
        text = "\n".join(f"{r[0]}. {r[2] or r[3] or r[1]} — {r[4]}" for r in rows)
        return await call.message.answer("📋 کانال‌های اجباری:\n\n" + text)
    if action == "stats":
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return await call.message.answer(f"📊 کاربران: {users}\n📢 کانال‌های اجباری: {len(channels())}")
    if action in ("add", "del", "block", "unblock", "broadcast"):
        prompts = {
            "add": "برای افزودن کانال، لینک عمومی کانال را همینجا بفرست؛ ربات باید در کانال ادمین باشد.",
            "del": "برای حذف کانال، شماره آن را بفرست.",
            "block": "آیدی عددی کاربر را بفرست.",
            "unblock": "آیدی عددی کاربر را بفرست.",
            "broadcast": "متن پیام همگانی را بفرست."
        }
        await call.message.answer(prompts[action])
        db.execute("INSERT OR REPLACE INTO users(user_id, language) VALUES(?, ?)", (ADMIN_ID, "admin_"+action))
        db.commit()

@dp.message(F.text)
async def text_handler(message: Message):
    add_user(message.from_user.id)
    uid = message.from_user.id
    if uid == ADMIN_ID:
        state = db.execute("SELECT language FROM users WHERE user_id=?", (uid,)).fetchone()
        state = state[0] if state else ""
        if state == "admin_add":
            url = message.text.strip()
            if not url.startswith("https://t.me/"):
                return await message.answer("لینک عمومی کانال را بفرست.")
            username = url.rstrip("/").split("/")[-1]
            try:
                chat = await bot.get_chat("@"+username)
                db.execute("INSERT INTO channels(chat_id,title,username,url) VALUES(?,?,?,?)",
                           (str(chat.id), chat.title or username, username, url))
                db.commit()
                return await message.answer("✅ کانال اضافه شد.", reply_markup=admin_kb())
            except Exception as e:
                return await message.answer("❌ کانال پیدا نشد یا ربات ادمین کانال نیست.")
        if state == "admin_del":
            try:
                db.execute("DELETE FROM channels WHERE id=?", (int(message.text.strip()),))
                db.commit()
                return await message.answer("✅ حذف شد.", reply_markup=admin_kb())
            except:
                return await message.answer("❌ شماره نادرست است.")
        if state in ("admin_block","admin_unblock"):
            try:
                target = int(message.text.strip())
                db.execute("UPDATE users SET blocked=? WHERE user_id=?", (1 if state=="admin_block" else 0, target))
                db.commit()
                return await message.answer("✅ انجام شد.", reply_markup=admin_kb())
            except:
                return await message.answer("❌ آیدی نادرست است.")
        if state == "admin_broadcast":
            rows = db.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()
            ok = 0
            for (target,) in rows:
                try:
                    await bot.send_message(target, message.text)
                    ok += 1
                except:
                    pass
                await asyncio.sleep(0.04)
            db.execute("UPDATE users SET language=? WHERE user_id=?", ("fa", ADMIN_ID))
            db.commit()
            return await message.answer(f"✅ ارسال شد: {ok}", reply_markup=admin_kb())
    if is_blocked(uid):
        return
    if not await require_join(message):
        return
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await message.answer("❌ لینک معتبر بفرست.")
    status = await message.answer("⏳ در حال دانلود..." if lang(uid)=="fa" else "⏳ Downloading...")
    folder = Path(tempfile.mkdtemp(dir=DOWNLOAD_DIR))
    try:
        def dl():
            opts = {
                "format": "best[ext=mp4]/best",
                "outtmpl": str(folder / "%(title).80s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
                p = Path(y.prepare_filename(info))
                return p.with_suffix(".mp4") if p.with_suffix(".mp4").exists() else p
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(None, dl)
        if not path.exists():
            raise RuntimeError("file missing")
        if path.stat().st_size > MAX_FILE_SIZE:
            return await status.edit_text("❌ حجم ویدیو برای ارسال زیاد است.")
        await status.edit_text("📤 در حال ارسال...")
        with open(path, "rb") as f:
            await message.answer_document(f, caption="🎬 Video")
        await status.delete()
    except Exception:
        await status.edit_text("❌ دانلود این لینک موفق نشد.")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

async def main():
    if BOT_TOKEN == "PUT_BOT_TOKEN_HERE" or not ADMIN_ID:
        raise RuntimeError("BOT_TOKEN و ADMIN_ID را در Railway Variables تنظیم کنید.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
