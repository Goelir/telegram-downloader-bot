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
import time
import uuid
import shutil
import logging
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor

import requests
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

# נתיב לקובץ cookies.txt (פורמט Netscape), אופציונלי. מומלץ לייצא מחשבון גוגל/אינסטגרם
# ייעודי שלא משמש לגלישה רגילה - כך הסשן לא מתבטל בגלל שימוש רגיל בדפדפן.
COOKIES_FILE = os.getenv("COOKIES_FILE") or os.getenv("INSTAGRAM_COOKIES")

# פרוקסי (רזידנציאלי) אופציונלי - עוקף חסימות שמבוססות על IP של שרתי ענן.
# פורמט לדוגמה (Decodo/Smartproxy עם sticky session):
#   http://USER:PASS@gate.decodo.com:10001
PROXY_URL = os.getenv("PROXY_URL")

# אם aria2c מותקן בשרת (sudo apt install aria2), משתמשים בו כמוריד חיצוני עם
# כמה חיבורים מקבילים - יכול לזרז משמעותית הורדות מפורמטים לא-מפוצלים (progressive).
ARIA2C_AVAILABLE = shutil.which("aria2c") is not None

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

# בקשות ממתינות לבחירת וידאו/אודיו. המפתח קצר (לא כל ה-URL/שאילתה) כי לטלגרם
# יש מגבלה של 64 בייט על callback_data של כפתורים - קישורים/חיפושים ארוכים
# היו חורגים ממנה וגורמים לכפתורים לא לעבוד.
pending_requests: dict[str, str] = {}


def resolve_spotify_query(url: str) -> str | None:
    """שולף שם שיר+אמן מקישור ספוטיפיי (בלי API key - פשוט קורא את כותרת הדף).
    ספוטיפיי לא מאפשר להוריד את קובץ האודיו האמיתי משם (הצפנת DRM), אז זה
    רק בשביל לחפש את השיר ביוטיוב ולהוריד משם."""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        match = re.search(r"<title>(.*?)</title>", resp.text, re.DOTALL)
        if not match:
            return None
        title = match.group(1)
        title = title.replace(" | Spotify", "").strip()
        title = re.sub(
            r"\s*[-–]\s*(song|single|album)(\s+and\s+lyrics)?\s+by\s*",
            " ",
            title,
            flags=re.IGNORECASE,
        )
        return title.strip() or None
    except Exception:
        logger.exception("Failed to resolve Spotify metadata for %s", url)
        return None


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
        # מוריד כמה פרגמנטים של פורמט מפוצל (DASH, נפוץ ביוטיוב) במקביל
        # במקום אחד-אחרי-השני - מנצל יותר טוב את רוחב הפס הזמין.
        "concurrent_fragment_downloads": 4,
    }

    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        common_opts["cookiefile"] = COOKIES_FILE

    if PROXY_URL:
        common_opts["proxy"] = PROXY_URL

    if ARIA2C_AVAILABLE:
        # aria2c עם כמה חיבורים מקבילים לקובץ - מזרז הורדות של פורמטים לא-מפוצלים
        common_opts["external_downloader"] = "aria2c"
        common_opts["external_downloader_args"] = {
            "aria2c": ["-x", "16", "-s", "16", "-k", "1M"]
        }

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


def _clear_dir(download_dir: str) -> None:
    """מנקה קבצים שנשארו מניסיון קודם שנכשל, כדי שלא יתבלבלו עם הניסיון הבא."""
    for f in glob.glob(os.path.join(download_dir, "*")):
        try:
            if os.path.isfile(f):
                os.remove(f)
        except OSError:
            pass


def _download_single(url: str, mode: str, download_dir: str) -> str:
    """מוריד קישור בודד (לא חיפוש) ומחזיר את הנתיב לקובץ. עלול לזרוק DownloadError."""
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


# מילות מפתח שמזהות "lyric video" - מעדיפים תוצאות בלעדיהן (וידאו/אודיו רשמי)
_LYRIC_KEYWORDS = ("lyric", "lyrics", "מילים")

# כמה תוצאות גולמיות לשלוף מיוטיוב לפני הסינון (חלקן עלולות להיחסם ולהיפסל)
_FLAT_SEARCH_POOL = 10

