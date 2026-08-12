#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בוט טלגרם להורדת תכנים מהאינטרנט (יוטיוב, אינסטגרם, טיקטוק ועוד אלפי אתרים)
מבוסס על yt-dlp + python-telegram-bot

שימוש:
1. python -m venv venv && source venv/bin/activate  (או venv\\Scripts\\activate בווינדוס)
2. pip install -r requirements.txt
3. התקן ffmpeg (חובה לאיחוד וידאו+אודות ולהמרה ל-mp3):
   - Ubuntu/Debian: sudo apt install ffmpeg
   - Mac (Homebrew): brew install ffmpeg
   - Windows: הורד מ-ffmpeg.org והוסף ל-PATH
4. צור קובץ .env (על בסיס env.example) והכנס לתוכו את BOT_TOKEN שקיבלת מ-BotFather
5. הרץ: python bot.py
"""

import os
import re
import glob
import shutil
import logging
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import yt_dlp

# ---------------------------------------------------------------------------
# הגדרות בסיסיות
# ---------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
INSTAGRAM_COOKIES = os.getenv("INSTAGRAM_COOKIES")  # נתיב לקובץ cookies.txt, אופציונלי

# מגבלת טלגרם להעלאת קובץ ע"י בוט רגיל (Bot API בענן) היא כ-50MB.
# אם תריץ Local Bot API Server אפשר להעלות עד 2GB - במקרה כזה שנה את הערך הזה.
MAX_FILESIZE_BYTES = 50 * 1024 * 1024

URL_REGEX = re.compile(r"https?://\S+")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("downloader-bot")

# מאגר תהליכים לריצת yt-dlp (חוסם) בלי לחסום את הבוט האסינכרוני
executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# פונקציות עזר להורדה בפועל (רצות ב-thread נפרד)
# ---------------------------------------------------------------------------

def _build_ydl_opts(download_dir: str, mode: str) -> dict:
    """מרכיב את הגדרות yt-dlp בהתאם למצב: video / audio"""
    outtmpl = os.path.join(download_dir, "%(title).150B.%(ext)s")

    common_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
    }

    if INSTAGRAM_COOKIES and os.path.exists(INSTAGRAM_COOKIES):
        common_opts["cookiefile"] = INSTAGRAM_COOKIES

    if mode == "audio":
        common_opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        )
    else:
        # וידאו: מנסה למצוא איכות שנכנסת למגבלת הגודל, ואם לא - הכי טוב שיש
        size_limit = MAX_FILESIZE_BYTES
        common_opts.update(
            {
                "format": (
                    f"bestvideo[filesize<{size_limit}]+bestaudio/"
                    f"best[filesize<{size_limit}]/best"
                ),
                "merge_output_format": "mp4",
            }
        )

    return common_opts


def download_content(url: str, mode: str, download_dir: str) -> str:
    """מוריד את התוכן ומחזיר את הנתיב לקובץ שהתקבל. רץ בתוך thread executor."""
    ydl_opts = _build_ydl_opts(download_dir, mode)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # אם זה פלייליסט/אלבום, ניקח את הפריט הראשון בלבד (noplaylist אמור לחסום את זה, אבל ליתר בטחון)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        filepath = ydl.prepare_filename(info)

        if mode == "audio":
            # לאחר ההמרה הסיומת תהיה mp3
            base, _ = os.path.splitext(filepath)
            mp3_path = base + ".mp3"
            if os.path.exists(mp3_path):
                filepath = mp3_path

    # אם yt-dlp לא נתן שם מדויק (למשל אחרי מיזוג פורמטים), נחפש את הקובץ שנוצר בפועל
    if not os.path.exists(filepath):
        candidates = [
            f for f in glob.glob(os.path.join(download_dir, "*")) if os.path.isfile(f)
        ]
        if candidates:
            filepath = max(candidates, key=os.path.getmtime)
        else:
            raise FileNotFoundError("ההורדה הסתיימה אך לא נמצא קובץ פלט")

    return filepath


# ---------------------------------------------------------------------------
# handlers של הבוט
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "היי! 👋\n"
        "שלח לי קישור מיוטיוב, אינסטגרם, טיקטוק (וגם עוד אלפי אתרים אחרים) "
        "ואני אוריד לך את התוכן ואשלח אותו כאן.\n\n"
        "לאחר ששולחים קישור, תוכל לבחור להוריד כווידאו או כאודיו (mp3)."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "פשוט שלח קישור לתוכן (וידאו/רילס/סרטון) ובחר וידאו או אודיו.\n"
        "הערה: טלגרם מגביל בוטים להעלאת קבצים עד כ-50MB, אז תכנים ארוכים/כבדים "
        "עלולים להיכשל או להישלח באיכות מוקטנת."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_REGEX.search(text)

    if not match:
        await update.message.reply_text("שלח לי קישור תקין (מתחיל ב-http/https) 🙂")
        return

    url = match.group(0)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 וידאו", callback_data=f"video|{url}"),
                InlineKeyboardButton("🎵 אודיו (mp3)", callback_data=f"audio|{url}"),
            ]
        ]
    )
    await update.message.reply_text("איך תרצה להוריד את זה?", reply_markup=keyboard)


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        mode, url = query.data.split("|", 1)
    except ValueError:
        await query.edit_message_text("קרתה תקלה בבקשה, נסה לשלוח את הקישור מחדש.")
        return

    await query.edit_message_text("⏳ מוריד... זה יכול לקחת כמה שניות עד כמה דקות, בהתאם לגודל.")

    chat_id = query.message.chat_id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

    download_dir = tempfile.mkdtemp(prefix="tgdl_")
    loop = asyncio.get_running_loop()

    try:
        filepath = await loop.run_in_executor(
            executor, download_content, url, mode, download_dir
        )

        filesize = os.path.getsize(filepath)
        if filesize > MAX_FILESIZE_BYTES:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ הקובץ שהתקבל גדול מדי לשליחה בטלגרם "
                    f"({filesize / (1024 * 1024):.1f}MB, מגבלה כ-50MB).\n"
                    "נסה קישור אחר / איכות נמוכה יותר, או להריץ בוט מקומי (Local Bot API) בלי מגבלת 50MB."
                ),
            )
            return

        with open(filepath, "rb") as f:
            if mode == "audio":
                await context.bot.send_audio(chat_id=chat_id, audio=f)
            else:
                await context.bot.send_video(chat_id=chat_id, video=f, supports_streaming=True)

    except yt_dlp.utils.DownloadError as e:
        logger.warning("Download error for %s: %s", url, e)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ לא הצלחתי להוריד מהקישור הזה.\n"
                "אפשר שהתוכן פרטי / דורש התחברות / הוסר, או שהאתר לא נתמך."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected error for %s", url)
        await context.bot.send_message(
            chat_id=chat_id, text=f"❌ קרתה שגיאה לא צפויה: {e}"
        )
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "לא נמצא BOT_TOKEN. צור קובץ .env (על בסיס env.example) והכנס את הטוקן שקיבלת מ-BotFather."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_choice))

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
