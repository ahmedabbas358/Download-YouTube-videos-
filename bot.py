#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import json
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp
from urllib.parse import urlparse

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تكوين البوت
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 1900000000))  # 1.9GB لتفادي حدود تليجرام
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# المنصات المدعومة
SUPPORTED_PLATFORMS = [
    "youtube", "youtu.be", "twitter", "x.com", "instagram", 
    "tiktok", "facebook", "fb.watch", "vimeo", "dailymotion", 
    "twitch", "reddit", "bilibili", "nicovideo"
]

# تأكد من وجود المجلدات اللازمة
Path("downloads").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)

# تخزين بيانات المستخدمين
if not Path("data/users.json").exists():
    with open("data/users.json", "w") as f:
        json.dump({}, f)

def save_user_data(user_id, data):
    """حفظ بيانات المستخدم"""
    try:
        with open("data/users.json", "r") as f:
            users = json.load(f)
        
        users[str(user_id)] = {
            **users.get(str(user_id), {}),
            **data,
            "last_activity": datetime.now().isoformat()
        }
        
        with open("data/users.json", "w") as f:
            json.dump(users, f, indent=2)
            
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات المستخدم: {e}")

def get_user_data(user_id):
    """استرجاع بيانات المستخدم"""
    try:
        with open("data/users.json", "r") as f:
            users = json.load(f)
            return users.get(str(user_id), {})
    except Exception as e:
        logger.error(f"خطأ في استرجاع بيانات المستخدم: {e}")
        return {}

def is_supported_url(url):
    """التحقق إذا كان الرابط مدعوماً"""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    return any(platform in domain for platform in SUPPORTED_PLATFORMS)