# כמה גרסאות ניתנות-להורדה בפועל להציג למשתמש, לכל היותר
_MAX_PRESENTED = 5


def _search_youtube_candidates(query: str, limit: int = _FLAT_SEARCH_POOL) -> list:
    """מביא רשימת מועמדים מיוטיוב (בלי הורדה בפועל) ומעדיף כאלה שאינם lyric video."""
    search_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        search_opts["cookiefile"] = COOKIES_FILE
    if PROXY_URL:
        search_opts["proxy"] = PROXY_URL

    with yt_dlp.YoutubeDL(search_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    entries = [e for e in (info.get("entries") or []) if e]

    preferred, deprioritized = [], []
    for entry in entries:
        title = (entry.get("title") or "").lower()
        if any(k in title for k in _LYRIC_KEYWORDS):
            deprioritized.append(entry)
        else:
            preferred.append(entry)
    return preferred + deprioritized


def _probe_downloadable(url: str) -> bool:
    """בודק בפועל (בלי להוריד קובץ) אם ניתן לחלץ פורמטים מהקישור. זה חושף
    גם חסימות כמו 'Sign in to confirm you're not a bot' - שמופיעות כבר
    בשלב הזה ולא רק בהורדה בפועל - ומאפשר לסנן אותן החוצה לפני שמציגים
    את התוצאה למשתמש."""
    probe_opts = {"quiet": True, "no_warnings": True}
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        probe_opts["cookiefile"] = COOKIES_FILE
    if PROXY_URL:
        probe_opts["proxy"] = PROXY_URL
    try:
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            ydl.extract_info(url, download=False)
        return True
    except yt_dlp.utils.DownloadError as e:
        logger.info("Probe failed for %s: %s", url, e)
        return False
    except Exception:
        logger.exception("Unexpected error probing %s", url)
        return False


def _search_downloadable_candidates(query: str) -> list:
    """מחפש ביוטיוב ובודק כל תוצאה בפועל (probe), ומחזיר רק גרסאות שבאמת
    ניתן להוריד מהן - כדי לא להציג למשתמש אפשרויות שייכשלו. אם אף תוצאה
    מיוטיוב לא עברה את הבדיקה, מנסה גם חיפוש ב-SoundCloud כפולבאק."""
    valid = []

    try:
        raw_candidates = _search_youtube_candidates(query)
    except Exception:
        logger.exception("Search failed for query %s", query)
        raw_candidates = []

    for entry in raw_candidates:
        video_url = entry.get("url") or entry.get("webpage_url")
        video_id = entry.get("id")
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        if not video_url:
            continue

        if _probe_downloadable(video_url):
            item = dict(entry)
            item["url"] = video_url
            item["source"] = "youtube"
            valid.append(item)

        if len(valid) >= _MAX_PRESENTED:
            break

    if not valid:
        # אף תוצאה מיוטיוב לא ניתנת להורדה - ננסה SoundCloud כפולבאק.
        # גם ב-SoundCloud חלק מהטראקים (בעיקר של אמנים/חברות תקליטים גדולות)
        # מסומנים "DRM protected" ולא ניתנים להורדה - אז בודקים כל תוצאה
        # בפועל (probe) בדיוק כמו ביוטיוב, ולא סומכים על כך שהיא קיימת בחיפוש.
        try:
            search_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
            }
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"scsearch{_FLAT_SEARCH_POOL}:{query}", download=False)
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                track_url = entry.get("url") or entry.get("webpage_url")
                if not track_url:
                    continue
                if _probe_downloadable(track_url):
                    item = dict(entry)
                    item["url"] = track_url
                    item["source"] = "soundcloud"
                    valid.append(item)
                if len(valid) >= _MAX_PRESENTED:
                    break
        except Exception:
            logger.exception("SoundCloud fallback search failed for %s", query)

    return valid


