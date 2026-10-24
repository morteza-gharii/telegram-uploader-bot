"""Telegram uploader bot (multi-language, auto-delete, batch links, search...).

- Users send any file -> bot stores the file_id (files stay on Telegram servers)
  and returns a shareable deep link: https://t.me/<bot>?start=file_<id>
- Batch mode: several files -> one link (t.me/<bot>?start=batch_<id>)
- Each user picks their language (fa/en); all strings are translated.
- Files sent via deep links auto-delete after a configurable delay.
- Optional anti-forward (protect_content) and per-language promo captions.
- Download counters, file deletion, multi-admin support, file search.
- Forced subscription to channels, manageable by admins from inside the bot.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageOriginChannel, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "bot.db")

# httpx (used by python-telegram-bot) crashes while parsing this machine's
# NO_PROXY entries (bracketed IPv6 literals). The bot only talks to
# api.telegram.org, so dropping no_proxy is safe and lets the valid
# HTTP(S)_PROXY variables be used.
for _k in list(os.environ):
    if _k.lower() == "no_proxy":
        del os.environ[_k]

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

TOKEN = CONFIG["token"]
OWNER_ID = int(CONFIG["owner_id"])
BOT_USERNAME = ""  # filled in at startup via get_me()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Keep third-party logs quiet: httpx logs full request URLs (incl. bot token).
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("uploader-bot")

TYPE_ICONS = {
    "document": "📄", "photo": "🖼️", "video": "🎬", "audio": "🎵",
    "voice": "🎤", "animation": "🎞️", "video_note": "📹",
}


# ------------------------------------------------------------------ strings ---
STRINGS = {
    "fa": {
        "welcome": (
            "سلام! 👋\nبه ربات آپلودر خوش اومدی.\n\n"
            "📤 کافیه فایلت (عکس، ویدیو، موزیک، فایل و...) رو بفرستی "
            "تا لینک دانلود اختصاصیش رو بگیری.\n"
            "فایل‌ها روی سرور تلگرام ذخیره می‌شن و هرکس لینک رو داشته باشه "
            "می‌تونه دانلودش کنه."
        ),
        "choose_lang": "🌐 لطفاً زبانت رو انتخاب کن:\n🌐 Please choose your language:",
        "lang_set": "✅ زبان فارسی شد.",
        "blocked": "⛔ شما توسط مدیر ربات بلاک شده‌اید.",
        "must_join": "⚠️ برای استفاده از ربات، اول باید عضو کانال‌های زیر بشی:",
        "must_join_file": "⚠️ برای دریافت فایل، اول باید عضو کانال‌های زیر بشی:",
        "joined_ok": "✅ عالیه! حالا می‌تونی از ربات استفاده کنی.",
        "still_not_joined": "⚠️ هنوز عضو همه‌ی کانال‌ها نشدی:",
        "btn_joined": "✅ عضو شدم",
        "btn_lang": "🌐 زبان",
        "btn_admin": "⚙️ پنل مدیریت",
        "btn_batch": "📦 لینک گروهی",
        "btn_search": "🔍 جستجو",
        "invalid_link": "❌ لینک نامعتبره.",
        "file_not_found": "❌ این فایل پیدا نشد (شاید حذف شده).",
        "unsupported": "❌ این نوع فایل پشتیبانی نمی‌شه.",
        "upload_ok": (
            "✅ فایلت آپلود شد!\n\n🔗 لینک دانلود:\n{link}\n\n"
            "هرکس این لینک رو باز کنه می‌تونه فایل رو دانلود کنه."
        ),
        "autodel_warn": "⏱️ این فایل {seconds} ثانیه دیگه به‌صورت خودکار حذف می‌شه؛ اگه لازمش داری ذخیره‌ش کن.",
        "no_invite": "برای این کانال لینک عضویت ثبت نشده؛ از مدیر بخواه دوباره اضافه‌اش کند.",
        # batch
        "batch_start": (
            "📦 حالت لینک گروهی فعال شد!\n\n"
            "فایل‌هات رو یکی‌یکی بفرست. وقتی تموم شد، دکمه‌ی «✅ اتمام» رو بزن "
            "تا یه لینک واحد برای همه‌شون بگیری.\n\nبرای لغو /cancel رو بفرست."
        ),
        "batch_done_btn": "✅ اتمام و دریافت لینک",
        "batch_progress": "📦 تعداد فایل‌ها: {n}\n\nفایل بعدی رو بفرست یا «✅ اتمام» رو بزن.",
        "batch_empty": "❌ هنوز فایلی اضافه نکردی!",
        "batch_ok": "✅ لینک گروهی ساخته شد!\n\n🔗 {link}\n📁 تعداد فایل‌ها: {n}",
        "batch_cancelled": "📦 حالت گروهی لغو شد.",
        # search
        "search_prompt": "🔍 عبارت جستجو رو بفرست (حداقل ۲ حرف):\nبرای لغو /cancel رو بفرست.",
        "search_short": "❌ عبارت جستجو باید حداقل ۲ حرف باشه.",
        "search_none": "❌ چیزی برای «{q}» پیدا نشد.",
        "search_results": "🔍 نتایج «{q}» (برای دانلود بزن روشون):",
        # admin
        "admin_only": "⛔ این بخش فقط برای مدیر رباته.",
        "no_access": "⛔ دسترسی نداری.",
        "admin_title": "⚙️ پنل مدیریت",
        "admin_closed": "پنل بسته شد.",
        "btn_stats": "📊 آمار",
        "btn_broadcast": "📢 پیام همگانی",
        "btn_block": "🚫 بلاک کاربر",
        "btn_unblock": "✅ آنبلاک کاربر",
        "btn_channels": "📣 مدیریت کانال‌ها",
        "btn_autodel": "⏱️ حذف خودکار",
        "btn_files": "🗑️ حذف فایل",
        "btn_admins": "👥 مدیران",
        "btn_promo": "📝 کپشن تبلیغاتی",
        "btn_close": "◀️ بستن",
        "btn_back": "◀️ بازگشت",
        "protect_on": "🛡️ ضدفوروارد: روشن ✅",
        "protect_off": "🛡️ ضدفوروارد: خاموش ❌",
        "stats_text": (
            "📊 آمار ربات:\n\n👥 کاربران: {users}\n🚫 بلاک‌شده‌ها: {blocked}\n"
            "📁 فایل‌ها: {files}\n⬇️ دانلودها: {downloads}\n"
            "📣 کانال‌های عضویت اجباری: {channels}"
        ),
        "broadcast_prompt": "📢 پیامت رو بفرست (متن، عکس، ویدیو، فایل...).\nبرای لغو، /cancel رو بفرست.",
        "broadcast_sending": "📢 در حال ارسال به {n} کاربر...",
        "broadcast_done": "📢 پیام همگانی تموم شد.\n\n✅ موفق: {sent}\n❌ ناموفق: {failed}",
        "block_prompt": "🚫 آیدی عددی کاربری که می‌خوای بلاک بشه رو بفرست.\nبرای لغو، /cancel رو بفرست.",
        "unblock_prompt": "✅ آیدی عددی کاربری که می‌خوای آنبلاک بشه رو بفرست.\nبرای لغو، /cancel رو بفرست.",
        "blocked_done": "🚫 کاربر بلاک شد.",
        "unblocked_done": "✅ کاربر آنبلاک شد.",
        "id_numeric": "❌ آیدی باید عددی باشه. دوباره از پنل تلاش کن.",
        "cancelled": "لغو شد.",
        "nothing_cancel": "چیزی برای لغو نیست.",
        "channels_title": "📣 کانال‌های عضویت اجباری:",
        "channels_empty": "(هنوز کانالی ثبت نشده — عضویت اجباری غیرفعاله)",
        "btn_add_channel": "➕ افزودن کانال",
        "channel_add_prompt": (
            "📣 برای افزودن کانال، یکی از این کارها رو بکن:\n"
            "• یه پیام از کانال رو فوروارد کن، یا\n"
            "• یوزرنیم کانال رو بفرست (مثلاً @mychannel)\n\n"
            "⚠️ حتماً ربات باید توی اون کانال *ادمین* باشه!\n"
            "برای لغو، /cancel رو بفرست."
        ),
        "channel_bad_input": "❌ یه پیام فورواردشده از کانال یا یوزرنیم بفرست.",
        "channel_not_found": "❌ کانال پیدا نشد. یوزرنیم رو چک کن و دوباره بفرست.",
        "channel_not_channel": "❌ این یه کانال نیست!",
        "channel_no_admin": (
            "⚠️ ربات توی کانال «{title}» ادمین نیست!\n"
            "اول ربات رو ادمین کانال کن (بدون نیاز به دسترسی خاص)، بعد دوباره اضافه‌ش کن."
        ),
        "channel_added": "✅ کانال «{title}» به عضویت اجباری اضافه شد.",
        "channel_exists": "ℹ️ این کانال قبلاً اضافه شده.",
        "autodel_title": "⏱️ حذف خودکار فایل‌های ارسالی\n\nوضعیت فعلی: {current}",
        "autodel_off": "خاموش",
        "autodel_secs": "{n} ثانیه",
        "autodel_custom": "✏️ مقدار دلخواه",
        "autodel_custom_prompt": "⏱️ مدت زمان به ثانیه رو بفرست (۰ یعنی خاموش).\nبرای لغو، /cancel رو بفرست.",
        "autodel_set": "✅ تنظیم شد: {desc}",
        "files_title": "🗑️ برای حذف، فایل رو انتخاب کن (۱۵ فایل آخر):",
        "files_empty": "📁 هنوز فایلی ثبت نشده.",
        "file_deleted": "🗑️ فایل حذف شد.",
        "admins_title": "👥 مدیران ربات:",
        "admins_empty": "(فعلاً مدیر دیگه‌ای اضافه نشده — فقط مالک اصلی)",
        "btn_add_admin": "➕ افزودن مدیر",
        "admin_add_prompt": "👥 آیدی عددی مدیر جدید رو بفرست.\nبرای لغو /cancel رو بفرست.",
        "admin_added": "✅ مدیر اضافه شد.",
        "admin_exists": "ℹ️ این کاربر قبلاً مدیره.",
        "admin_removed": "🗑️ مدیر از لیست حذف شد.",
        "promo_title": (
            "📝 کپشن تبلیغاتی خودکار\n\n"
            "این متن موقع دانلود، زیر فایل اضافه می‌شه — به زبان خود کاربر.\n\n"
            "🇮🇷 فارسی:\n{fa}\n\n🇬🇧 انگلیسی:\n{en}"
        ),
        "promo_none": "(تنظیم نشده)",
        "promo_fa_btn": "🇮🇷 متن فارسی",
        "promo_en_btn": "🇬🇧 English text",
        "promo_clear": "🗑️ حذف کپشن",
        "promo_fa_prompt": "🇮🇷 متن تبلیغاتی فارسی رو بفرست:\nبرای لغو /cancel رو بفرست.",
        "promo_en_prompt": "🇬🇧 Send the English promo text:\nTo cancel, send /cancel.",
        "promo_set": "✅ ذخیره شد.",
        "promo_cleared": "🗑️ کپشن تبلیغاتی پاک شد.",
    },
    "en": {
        "welcome": (
            "Hello! 👋\nWelcome to the uploader bot.\n\n"
            "📤 Just send your file (photo, video, music, document, ...) "
            "and you'll get a dedicated download link.\n"
            "Files are stored on Telegram servers — anyone with the link "
            "can download it."
        ),
        "choose_lang": "🌐 Please choose your language:\n🌐 لطفاً زبانت رو انتخاب کن:",
        "lang_set": "✅ Language set to English.",
        "blocked": "⛔ You have been blocked by the bot admin.",
        "must_join": "⚠️ To use the bot, please join these channels first:",
        "must_join_file": "⚠️ To receive the file, please join these channels first:",
        "joined_ok": "✅ Great! You can now use the bot.",
        "still_not_joined": "⚠️ You haven't joined all channels yet:",
        "btn_joined": "✅ I've joined",
        "btn_lang": "🌐 Language",
        "btn_admin": "⚙️ Admin panel",
        "btn_batch": "📦 Batch link",
        "btn_search": "🔍 Search",
        "invalid_link": "❌ Invalid link.",
        "file_not_found": "❌ File not found (it may have been deleted).",
        "unsupported": "❌ This file type is not supported.",
        "upload_ok": (
            "✅ Your file was uploaded!\n\n🔗 Download link:\n{link}\n\n"
            "Anyone who opens this link can download the file."
        ),
        "autodel_warn": "⏱️ This file will be auto-deleted in {seconds} seconds — save it if you need it.",
        "no_invite": "No join link saved for this channel; ask the admin to re-add it.",
        # batch
        "batch_start": (
            "📦 Batch mode enabled!\n\n"
            "Send your files one by one. When done, tap «✅ Finish» "
            "to get a single link for all of them.\n\nTo cancel, send /cancel."
        ),
        "batch_done_btn": "✅ Finish & get link",
        "batch_progress": "📦 Files: {n}\n\nSend the next file or tap «✅ Finish».",
        "batch_empty": "❌ No files added yet!",
        "batch_ok": "✅ Batch link created!\n\n🔗 {link}\n📁 Files: {n}",
        "batch_cancelled": "📦 Batch mode cancelled.",
        # search
        "search_prompt": "🔍 Send your search query (min 2 characters):\nTo cancel, send /cancel.",
        "search_short": "❌ Query must be at least 2 characters.",
        "search_none": "❌ Nothing found for «{q}».",
        "search_results": "🔍 Results for «{q}» (tap to download):",
        # admin
        "admin_only": "⛔ This section is for the bot admin only.",
        "no_access": "⛔ Access denied.",
        "admin_title": "⚙️ Admin panel",
        "admin_closed": "Panel closed.",
        "btn_stats": "📊 Stats",
        "btn_broadcast": "📢 Broadcast",
        "btn_block": "🚫 Block user",
        "btn_unblock": "✅ Unblock user",
        "btn_channels": "📣 Channels",
        "btn_autodel": "⏱️ Auto-delete",
        "btn_files": "🗑️ Delete file",
        "btn_admins": "👥 Admins",
        "btn_promo": "📝 Promo caption",
        "btn_close": "◀️ Close",
        "btn_back": "◀️ Back",
        "protect_on": "🛡️ Anti-forward: ON ✅",
        "protect_off": "🛡️ Anti-forward: OFF ❌",
        "stats_text": (
            "📊 Bot stats:\n\n👥 Users: {users}\n🚫 Blocked: {blocked}\n"
            "📁 Files: {files}\n⬇️ Downloads: {downloads}\n"
            "📣 Force-join channels: {channels}"
        ),
        "broadcast_prompt": "📢 Send your message (text, photo, video, file...).\nTo cancel, send /cancel.",
        "broadcast_sending": "📢 Sending to {n} users...",
        "broadcast_done": "📢 Broadcast finished.\n\n✅ Success: {sent}\n❌ Failed: {failed}",
        "block_prompt": "🚫 Send the numeric ID of the user to block.\nTo cancel, send /cancel.",
        "unblock_prompt": "✅ Send the numeric ID of the user to unblock.\nTo cancel, send /cancel.",
        "blocked_done": "🚫 User blocked.",
        "unblocked_done": "✅ User unblocked.",
        "id_numeric": "❌ ID must be numeric. Try again from the panel.",
        "cancelled": "Cancelled.",
        "nothing_cancel": "Nothing to cancel.",
        "channels_title": "📣 Force-join channels:",
        "channels_empty": "(No channels added yet — force-join is disabled)",
        "btn_add_channel": "➕ Add channel",
        "channel_add_prompt": (
            "📣 To add a channel, either:\n"
            "• Forward a message from the channel, or\n"
            "• Send the channel username (e.g. @mychannel)\n\n"
            "⚠️ The bot must be an *admin* of that channel!\n"
            "To cancel, send /cancel."
        ),
        "channel_bad_input": "❌ Forward a message from the channel or send its username.",
        "channel_not_found": "❌ Channel not found. Check the username and try again.",
        "channel_not_channel": "❌ That's not a channel!",
        "channel_no_admin": (
            "⚠️ The bot is not an admin of «{title}»!\n"
            "Make the bot an admin first (no special rights needed), then add it again."
        ),
        "channel_added": "✅ Channel «{title}» added to force-join.",
        "channel_exists": "ℹ️ This channel was already added.",
        "autodel_title": "⏱️ Auto-delete sent files\n\nCurrent: {current}",
        "autodel_off": "Off",
        "autodel_secs": "{n} seconds",
        "autodel_custom": "✏️ Custom",
        "autodel_custom_prompt": "⏱️ Send the duration in seconds (0 = off).\nTo cancel, send /cancel.",
        "autodel_set": "✅ Set to: {desc}",
        "files_title": "🗑️ Choose a file to delete (last 15):",
        "files_empty": "📁 No files yet.",
        "file_deleted": "🗑️ File deleted.",
        "admins_title": "👥 Bot admins:",
        "admins_empty": "(No extra admins yet — only the owner)",
        "btn_add_admin": "➕ Add admin",
        "admin_add_prompt": "👥 Send the numeric ID of the new admin.\nTo cancel, send /cancel.",
        "admin_added": "✅ Admin added.",
        "admin_exists": "ℹ️ This user is already an admin.",
        "admin_removed": "🗑️ Admin removed.",
        "promo_title": (
            "📝 Auto promo caption\n\n"
            "This text is added under files on download — in the user's own language.\n\n"
            "🇮🇷 Persian:\n{fa}\n\n🇬🇧 English:\n{en}"
        ),
        "promo_none": "(not set)",
        "promo_fa_btn": "🇮🇷 Persian text",
        "promo_en_btn": "🇬🇧 English text",
        "promo_clear": "🗑️ Clear caption",
        "promo_fa_prompt": "🇮🇷 Send the Persian promo text:\nTo cancel, send /cancel.",
        "promo_en_prompt": "🇬🇧 Send the English promo text:\nTo cancel, send /cancel.",
        "promo_set": "✅ Saved.",
        "promo_cleared": "🗑️ Promo caption cleared.",
    },
}


def t(lang, key, **kwargs):
    table = STRINGS.get(lang) or STRINGS["fa"]
    s = table.get(key)
    if s is None:
        s = STRINGS["fa"].get(key, key)
    try:
        return s.format(**kwargs) if kwargs else s
    except Exception:
        return s


# ---------------------------------------------------------------- database ---
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, blocked INTEGER DEFAULT 0, "
            "created_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT, "
            "file_type TEXT, file_name TEXT, caption TEXT, "
            "uploader_id INTEGER, created_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS channels ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT UNIQUE, "
            "title TEXT, invite TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS admins ("
            "user_id INTEGER PRIMARY KEY, added_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS batches ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_id INTEGER, "
            "created_at TEXT, finalized INTEGER DEFAULT 0, "
            "downloads INTEGER DEFAULT 0)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS batch_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER, "
            "file_id INTEGER, position INTEGER)"
        )
        # migrations
        for ddl in (
            "ALTER TABLE channels ADD COLUMN invite TEXT",
            "ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'fa'",
            "ALTER TABLE users ADD COLUMN lang_set INTEGER DEFAULT 0",
            "ALTER TABLE files ADD COLUMN downloads INTEGER DEFAULT 0",
        ):
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()
        for key, val in (("auto_delete_secs", "120"), ("protect_content", "1")):
            if self.get_setting(key) is None:
                self.set_setting(key, val)

    # users ---------------------------------------------------------------
    def add_user(self, user_id):
        """Returns True if the user is new."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id, blocked, created_at) "
            "VALUES (?, 0, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_user(self, user_id):
        self.conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        self.conn.commit()

    def is_blocked(self, user_id):
        row = self.conn.execute(
            "SELECT blocked FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return bool(row and row["blocked"])

    def set_blocked(self, user_id, blocked):
        self.add_user(user_id)
        self.conn.execute(
            "UPDATE users SET blocked=? WHERE user_id=?", (1 if blocked else 0, user_id)
        )
        self.conn.commit()

    def all_users(self):
        return [
            r["user_id"]
            for r in self.conn.execute(
                "SELECT user_id FROM users WHERE blocked=0"
            ).fetchall()
        ]

    def user_count(self):
        return self.conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def blocked_count(self):
        return self.conn.execute(
            "SELECT COUNT(*) c FROM users WHERE blocked=1"
        ).fetchone()["c"]

    def get_lang(self, user_id):
        row = self.conn.execute(
            "SELECT lang FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return row["lang"] if row and row["lang"] else "fa"

    def needs_lang(self, user_id):
        row = self.conn.execute(
            "SELECT lang_set FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return not (row and row["lang_set"])

    def set_lang(self, user_id, code):
        self.add_user(user_id)
        self.conn.execute(
            "UPDATE users SET lang=?, lang_set=1 WHERE user_id=?", (code, user_id)
        )
        self.conn.commit()

    # files ---------------------------------------------------------------
    def add_file(self, file_id, file_type, file_name, caption, uploader_id):
        cur = self.conn.execute(
            "INSERT INTO files (file_id, file_type, file_name, caption, "
            "uploader_id, created_at) VALUES (?,?,?,?,?,?)",
            (
                file_id, file_type, file_name, caption, uploader_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_file(self, file_db_id):
        return self.conn.execute(
            "SELECT * FROM files WHERE id=?", (file_db_id,)
        ).fetchone()

    def file_count(self):
        return self.conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]

    def inc_downloads(self, file_db_id):
        self.conn.execute(
            "UPDATE files SET downloads = COALESCE(downloads,0)+1 WHERE id=?",
            (file_db_id,),
        )
        self.conn.commit()

    def total_downloads(self):
        row = self.conn.execute("SELECT SUM(downloads) s FROM files").fetchone()
        return row["s"] or 0

    def recent_files(self, limit=15):
        return self.conn.execute(
            "SELECT * FROM files ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def delete_file(self, file_db_id):
        self.conn.execute("DELETE FROM batch_items WHERE file_id=?", (file_db_id,))
        self.conn.execute("DELETE FROM files WHERE id=?", (file_db_id,))
        self.conn.commit()

    def search_files(self, query, limit=10):
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        return self.conn.execute(
            "SELECT * FROM files WHERE file_name LIKE ? ESCAPE '\\' "
            "OR caption LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()

    # channels ------------------------------------------------------------
    def add_channel(self, chat_id, title, invite=None):
        try:
            self.conn.execute(
                "INSERT INTO channels (chat_id, title, invite) VALUES (?, ?, ?)",
                (str(chat_id), title, invite),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def del_channel(self, channel_db_id):
        self.conn.execute("DELETE FROM channels WHERE id=?", (channel_db_id,))
        self.conn.commit()

    def all_channels(self):
        return self.conn.execute("SELECT * FROM channels ORDER BY id").fetchall()

    # admins --------------------------------------------------------------
    def add_admin(self, user_id):
        try:
            self.conn.execute(
                "INSERT INTO admins (user_id, added_at) VALUES (?, ?)",
                (user_id, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def del_admin(self, user_id):
        self.conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        self.conn.commit()

    def is_admin_db(self, user_id):
        return self.conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone() is not None

    def all_admins(self):
        return self.conn.execute("SELECT * FROM admins ORDER BY user_id").fetchall()

    # batches -------------------------------------------------------------
    def create_batch(self, uploader_id):
        cur = self.conn.execute(
            "INSERT INTO batches (uploader_id, created_at, finalized) "
            "VALUES (?, ?, 0)",
            (uploader_id, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_batch_item(self, batch_id, file_db_id):
        pos = self.conn.execute(
            "SELECT COUNT(*) c FROM batch_items WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]
        self.conn.execute(
            "INSERT INTO batch_items (batch_id, file_id, position) "
            "VALUES (?, ?, ?)",
            (batch_id, file_db_id, pos),
        )
        self.conn.commit()
        return pos + 1

    def batch_item_count(self, batch_id):
        return self.conn.execute(
            "SELECT COUNT(*) c FROM batch_items WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]

    def get_batch(self, batch_id):
        return self.conn.execute(
            "SELECT * FROM batches WHERE id=?", (batch_id,)
        ).fetchone()

    def get_batch_files(self, batch_id):
        return self.conn.execute(
            "SELECT f.* FROM batch_items bi JOIN files f ON f.id=bi.file_id "
            "WHERE bi.batch_id=? ORDER BY bi.position",
            (batch_id,),
        ).fetchall()

    def finalize_batch(self, batch_id):
        self.conn.execute(
            "UPDATE batches SET finalized=1 WHERE id=?", (batch_id,)
        )
        self.conn.commit()

    def delete_batch(self, batch_id):
        self.conn.execute("DELETE FROM batch_items WHERE batch_id=?", (batch_id,))
        self.conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        self.conn.commit()

    def cleanup_drafts(self, uploader_id):
        for r in self.conn.execute(
            "SELECT id FROM batches WHERE uploader_id=? AND finalized=0",
            (uploader_id,),
        ).fetchall():
            self.delete_batch(r["id"])

    def inc_batch_downloads(self, batch_id):
        self.conn.execute(
            "UPDATE batches SET downloads = COALESCE(downloads,0)+1 WHERE id=?",
            (batch_id,),
        )
        self.conn.commit()

    # settings ------------------------------------------------------------
    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        self.conn.commit()


db = DB(DB_PATH)


# ------------------------------------------------------------------ helpers ---
def is_owner(user_id):
    return user_id == OWNER_ID


def is_admin(user_id):
    return is_owner(user_id) or db.is_admin_db(user_id)


async def missing_channels(user_id, bot) -> list:
    """Channels the user has not joined yet."""
    missing = []
    for ch in db.all_channels():
        try:
            member = await bot.get_chat_member(ch["chat_id"], user_id)
            if member.status not in ("member", "administrator", "creator"):
                missing.append(ch)
        except TelegramError:
            missing.append(ch)
    return missing


def channel_link(ch):
    if ch["invite"]:
        return ch["invite"]
    cid = str(ch["chat_id"])
    if cid.startswith("@"):
        return f"https://t.me/{cid[1:]}"
    return None


def channel_buttons(channels, lang, check_button=True):
    rows = []
    for ch in channels:
        url = channel_link(ch)
        if url:
            rows.append([InlineKeyboardButton(f"📣 {ch['title']}", url=url)])
        else:
            rows.append(
                [InlineKeyboardButton(f"📣 {ch['title']}", callback_data="no_invite")]
            )
    if check_button:
        rows.append([InlineKeyboardButton(t(lang, "btn_joined"), callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)


def lang_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🇮🇷 فارسی", callback_data="lang_fa")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        ]
    )


def main_menu_keyboard(user_id, lang):
    rows = [
        [
            InlineKeyboardButton(t(lang, "btn_batch"), callback_data="menu_batch"),
            InlineKeyboardButton(t(lang, "btn_search"), callback_data="menu_search"),
        ],
        [InlineKeyboardButton(t(lang, "btn_lang"), callback_data="menu_lang")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(t(lang, "btn_admin"), callback_data="admin_main")])
    return InlineKeyboardMarkup(rows)


async def show_main_menu(msg, context, user_id, lang, edit=False):
    """Join check, then welcome (or join prompt). msg: message to reply/edit."""
    missing = await missing_channels(user_id, context.bot)
    if missing:
        text, kb = t(lang, "must_join"), channel_buttons(missing, lang)
    else:
        text, kb = t(lang, "welcome"), main_menu_keyboard(user_id, lang)
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.reply_text(text, reply_markup=kb)


async def require_access(update: Update, context: ContextTypes.DEFAULT_TYPE, lang):
    """Returns True if the user may proceed, else sends the right prompt."""
    user = update.effective_user
    msg = update.effective_message

    if db.is_blocked(user.id):
        await msg.reply_text(t(lang, "blocked"))
        return False

    missing = await missing_channels(user.id, context.bot)
    if missing:
        await msg.reply_text(
            t(lang, "must_join"),
            reply_markup=channel_buttons(missing, lang),
        )
        return False
    return True


def build_caption(base, promo):
    base = (base or "").strip()
    promo = (promo or "").strip()
    if promo and base:
        combined = base + "\n\n" + promo
    elif promo:
        combined = promo
    else:
        combined = base
    return combined[:1000] if len(combined) > 1000 else combined


async def send_stored_file(bot, chat_id, row, promo=None):
    """Sends the stored file, returns the sent Message."""
    protect = db.get_setting("protect_content", "1") == "1"
    caption = build_caption(row["caption"], promo)
    kwargs = {"chat_id": chat_id, "protect_content": protect}
    if caption:
        kwargs["caption"] = caption
    ftype, file_id = row["file_type"], row["file_id"]
    if ftype == "document":
        return await bot.send_document(document=file_id, **kwargs)
    elif ftype == "photo":
        return await bot.send_photo(photo=file_id, **kwargs)
    elif ftype == "video":
        return await bot.send_video(video=file_id, **kwargs)
    elif ftype == "audio":
        return await bot.send_audio(audio=file_id, **kwargs)
    elif ftype == "voice":
        return await bot.send_voice(voice=file_id, **kwargs)
    elif ftype == "animation":
        return await bot.send_animation(animation=file_id, **kwargs)
    elif ftype == "video_note":
        return await bot.send_video_note(video_note=file_id, chat_id=chat_id,
                                         protect_content=protect)
    else:
        return await bot.send_document(document=file_id, **kwargs)


async def _auto_delete(bot, chat_id, message_ids, delay):
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def _schedule_autodelete(context, chat_id, message_ids, reply_msg, lang):
    try:
        delay = int(db.get_setting("auto_delete_secs", "120") or 0)
    except ValueError:
        delay = 0
    if delay > 0 and message_ids:
        warn = await reply_msg.reply_text(t(lang, "autodel_warn", seconds=delay))
        asyncio.create_task(
            _auto_delete(context.bot, chat_id, message_ids + [warn.message_id], delay)
        )


async def do_download(msg, context, user_id, payload, lang):
    """Handles t.me/<bot>?start=file_<id> and ...?start=batch_<id>.

    The pending payload is consumed here, but re-saved if the user still
    needs to join channels, so the download resumes after they join.
    """
    context.user_data.pop("pending_file", None)  # consume
    is_batch = payload.startswith("batch_")
    try:
        obj_id = int(payload.split("_", 1)[1])
    except (ValueError, IndexError):
        await msg.reply_text(t(lang, "invalid_link"))
        return

    if is_batch:
        batch = db.get_batch(obj_id)
        rows = db.get_batch_files(obj_id) if batch and batch["finalized"] else []
        if not rows:
            await msg.reply_text(t(lang, "file_not_found"))
            return
    else:
        row = db.get_file(obj_id)
        if not row:
            await msg.reply_text(t(lang, "file_not_found"))
            return
        rows = [row]

    missing = await missing_channels(user_id, context.bot)
    if missing:
        context.user_data["pending_file"] = payload  # resume after join
        await msg.reply_text(
            t(lang, "must_join_file"),
            reply_markup=channel_buttons(missing, lang),
        )
        return

    promo = (db.get_setting(f"promo_{lang}") or "").strip()
    sent_ids = []
    first = True
    for r in rows:
        try:
            sent = await send_stored_file(context.bot, msg.chat.id, r,
                                          promo=promo if first else None)
        except TelegramError:
            log.warning("failed to send file db id %s", r["id"])
            continue
        first = False
        if sent:
            sent_ids.append(sent.message_id)
            db.inc_downloads(r["id"])
    if is_batch and sent_ids:
        db.inc_batch_downloads(obj_id)
    if not sent_ids:
        await msg.reply_text(t(lang, "file_not_found"))
        return
    await _schedule_autodelete(context, msg.chat.id, sent_ids, msg, lang)


# ------------------------------------------------------------------ /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = db.add_user(user.id)
    lang = db.get_lang(user.id)

    if db.is_blocked(user.id):
        await update.message.reply_text(t(lang, "blocked"))
        return

    args = context.args
    if args and (args[0].startswith("file_") or args[0].startswith("batch_")):
        if is_new or db.needs_lang(user.id):
            context.user_data["pending_file"] = args[0]
            await update.message.reply_text(
                t(lang, "choose_lang"), reply_markup=lang_keyboard()
            )
            return
        await do_download(update.message, context, user.id, args[0], lang)
        return

    if is_new or db.needs_lang(user.id):
        await update.message.reply_text(
            t(lang, "choose_lang"), reply_markup=lang_keyboard()
        )
        return

    await show_main_menu(update.message, context, user.id, lang)


# ----------------------------------------------------------------- uploading --
MEDIA_FILTER = (
    filters.Document.ALL
    | filters.PHOTO
    | filters.VIDEO
    | filters.AUDIO
    | filters.VOICE
    | filters.ANIMATION
    | filters.VIDEO_NOTE
)


def extract_media(message):
    if message.document:
        d = message.document
        return "document", d.file_id, d.file_name or "file"
    if message.photo:
        p = message.photo[-1]
        return "photo", p.file_id, "photo.jpg"
    if message.video:
        v = message.video
        return "video", v.file_id, v.file_name or "video.mp4"
    if message.audio:
        a = message.audio
        return "audio", a.file_id, a.file_name or a.title or "audio.mp3"
    if message.voice:
        return "voice", message.voice.file_id, "voice.ogg"
    if message.animation:
        return "animation", message.animation.file_id, "animation.gif"
    if message.video_note:
        return "video_note", message.video_note.file_id, "video_note.mp4"
    return None, None, None


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting"):
        return
    user = update.effective_user
    lang = db.get_lang(user.id)
    if not await require_access(update, context, lang):
        return
    ftype, file_id, file_name = extract_media(update.message)
    if not file_id:
        await update.message.reply_text(t(lang, "unsupported"))
        return
    caption = update.message.caption
    fid = db.add_file(file_id, ftype, file_name, caption, user.id)

    # Batch mode: collect into the draft batch instead of a single link.
    if context.user_data.get("batch_mode"):
        batch_id = context.user_data.get("batch_id")
        if not batch_id:
            context.user_data.pop("batch_mode", None)
        else:
            n = db.add_batch_item(batch_id, fid)
            try:
                await context.bot.edit_message_text(
                    t(lang, "batch_progress", n=n),
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data.get("batch_msg_id"),
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(t(lang, "batch_done_btn"),
                                               callback_data="batch_done")]]
                    ),
                )
            except TelegramError:
                pass
            return

    link = f"https://t.me/{BOT_USERNAME}?start=file_{fid}"
    await update.message.reply_text(t(lang, "upload_ok", link=link))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles free text input: currently only the search flow."""
    if context.user_data.get("awaiting") == "search":
        await do_search(update, context)


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    user = update.effective_user
    lang = db.get_lang(user.id)
    query = (update.message.text or "").strip()
    if len(query) < 2:
        await update.message.reply_text(t(lang, "search_short"))
        return
    rows = db.search_files(query)
    if not rows:
        await update.message.reply_text(t(lang, "search_none", q=query))
        return
    buttons = []
    for r in rows:
        name = (r["file_name"] or "file")[:30]
        icon = TYPE_ICONS.get(r["file_type"], "📄")
        buttons.append(
            [InlineKeyboardButton(
                f"{icon} {name}",
                url=f"https://t.me/{BOT_USERNAME}?start=file_{r['id']}",
            )]
        )
    await update.message.reply_text(
        t(lang, "search_results", q=query),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ------------------------------------------------------------------ callbacks -
async def on_no_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = db.get_lang(update.effective_user.id)
    await update.callback_query.answer(t(lang, "no_invite"), show_alert=True)


async def on_check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    lang = db.get_lang(user_id)
    missing = await missing_channels(user_id, context.bot)
    if missing:
        await q.edit_message_text(
            t(lang, "still_not_joined"),
            reply_markup=channel_buttons(missing, lang),
        )
    else:
        pending = context.user_data.get("pending_file")
        if pending:
            await q.edit_message_text(t(lang, "joined_ok"))
            await do_download(q.message, context, user_id, pending, lang)
        else:
            await q.edit_message_text(t(lang, "joined_ok"))
            await show_main_menu(q.message, context, user_id, lang)


async def on_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles lang_fa / lang_en / menu_lang callbacks."""
    q = update.callback_query
    user_id = q.from_user.id
    data = q.data
    await q.answer()

    if data == "menu_lang":
        await q.edit_message_text(t(db.get_lang(user_id), "choose_lang"),
                                  reply_markup=lang_keyboard())
        return

    code = data.split("_", 1)[1]  # fa / en
    if code not in STRINGS:
        code = "fa"
    db.set_lang(user_id, code)
    lang = code

    # Don't pop pending_file here: do_download consumes it, and re-saves it
    # if the user still needs to join channels.
    pending = context.user_data.get("pending_file")
    if pending:
        await q.edit_message_text(t(lang, "lang_set"))
        await do_download(q.message, context, user_id, pending, lang)
    else:
        await show_main_menu(q.message, context, user_id, lang, edit=True)


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main-menu buttons: batch mode, search, finish batch."""
    q = update.callback_query
    user_id = q.from_user.id
    lang = db.get_lang(user_id)
    data = q.data
    await q.answer()

    if data == "menu_batch":
        if not await require_access(update, context, lang):
            return
        db.cleanup_drafts(user_id)
        batch_id = db.create_batch(user_id)
        context.user_data["batch_mode"] = True
        context.user_data["batch_id"] = batch_id
        sent = await q.message.reply_text(
            t(lang, "batch_start"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(lang, "batch_done_btn"),
                                       callback_data="batch_done")]]
            ),
        )
        context.user_data["batch_msg_id"] = sent.message_id

    elif data == "menu_search":
        context.user_data["awaiting"] = "search"
        await q.message.reply_text(t(lang, "search_prompt"))

    elif data == "batch_done":
        batch_id = context.user_data.pop("batch_id", None)
        context.user_data.pop("batch_mode", None)
        context.user_data.pop("batch_msg_id", None)
        if not batch_id:
            await q.message.reply_text(t(lang, "batch_empty"))
            return
        n = db.batch_item_count(batch_id)
        if n == 0:
            db.delete_batch(batch_id)
            await q.message.reply_text(t(lang, "batch_empty"))
            return
        db.finalize_batch(batch_id)
        link = f"https://t.me/{BOT_USERNAME}?start=batch_{batch_id}"
        await q.message.reply_text(t(lang, "batch_ok", link=link, n=n))


# ------------------------------------------------------------- admin panel ----
def admin_keyboard(lang, user_id):
    rows = [
        [InlineKeyboardButton(t(lang, "btn_stats"), callback_data="admin_stats")],
        [InlineKeyboardButton(t(lang, "btn_broadcast"), callback_data="admin_broadcast")],
        [
            InlineKeyboardButton(t(lang, "btn_block"), callback_data="admin_block"),
            InlineKeyboardButton(t(lang, "btn_unblock"), callback_data="admin_unblock"),
        ],
        [InlineKeyboardButton(t(lang, "btn_channels"), callback_data="admin_channels")],
        [InlineKeyboardButton(t(lang, "btn_files"), callback_data="admin_files")],
        [
            InlineKeyboardButton(t(lang, "btn_autodel"), callback_data="admin_autodel"),
            InlineKeyboardButton(
                t(lang, "protect_on") if db.get_setting("protect_content", "1") == "1"
                else t(lang, "protect_off"),
                callback_data="admin_protect",
            ),
        ],
        [InlineKeyboardButton(t(lang, "btn_promo"), callback_data="admin_promo")],
    ]
    if is_owner(user_id):
        rows.append([InlineKeyboardButton(t(lang, "btn_admins"), callback_data="admin_admins")])
    rows.append([InlineKeyboardButton(t(lang, "btn_lang"), callback_data="menu_lang")])
    rows.append([InlineKeyboardButton(t(lang, "btn_close"), callback_data="admin_close")])
    return InlineKeyboardMarkup(rows)


def autodel_desc(lang, secs):
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        secs = 0
    return t(lang, "autodel_off") if secs <= 0 else t(lang, "autodel_secs", n=secs)


def autodel_keyboard(lang):
    presets = [0, 30, 60, 120, 300, 900]
    rows, row = [], []
    for s in presets:
        row.append(InlineKeyboardButton(autodel_desc(lang, s), callback_data=f"autodel_{s}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(t(lang, "autodel_custom"), callback_data="autodel_custom")])
    rows.append([InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")])
    return InlineKeyboardMarkup(rows)


async def show_autodel(q, lang):
    current = autodel_desc(lang, db.get_setting("auto_delete_secs", "120"))
    await q.edit_message_text(
        t(lang, "autodel_title", current=current),
        reply_markup=autodel_keyboard(lang),
    )


def promo_texts():
    fa = (db.get_setting("promo_fa") or "").strip()
    en = (db.get_setting("promo_en") or "").strip()
    return fa, en


async def show_promo(q, lang):
    fa, en = promo_texts()
    await q.edit_message_text(
        t(lang, "promo_title",
          fa=fa if fa else t(lang, "promo_none"),
          en=en if en else t(lang, "promo_none")),
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(t(lang, "promo_fa_btn"), callback_data="promo_fa"),
                 InlineKeyboardButton(t(lang, "promo_en_btn"), callback_data="promo_en")],
                [InlineKeyboardButton(t(lang, "promo_clear"), callback_data="promo_clear")],
                [InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")],
            ]
        ),
    )


async def show_files(q, lang):
    files = db.recent_files(15)
    if not files:
        await q.edit_message_text(
            t(lang, "files_empty"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")]]
            ),
        )
        return
    rows = []
    for r in files:
        name = (r["file_name"] or "file")[:22]
        icon = TYPE_ICONS.get(r["file_type"], "📄")
        dl = r["downloads"] or 0
        rows.append(
            [InlineKeyboardButton(f"❌ {icon} {name} ({dl}⬇)",
                                  callback_data=f"file_del_{r['id']}")]
        )
    rows.append([InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")])
    await q.edit_message_text(t(lang, "files_title"), reply_markup=InlineKeyboardMarkup(rows))


async def show_admins(q, lang):
    admins = db.all_admins()
    rows = []
    for a in admins:
        rows.append(
            [InlineKeyboardButton(f"❌ {a['user_id']}",
                                  callback_data=f"admin_del_{a['user_id']}")]
        )
    if not admins:
        text = t(lang, "admins_title") + "\n" + t(lang, "admins_empty")
    else:
        text = t(lang, "admins_title")
    rows.append([InlineKeyboardButton(t(lang, "btn_add_admin"), callback_data="admin_add")])
    rows.append([InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = db.get_lang(update.effective_user.id)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    await update.message.reply_text(
        t(lang, "admin_title"),
        reply_markup=admin_keyboard(lang, update.effective_user.id),
    )


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    lang = db.get_lang(user_id)
    if not is_admin(user_id):
        await q.answer(t(lang, "no_access"), show_alert=True)
        return
    data = q.data
    await q.answer()
    back = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")]]
    )

    if data == "admin_main":
        context.user_data.pop("awaiting", None)
        await q.edit_message_text(t(lang, "admin_title"),
                                  reply_markup=admin_keyboard(lang, user_id))

    elif data == "admin_close":
        await q.edit_message_text(t(lang, "admin_closed"))

    elif data == "admin_stats":
        await q.edit_message_text(
            t(lang, "stats_text", users=db.user_count(), blocked=db.blocked_count(),
              files=db.file_count(), downloads=db.total_downloads(),
              channels=len(db.all_channels())),
            reply_markup=back,
        )

    elif data == "admin_broadcast":
        context.user_data["awaiting"] = "broadcast"
        await q.edit_message_text(t(lang, "broadcast_prompt"), reply_markup=back)

    elif data == "admin_block":
        context.user_data["awaiting"] = "block"
        await q.edit_message_text(t(lang, "block_prompt"), reply_markup=back)

    elif data == "admin_unblock":
        context.user_data["awaiting"] = "unblock"
        await q.edit_message_text(t(lang, "unblock_prompt"), reply_markup=back)

    elif data == "admin_channels":
        await show_channels(q, lang)

    elif data == "admin_files":
        await show_files(q, lang)

    elif data.startswith("file_del_"):
        db.delete_file(int(data.split("_", 2)[2]))
        await q.answer(t(lang, "file_deleted"))
        await show_files(q, lang)

    elif data == "admin_autodel":
        await show_autodel(q, lang)

    elif data == "admin_protect":
        cur = db.get_setting("protect_content", "1") == "1"
        db.set_setting("protect_content", "0" if cur else "1")
        await q.edit_message_text(t(lang, "admin_title"),
                                  reply_markup=admin_keyboard(lang, user_id))

    elif data == "admin_promo":
        await show_promo(q, lang)

    elif data == "promo_fa":
        context.user_data["awaiting"] = "promo_fa"
        await q.edit_message_text(t(lang, "promo_fa_prompt"), reply_markup=back)

    elif data == "promo_en":
        context.user_data["awaiting"] = "promo_en"
        await q.edit_message_text(t(lang, "promo_en_prompt"), reply_markup=back)

    elif data == "promo_clear":
        db.set_setting("promo_fa", "")
        db.set_setting("promo_en", "")
        await q.answer(t(lang, "promo_cleared"))
        await show_promo(q, lang)

    elif data == "admin_admins":
        if not is_owner(user_id):
            await q.answer(t(lang, "no_access"), show_alert=True)
            return
        await show_admins(q, lang)

    elif data == "admin_add":
        if not is_owner(user_id):
            await q.answer(t(lang, "no_access"), show_alert=True)
            return
        context.user_data["awaiting"] = "admin_add"
        await q.edit_message_text(t(lang, "admin_add_prompt"), reply_markup=back)

    elif data.startswith("admin_del_"):
        if not is_owner(user_id):
            await q.answer(t(lang, "no_access"), show_alert=True)
            return
        db.del_admin(int(data.split("_", 2)[2]))
        await q.answer(t(lang, "admin_removed"))
        await show_admins(q, lang)

    elif data.startswith("autodel_"):
        part = data.split("_", 1)[1]
        if part == "custom":
            context.user_data["awaiting"] = "autodel_custom"
            await q.edit_message_text(
                t(lang, "autodel_custom_prompt"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(t(lang, "btn_back"),
                                           callback_data="admin_autodel")]]
                ),
            )
        else:
            db.set_setting("auto_delete_secs", part)
            await show_autodel(q, lang)

    elif data == "channel_add":
        context.user_data["awaiting"] = "channel_add"
        await q.edit_message_text(
            t(lang, "channel_add_prompt"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_channels")]]
            ),
        )

    elif data.startswith("channel_del_"):
        db.del_channel(int(data.split("_", 2)[2]))
        await show_channels(q, lang)


async def show_channels(q, lang):
    channels = db.all_channels()
    rows = []
    for ch in channels:
        rows.append(
            [InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"channel_del_{ch['id']}")]
        )
    rows.append([InlineKeyboardButton(t(lang, "btn_add_channel"), callback_data="channel_add")])
    rows.append([InlineKeyboardButton(t(lang, "btn_back"), callback_data="admin_main")])
    text = t(lang, "channels_title")
    if not channels:
        text += "\n" + t(lang, "channels_empty")
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id)
    had = False
    if context.user_data.pop("awaiting", None):
        had = True
    if context.user_data.pop("batch_mode", None):
        batch_id = context.user_data.pop("batch_id", None)
        context.user_data.pop("batch_msg_id", None)
        if batch_id:
            db.delete_batch(batch_id)
        had = True
    if had and is_admin(user_id):
        await update.message.reply_text(t(lang, "cancelled"),
                                        reply_markup=admin_keyboard(lang, user_id))
    elif had:
        await update.message.reply_text(t(lang, "cancelled"))
    else:
        await update.message.reply_text(t(lang, "nothing_cancel"))


async def on_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the next message after an admin-panel prompt."""
    if not is_admin(update.effective_user.id):
        return
    user_id = update.effective_user.id
    lang = db.get_lang(user_id)
    state = context.user_data.get("awaiting")
    if not state:
        return
    msg = update.message
    kb = admin_keyboard(lang, user_id)

    if state == "broadcast":
        context.user_data.pop("awaiting", None)
        await do_broadcast(update, context, msg, lang, kb)
    elif state in ("block", "unblock"):
        context.user_data.pop("awaiting", None)
        text = (msg.text or "").strip()
        if not text.isdigit():
            await msg.reply_text(t(lang, "id_numeric"), reply_markup=kb)
            return
        db.set_blocked(int(text), state == "block")
        await msg.reply_text(
            t(lang, "blocked_done") if state == "block" else t(lang, "unblocked_done"),
            reply_markup=kb,
        )
    elif state == "channel_add":
        await handle_channel_add(update, context, msg, lang, kb)
    elif state == "autodel_custom":
        context.user_data.pop("awaiting", None)
        text = (msg.text or "").strip()
        if not text.isdigit():
            await msg.reply_text(t(lang, "autodel_custom_prompt"), reply_markup=kb)
            return
        db.set_setting("auto_delete_secs", text)
        await msg.reply_text(t(lang, "autodel_set", desc=autodel_desc(lang, text)),
                             reply_markup=kb)
    elif state in ("promo_fa", "promo_en"):
        context.user_data.pop("awaiting", None)
        text = (msg.text or "").strip()
        db.set_setting(state, text)
        await msg.reply_text(t(lang, "promo_set"), reply_markup=kb)
    elif state == "admin_add":
        if not is_owner(user_id):
            context.user_data.pop("awaiting", None)
            await msg.reply_text(t(lang, "no_access"), reply_markup=kb)
            return
        context.user_data.pop("awaiting", None)
        text = (msg.text or "").strip()
        if not text.isdigit():
            await msg.reply_text(t(lang, "id_numeric"), reply_markup=kb)
            return
        target = int(text)
        if target == OWNER_ID or db.is_admin_db(target):
            await msg.reply_text(t(lang, "admin_exists"), reply_markup=kb)
            return
        db.add_admin(target)
        await msg.reply_text(t(lang, "admin_added"), reply_markup=kb)


async def handle_channel_add(update, context, msg, lang, kb):
    chat_ref = None
    origin = msg.forward_origin
    if isinstance(origin, MessageOriginChannel):
        chat_ref = origin.chat.id
    elif msg.text:
        chat_ref = msg.text.strip()
        if chat_ref.startswith("https://t.me/"):
            chat_ref = "@" + chat_ref.split("https://t.me/")[1].split("/")[0]
    else:
        await msg.reply_text(t(lang, "channel_bad_input"))
        return

    try:
        chat = await context.bot.get_chat(chat_ref)
    except TelegramError:
        await msg.reply_text(t(lang, "channel_not_found"))
        return

    if chat.type != "channel":
        await msg.reply_text(t(lang, "channel_not_channel"))
        return

    title = chat.title
    public_ref = f"@{chat.username}" if chat.username else str(chat.id)

    try:
        me = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(chat.id, me.id)
        bot_is_admin = bot_member.status in ("administrator", "creator")
    except TelegramError:
        bot_is_admin = False

    if not bot_is_admin:
        context.user_data.pop("awaiting", None)
        await msg.reply_text(t(lang, "channel_no_admin", title=title), reply_markup=kb)
        return

    invite = None
    try:
        invite = await context.bot.export_chat_invite_link(chat.id)
    except Exception:
        invite = chat.invite_link

    if db.add_channel(public_ref, title, invite):
        context.user_data.pop("awaiting", None)
        await msg.reply_text(t(lang, "channel_added", title=title), reply_markup=kb)
    else:
        context.user_data.pop("awaiting", None)
        await msg.reply_text(t(lang, "channel_exists"), reply_markup=kb)


async def do_broadcast(update, context, msg, lang, kb):
    users = db.all_users()
    sent, failed = 0, 0
    progress = await update.message.reply_text(t(lang, "broadcast_sending", n=len(users)))
    for uid in users:
        try:
            await context.bot.copy_message(
                chat_id=uid, from_chat_id=msg.chat_id, message_id=msg.message_id
            )
            sent += 1
        except Forbidden:
            db.remove_user(uid)
            failed += 1
        except TelegramError as e:
            log.warning("broadcast to %s failed: %s", uid, e)
            failed += 1
        await asyncio.sleep(0.05)
    await progress.edit_text(
        t(lang, "broadcast_done", sent=sent, failed=failed),
        reply_markup=kb,
    )


# --------------------------------------------------------------------- main ---
def main():
    global BOT_USERNAME
    from telegram.request import HTTPXRequest

    # PID file for the watchdog
    with open(os.path.join(BASE_DIR, "bot.pid"), "w") as f:
        f.write(str(os.getpid()))

    # Generous timeouts: the egress proxy can be slow on cold connections.
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_entry))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(on_check_sub, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(on_no_invite, pattern="^no_invite$"))
    app.add_handler(CallbackQueryHandler(on_lang, pattern="^(lang_|menu_lang$)"))
    app.add_handler(CallbackQueryHandler(on_menu, pattern="^(menu_batch|menu_search|batch_done)$"))
    app.add_handler(CallbackQueryHandler(
        on_admin_callback,
        pattern="^(admin_|channel_|autodel_|file_del_|promo_)",
    ))

    # Admin typed input for admin flows must run BEFORE the media uploader.
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, on_admin_input), group=-1
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(MEDIA_FILTER & ~filters.COMMAND, on_media))

    async def _post_init(app: Application):
        global BOT_USERNAME
        me = await app.bot.get_me()
        BOT_USERNAME = me.username
        log.info("Bot started as @%s", BOT_USERNAME)

    app.post_init = _post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