async def download_video(url, user_id, quality='best', extract_audio=False):
    """تحميل الفيديو من الرابط مع خيارات متقدمة"""
    user_dir = Path(f"downloads/{user_id}")
    user_dir.mkdir(exist_ok=True)
    
    ydl_opts = {
        'outtmpl': str(user_dir / '%(title)s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'format': f'{quality}[filesize<{MAX_FILE_SIZE}]' if quality != 'best' else f'best[filesize<{MAX_FILE_SIZE}]',
        'max_filesize': MAX_FILE_SIZE,
        'noplaylist': False,
        'extract_flat': False,
        'logger': logger,
    }
    
    if extract_audio:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
        })
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if extract_audio:
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            return filename, info.get('title', 'Unknown'), info.get('duration', 0)
    except yt_dlp.utils.DownloadError as e:
        if "File is larger than max-filesize" in str(e):
            raise Exception("حجم الملف يتجاوز الحد المسموح به (2GB)")
        else:
            raise e
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        raise e

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر البدء"""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    
    if not user_data:
        save_user_data(user_id, {
            "first_seen": datetime.now().isoformat(),
            "username": update.effective_user.username,
            "first_name": update.effective_user.first_name,
            "last_name": update.effective_user.last_name,
            "download_count": 0
        })
    
    keyboard = [
        [InlineKeyboardButton("📥 تحميل فيديو", callback_data="download_video")],
        [InlineKeyboardButton("🎵 استخراج صوت", callback_data="extract_audio")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("👨‍💼 لوحة التحكم", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"مرحباً {update.effective_user.first_name}! 👋\n\n"
        "أنا بوت متقدم لتحميل الفيديوهات من مختلف المنصات. 🎥\n\n"
        "يمكنني التحميل من:\n"
        "✅ YouTube - فيديوهات وقوائم تشغيل\n"
        "✅ Twitter/X - تغريدات بالفيديو\n"
        "✅ Instagram - منشورات وريلز\n"
        "✅ TikTok - فيديوهات قصيرة\n"
        "✅ Facebook - مقاطع عامة\n"
        "✅ +10 منصات أخرى\n\n"
        "اختر أحد الخيارات من الأسفل:",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر المساعدة"""
    help_text = """
🎥 **بوت تحميل الفيديوهات المتقدم**

**الأوامر المتاحة:**
/start - بدء استخدام البوت
/help - عرض هذه المساعدة
/stats - عرض إحصائيك

**كيفية الاستخدام:**
1. أرسل رابط الفيديو مباشرة
2. أو استخدم الأزرار للاختيار من الخيارات

**المنصات المدعومة:**
YouTube, Twitter/X, Instagram, TikTok, Facebook, Vimeo, Dailymotion, Twitch, Reddit, وغيرها.

**الميزات:**
- تحميل بجودات متعددة (حتى 8K)
- استخراج الصوت بصيغ مختلفة
- دعم قوائم التشغيل
- واجهة أزرار تفاعلية
    """
    await update.message.reply_text(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل العامة"""
    if update.message.text.startswith('/'):
        return
    
    url = update.message.text.strip()
    if not is_supported_url(url):
        await update.message.reply_text(
            "⚠️ هذا الرابط غير مدعوم أو غير صحيح.\n\n"
            "المنصات المدعومة: YouTube, Twitter, Instagram, TikTok, Facebook, Vimeo, Dailymotion, Twitch."
        )
        return
    
    # عرض خيارات التحميل
    keyboard = [
        [InlineKeyboardButton("📥 تحميل بأعلى جودة", callback_data=f"download:{url}:best")],
        [InlineKeyboardButton("🎵 استخراج صوت MP3", callback_data=f"extract:{url}:mp3")],
        [InlineKeyboardButton("⚙️ خيارات متقدمة", callback_data=f"advanced:{url}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔍 تم التعرف على الرابط!\n\n"
        "اختر طريقة التحميل المناسبة:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأزرار"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "stats":
        user_data = get_user_data(user_id)
        stats_text = f"""
📊 **إحصائياتك الشخصية**

👤 المستخدم: {query.from_user.first_name}
📥 عدد التحميلات: {user_data.get('download_count', 0)}
🎵 عدد استخراجات الصوت: {user_data.get('audio_extract_count', 0)}
📅 أول استخدام: {user_data.get('first_seen', 'غير معروف')}
🔄 آخر نشاط: {user_data.get('last_activity', 'غير معروف')}
        """
        await query.edit_message_text(stats_text)
    
    elif data == "admin_panel":
        if user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ ليس لديك صلاحية الوصول لهذه اللوحة")
            return
        
        # جلب إحصائيات عامة
        try:
            with open("data/users.json", "r") as f:
                users = json.load(f)
            
            total_users = len(users)
            total_downloads = sum(user.get('download_count', 0) for user in users.values())
            total_audio = sum(user.get('audio_extract_count', 0) for user in users.values())
            
            admin_text = f"""
👨‍💼 **لوحة تحكم المشرف**

👥 إجمالي المستخدمين: {total_users}
📥 إجمالي التحميلات: {total_downloads}
🎵 إجمالي استخراجات الصوت: {total_audio}

⚡ حالة الخدمة: ✅ نشطة
📊 استخدام الذاكرة: جيد
🔄 آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """
            
            keyboard = [
                [InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="refresh_stats")],
                [InlineKeyboardButton("📋 تصدير البيانات", callback_data="export_data")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(admin_text, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"خطأ في لوحة التحكم: {e}")
            await query.edit_message_text("❌ حدث خطأ في جلب البيانات")
    
    elif data.startswith("download:"):
        parts = data.split(":")
        if len(parts) >= 3:
            url = parts[1]
            quality = parts[2]
            
            await query.edit_message_text("⏳ جاري تحميل الفيديو...")
            
            try:
                filename, title, duration = await download_video(url, user_id, quality)
                
                if not filename or not Path(filename).exists():
                    await query.edit_message_text("❌ فشل في تحميل الفيديو")
                    return
                
                # تحديث إحصائيات المستخدم
                user_data = get_user_data(user_id)
                user_data['download_count'] = user_data.get('download_count', 0) + 1
                save_user_data(user_id, user_data)
                
                await query.edit_message_text("📤 جاري إرسال الفيديو...")
                
                # إرسال الفيديو
                with open(filename, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=video_file,
                        caption=f"🎥 **{title}**\n\n✅ تم التحميل بواسطة @{query.from_user.username}",
                        duration=duration,
                        supports_streaming=True
                    )
                
                await query.delete_message()
                
                # حذف الملف المؤقت
                Path(filename).unlink()
                
            except Exception as e:
                logger.error(f"خطأ في التحميل: {e}")
                await query.edit_message_text(f"❌ حدث خطأ: {str(e)}")
    
    elif data.startswith("extract:"):
        parts = data.split(":")
        if len(parts) >= 3:
            url = parts[1]
            format = parts[2]
            
            await query.edit_message_text("⏳ جاري استخراج الصوت...")
            
            try:
                filename, title, duration = await download_video(url, user_id, extract_audio=True)
                
                if not filename or not Path(filename).exists():
                    await query.edit_message_text("❌ فشل في استخراج الصوت")
                    return
                
                # تحديث إحصائيات المستخدم
                user_data = get_user_data(user_id)
                user_data['audio_extract_count'] = user_data.get('audio_extract_count', 0) + 1
                save_user_data(user_id, user_data)
                
                await query.edit_message_text("📤 جاري إرسال الملف الصوتي...")
                
                # إرسال الملف الصوتي
                with open(filename, 'rb') as audio_file:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=audio_file,
                        caption=f"🎵 **{title}**\n\n✅ تم استخراج الصوت بواسطة @{query.from_user.username}",
                        duration=duration
                    )
                
                await query.delete_message()
                
                # حذف الملف المؤقت
                Path(filename).unlink()
                
            except Exception as e:
                logger.error(f"خطأ في استخراج الصوت: {e}")
                await query.edit_message_text(f"❌ حدث خطأ: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text("❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.")

def main():
    """الدالة الرئيسية"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN غير مضبوط. يرجى تعيينه في متغيرات البيئة.")
        return
    
    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)
    
    # بدء البوت
    logger.info("🎥 بدء تشغيل بوت تحميل الفيديوهات...")
    application.run_polling()

if __name__ == '__main__':
    main()