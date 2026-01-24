# 🎬 Siphon - Video Scraper & Telegram Bot

סולס (Siphon) הוא סקריפט עוצמתי (מבוסס Playwright) לחילוץ קישורי וידאו ישירים מאתרים, עם ממשק טלגרם נוח.

## 📋 תכונות

- ✅ **Sniffing חכם**: האזנה לתעבורת הרשת בדומה ל-IDM.
- ✅ **ממשק טלגרם**: שליטה נוחה דרך בוט עם שני מצבים.
- ✅ **Single Download**: הורדה ישירה של הוידאו מהלינק ששלחת.
- ✅ **Full Page Scrape**: סריקת עמוד שלם וכל תתי-הלינקים שלו וייצוא ל-CSV.
- ✅ **תמיכה רחבה**: MP4, WebM, MOV, AVI, MKV, M3U8.
- ✅ **סינון חכם**: סינון לפי מילות מפתח (אופציונלי).

## 🚀 התקנה

1. התקן את התלויות:
```bash
pip install -r requirements.txt
```

2. התקן את הדפדפנים של Playwright:
```bash
playwright install chromium
```

## ⚙️ הגדרת הבוט (Telegram)

1. צור בוט דרך [@BotFather](https://t.me/BotFather) וקבל Token.
2. השג `API_ID` ו-`API_HASH` מ-[my.telegram.org](https://my.telegram.org).
3. ערוך את קובץ `.env` והכנס את הפרטים:
```env
BOT_TOKEN=your_token_here
API_ID=your_api_id
API_HASH=your_api_hash
ADMIN_IDS=123456789,987654321  # ליסט של מנהלים
USER_IDS=111111111             # ליסט של משתמשים מורשים
```

## 🤖 שימוש בבוט

1. הרץ את הבוט:
```bash
run_bot.bat
```
או:
```bash
python bot.py
```

2. בטלגרם, לחץ על `/start` ובחר את המצב הרצוי:
   - **📥 Single Download**: שלח לינק לעמוד עם וידאו, והבוט ישלח לך את הקובץ.
   - **📂 Full Page Scrape**: שלח לינק לעמוד ראשי, והבוט יסרוק את כל האתר וישלח קובץ CSV עם כל הלינקים שנמצאו.

## 📊 פורמט הפלט (במצב Full Scrape)

קובץ CSV עם העמודות הבאות:
- **title**: כותרת הסרטון
- **url**: קישור ישיר להורדה
- **size**: גודל הקובץ (MB)
- **page_url**: קישור לעמוד המקורי