def download_content(target: str, mode: str, download_dir: str, attempts: int = 3) -> str:
    """מוריד את התוכן ומחזיר את הנתיב לקובץ שהתקבל. רץ בתוך thread executor.

    חסימת 'Sign in to confirm you're not a bot' של יוטיוב לא תמיד עקבית - אותו
    סרטון בדיוק יכול להצליח ברגע אחד ולהיכשל כמה דקות אחר כך (כנראה בגלל
    התנהגות לא-דטרמיניסטית ברמת ה-IP/session/client). לכן גם גרסה שכבר עברה
    בדיקה (probe) לפני שהוצגה למשתמש עלולה להיכשל בהורדה בפועל. במקום לוותר
    מיד, מנסים כמה פעמים עם השהיה קצרה ביניהן לפני שמדווחים על כישלון."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            _clear_dir(download_dir)
            time.sleep(3)
        try:
            return _download_single(target, mode, download_dir)
        except yt_dlp.utils.DownloadError as e:
            logger.warning(
                "Download attempt %d/%d failed for %s: %s", attempt, attempts, target, e
            )
            last_error = e
            continue
    raise last_error


# ---------------------------------------------------------------------------
# handlers של הבוט
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "היי! 👋\n"
        "שלח לי קישור מיוטיוב, אינסטגרם, טיקטוק, ספוטיפיי (וגם עוד אלפי אתרים אחרים) "
        "ואני אוריד לך את התוכן ואשלח אותו כאן.\n\n"
        "אפשר גם פשוט לכתוב שם שיר/אמן בלי קישור - אני אחפש ואוריד.\n\n"
        "לאחר מכן תוכל לבחור להוריד כווידאו או כאודיו (mp3)."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "שלח קישור לתוכן (וידאו/רילס/סרטון/ספוטיפיי) או פשוט שם שיר לחיפוש, "
        "ובחר וידאו או אודיו.\n"
        "הערה: טלגרם מגביל בוטים להעלאת קבצים עד כ-50MB, אז תכנים ארוכים/כבדים "
        "עלולים להיכשל או להישלח באיכות מוקטנת.\n"
        "הערה נוספת: תוכן שיוטיוב חוסם מאחורי דרישת התחברות (הגבלת גיל וכו') "
        "לא ניתן להורדה - זו הגבלה מצד יוטיוב על התוכן עצמו.\n"
        "בחיפוש לפי שם שיר: הבוט בודק מראש אילו גרסאות ביוטיוב באמת ניתנות "
        "להורדה (עם עדיפות לגרסאות שאינן lyric video), ומציג לך רק אותן לבחירה. "
        "אם אף גרסה ביוטיוב לא עברה את הבדיקה, הוא בודק גם ב-SoundCloud."
    )


def _format_candidate_label(entry: dict) -> str:
    title = (entry.get("title") or "ללא כותרת").strip()
    uploader = (entry.get("uploader") or "").strip()
    label = f"{title} - {uploader}" if uploader else title
    if entry.get("source") == "soundcloud":
        label = f"{label} (SoundCloud)"
    return label[:60]


async def _present_search_results(
    status_msg, query_text: str, note: str = ""
) -> None:
    """מחפש ביוטיוב, בודק בפועל אילו תוצאות ניתן להוריד (probe), ומציג
    למשתמש רק גרסאות שכבר אושרו כניתנות להורדה - במקום להציג תוצאות
    שעלולות להיחסם. זה לוקח קצת יותר זמן מחיפוש רגיל כי כל תוצאה נבדקת."""
    await status_msg.edit_text(
        f"🔎 מחפש ובודק אילו גרסאות של \"{query_text}\" ניתן להוריד בפועל...\n"
        "(זה יכול לקחת כמה שניות, כי כל תוצאה נבדקת)"
    )

    loop = asyncio.get_running_loop()
    try:
        candidates = await loop.run_in_executor(
            executor, _search_downloadable_candidates, query_text
        )
    except Exception:
        logger.exception("Search failed for query %s", query_text)
        candidates = []

    if not candidates:
        await status_msg.edit_text(
            f"❌ לא מצאתי אף גרסה של \"{query_text}\" שניתן להוריד כרגע.\n"
            "בדקתי גם ביוטיוב וגם ב-SoundCloud - כל התוצאות שם או חסומות "
            "(YouTube) או מוגנות DRM (SoundCloud). זה קורה בעיקר עם שירים "
            "פופולריים של אמנים/חברות תקליטים גדולות."
        )
        return

    key = uuid.uuid4().hex[:12]
    pending_requests[key] = candidates

    buttons = [
        [InlineKeyboardButton(f"🎵 {_format_candidate_label(e)}", callback_data=f"pick|{key}|{i}")]
        for i, e in enumerate(candidates)
    ]
    text = f"{note}מצאתי גרסאות של \"{query_text}\" שניתן להוריד - בחר איזו:"
    await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    match = URL_REGEX.search(text)

    if match:
        url = match.group(0)
        if "spotify.com" in url:
            status_msg = await update.message.reply_text(
                "🔎 מזהה את השיר מהקישור של ספוטיפיי..."
            )
            query_text = resolve_spotify_query(url)
            if not query_text:
                await status_msg.edit_text(
                    "❌ לא הצלחתי לזהות את פרטי השיר מהקישור הזה."
                )
                return
            await _present_search_results(
                status_msg, query_text, note="(נמצא מספוטיפיי)\n"
            )
            return
        else:
            target = url
            label = url
    elif text:
        status_msg = await update.message.reply_text(f"🔎 מחפש \"{text}\" ביוטיוב...")
        await _present_search_results(status_msg, text)
        return
    else:
        await update.message.reply_text(
            "שלח לי קישור (http/https) או שם שיר לחיפוש 🙂"
        )
        return

    # שומרים את היעד בפועל תחת מפתח קצר, כי ל-callback_data של טלגרם יש
    # מגבלה של 64 בייט - קישורים/שאילתות ארוכות לא היו נכנסות בו.
    key = uuid.uuid4().hex[:12]
    pending_requests[key] = target

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 וידאו", callback_data=f"video|{key}"),
                InlineKeyboardButton("🎵 אודיו (mp3)", callback_data=f"audio|{key}"),
            ]
        ]
    )
    await update.message.reply_text(f"{label}\n\nאיך תרצה להוריד?", reply_markup=keyboard)


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data

    # שלב בחירת גרסה מתוך רשימת תוצאות חיפוש (לפני בחירת וידאו/אודיו)
    if data.startswith("pick|"):
        try:
            _, key, idx_str = data.split("|", 2)
            idx = int(idx_str)
        except ValueError:
            await query.edit_message_text("קרתה תקלה בבקשה, נסה לשלוח את הבקשה מחדש.")
            return

        candidates = pending_requests.get(key)
        if not candidates or idx >= len(candidates):
            await query.edit_message_text(
                "הבקשה הזו כבר לא בתוקף. שלח את שם השיר/קישור שוב בבקשה."
            )
            return

        entry = candidates[idx]
        video_url = entry.get("url") or entry.get("webpage_url")
        video_id = entry.get("id")
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        if not video_url:
            await query.edit_message_text("קרתה תקלה עם התוצאה הזו, נסה לבחור גרסה אחרת.")
            return

        title = _format_candidate_label(entry)

        new_key = uuid.uuid4().hex[:12]
        pending_requests[new_key] = video_url

        # ל-SoundCloud אין וידאו - מציגים רק אפשרות mp3
        if entry.get("source") == "soundcloud":
            row = [InlineKeyboardButton("🎵 הורד mp3", callback_data=f"audio|{new_key}")]
        else:
            row = [
                InlineKeyboardButton("🎬 וידאו", callback_data=f"video|{new_key}"),
                InlineKeyboardButton("🎵 אודיו (mp3)", callback_data=f"audio|{new_key}"),
            ]

        keyboard = InlineKeyboardMarkup(
            [
                row,
            ]
        )
        await query.edit_message_text(f"🎵 {title}\n\nאיך תרצה להוריד?", reply_markup=keyboard)
        return

    try:
        mode, key = data.split("|", 1)
    except ValueError:
        await query.edit_message_text("קרתה תקלה בבקשה, נסה לשלוח את הבקשה מחדש.")
        return

    url = pending_requests.pop(key, None)
    if not url:
        await query.edit_message_text(
            "הבקשה הזו כבר לא בתוקף (אפשר להשתמש בכפתור פעם אחת). "
            "שלח את הקישור/שם השיר שוב בבקשה."
        )
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
                "❌ לא הצלחתי להוריד מהקישור/גרסה הזו.\n"
                "אפשר שהתוכן פרטי / דורש התחברות / הוסר, או שהאתר לא נתמך.\n"
                "אם זו הייתה תוצאת חיפוש - נסה לשלוח את שם השיר שוב ולבחור גרסה אחרת מהרשימה."
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
