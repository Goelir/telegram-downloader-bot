# בוט טלגרם להורדת תכנים

בוט טלגרם שמוריד תכנים מיוטיוב, אינסטגרם, טיקטוק, ועוד אלפי אתרים (בזכות [yt-dlp](https://github.com/yt-dlp/yt-dlp)).
שולחים לבוט קישור, בוחרים וידאו או אודיו (mp3), והבוט שולח את הקובץ בחזרה בצ'אט.

## שלב 1 - יצירת הבוט בטלגרם (אם עדיין אין לך)

1. פתח בטלגרם שיחה עם [@BotFather](https://t.me/BotFather).
2. שלח את הפקודה `/newbot`.
3. תן לבוט שם תצוגה (כל שם, למשל "המוריד שלי").
4. תן לו username שמסתיים ב-`bot`, למשל `eli_downloader_bot`.
5. BotFather יחזיר לך הודעה עם **טוקן** שנראה כך:
   `123456789:AAExampleTokenExampleTokenExample`
6. שמור את הטוקן הזה - הוא הסוד שמזהה את הבוט שלך. אל תשתף אותו עם אף אחד.

## שלב 2 - התקנת התוכנה על המחשב/שרת

דרוש Python 3.9+ מותקן.

```bash
# בתוך תיקיית הפרויקט:
python -m venv venv
source venv/bin/activate        # בווינדוס: venv\Scripts\activate

pip install -r requirements.txt
```

בנוסף חובה להתקין **ffmpeg** (נדרש כדי לאחד וידאו+אודיו ולהמיר ל-mp3):

- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **Mac (Homebrew):** `brew install ffmpeg`
- **Windows:** הורד מ-https://ffmpeg.org/download.html והוסף את התיקייה של ffmpeg.exe ל-PATH

## שלב 3 - הגדרת הטוקן

1. צור קובץ בשם `.env` בתיקיית הפרויקט (אפשר להעתיק את `.env.example`).
2. הכנס לתוכו את הטוקן:

```
BOT_TOKEN=123456789:AAExampleTokenExampleTokenExample
```

## שלב 4 - הרצה

```bash
python bot.py
```

אם הכל תקין, תראה בטרמינל `Bot starting (polling)...`. עכשיו אפשר לפתוח את הבוט בטלגרם, לשלוח `/start`, ולשלוח קישור לניסיון.

## הערות חשובות

- **מגבלת גודל קובץ:** ל-Telegram Bot API הרגיל יש מגבלה של כ-50MB להעלאת קובץ ע"י בוט. אם התוכן גדול מזה, הבוט ידווח על כך. כדי לעקוף את המגבלה אפשר להריץ [Local Bot API Server](https://github.com/tdlib/telegram-bot-api) (מגביל עד 2GB) - זה מורכב יותר להקמה, ספר לי אם תרצה שאעזור בזה.
- **אינסטגרם:** תוכן פרטי (סטוריז, פרופילים פרטיים) דורש קובץ cookies של חשבון מחובר. אפשר לייצא אחד באמצעות תוסף כרום כמו "Get cookies.txt", לשמור כ-`cookies.txt`, ולהוסיף בקובץ `.env`:
  ```
  INSTAGRAM_COOKIES=cookies.txt
  ```
- **אתרים נתמכים:** yt-dlp תומך באלפי אתרים (יוטיוב, אינסטגרם, טיקטוק, טוויטר/X, פייסבוק, ועוד). ברוב האתרים תוכן ציבורי יעבוד "מהקופסה".
- **הרצה 24/7:** כדי שהבוט ירוץ תמיד (גם כשהמחשב שלך כבוי), כדאי להעלות אותו לשרת VPS קטן (למשל DigitalOcean/Hetzner/Oracle Cloud Free Tier) ולהריץ אותו שם עם `systemd` או `pm2`, או להשתמש ב-Docker. ספר לי אם תרצה שאכין לך את זה.

## פריסה על Render (הרצה 24/7 בענן)

הפרויקט כולל `Dockerfile` ו-`render.yaml` שמוכנים לפריסה על [Render](https://render.com).

**חשוב לדעת לפני שמתחילים:** ל-Render יש תוכנית חינמית, אבל היא **לא** תומכת ב-Background Worker (סוג השירות המתאים לבוט הזה, שכל הזמן "מאזין" לטלגרם ולא מריץ שרת HTTP). צוות התמיכה של Render מאשר זאת במפורש. המחיר המינימלי לבוט כזה הוא תוכנית **Starter, כ-7$/חודש**.

שלבים:

1. **העלה את קוד הפרויקט לריפו ב-GitHub** (Render נפרס מ-git, לא מ-zip):
   ```bash
   cd telegram-downloader-bot
   git init
   git add .
   git commit -m "init"
   # צור ריפו חדש וריק בגיטהאב, ואז:
   git remote add origin <כתובת-הריפו-שלך>
   git push -u origin main
   ```
   ⚠️ הקפד ש-`.env` (אם יצרת) **לא** נכנס לריפו - הוא לא צריך, כי הטוקן יוגדר בדשבורד של Render (ראה שלב 3).

2. בדשבורד של Render: **New +** → **Blueprint** → חבר את הריפו. Render יזהה אוטומטית את `render.yaml` ויציע ליצור שירות מסוג Background Worker בשמו `telegram-downloader-bot`.

3. בעת האישור, Render יבקש למלא את `BOT_TOKEN` (מוגדר ב-render.yaml כ-`sync: false`, כלומר סודי וידני) - הדבק כאן את הטוקן מ-BotFather.

4. אשר ולחץ Deploy. Render יבנה את ה-Docker image (כולל ffmpeg) ויריץ את הבוט. אפשר לעקוב אחרי הלוגים בדשבורד כדי לראות `Bot starting (polling)...`.

**עדכון גרסה בעתיד:** כל `git push` לריפו יגרום ל-Render לבנות ולפרוס מחדש אוטומטית.

## מבנה הפרויקט

```
telegram-downloader-bot/
├── bot.py              # קוד הבוט
├── requirements.txt    # תלויות Python
├── .env.example        # תבנית להגדרות (טוקן וכו') - להרצה מקומית
├── Dockerfile          # אימג' לפריסה (מתקין ffmpeg אוטומטית) - ל-Render/Docker
├── render.yaml         # הגדרת Blueprint לפריסה על Render
└── README.md           # הקובץ הזה
```
