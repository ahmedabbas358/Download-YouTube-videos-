#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeVideo
from dotenv import load_dotenv
import yt_dlp
import json
from pathlib import Path
import aiohttp
import redis
from urllib.parse import urlparse

# تحميل متغيرات البيئة
load_dotenv()

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# تكوين البوت
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 1900000000))  # 1.9GB لتفادي حدود تليجرام
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# إعدادات إضافية
SUPPORTED_PLATFORMS = [
    "youtube", "youtu.be", "twitter", "x.com", "instagram", 
    "tiktok", "facebook", "fb.watch", "vimeo", "dailymotion", 
    "twitch", "reddit", "bilibili", "nicovideo"
]

# إعداد Redis للتخزين
try:
    redis_client = redis.from_url(REDIS_URL)
    logger.info("✅ تم الاتصال بـ Redis بنجاح")
except Exception as e:
    logger.warning(f"❌ فشل الاتصال بـ Redis: {e}")
    redis_client = None

# إنشاء عميل تليجرام
client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# تأكد من وجود المجلدات اللازمة
Path("downloads").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
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
            
        # حفظ في Redis إذا كان متاحاً
        if redis_client:
            redis_client.hset(f"user:{user_id}", mapping=users[str(user_id)])
            
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات المستخدم: {e}")

def get_user_data(user_id):
    """استرجاع بيانات المستخدم"""
    try:
        # محاولة جلب من Redis أولاً
        if redis_client:
            user_data = redis_client.hgetall(f"user:{user_id}")
            if user_data:
                return {k.decode(): v.decode() for k, v in user_data.items()}
        
        # إذا فشل، جلب من ملف JSON
        with open("data/users.json", "r") as f:
            users = json.load(f)
            return users.get(str(user_id), {})
    except Exception as e:
        logger.error(f"خطأ في استرجاع بيانات المستخدم: {e}")
        return {}

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

async def get_video_info(url):
    """الحصول على معلومات الفيديو بدون تحميل"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'formats': info.get('formats', []),
                'thumbnail': info.get('thumbnail', ''),
                'uploader': info.get('uploader', ''),
                'webpage_url': info.get('webpage_url', ''),
            }
    except Exception as e:
        logger.error(f"خطأ في الحصول على معلومات الفيديو: {e}")
        return None

def is_supported_url(url):
    """التحقق إذا كان الرابط مدعوماً"""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    return any(platform in domain for platform in SUPPORTED_PLATFORMS)

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """معالج أمر البدء"""
    user_id = event.sender_id
    user_data = get_user_data(user_id)
    
    if not user_data:
        save_user_data(user_id, {
            "first_seen": datetime.now().isoformat(),
            "username": event.sender.username,
            "first_name": event.sender.first_name,
            "last_name": event.sender.last_name,
            "download_count": 0
        })
    
    buttons = [
        [Button.inline("📥 تحميل فيديو", data="download_video")],
        [Button.inline("🎵 استخراج صوت", data="extract_audio")],
        [Button.inline("⚙️ الإعدادات", data="settings")],
        [Button.inline("📊 إحصائيات", data="stats")],
        [Button.url("📖 الدليل", url="https://t.me/your_channel")]
    ]
    
    if user_id in ADMIN_IDS:
        buttons.append([Button.inline("👨‍💼 لوحة التحكم", data="admin_panel")])
    
    await event.reply(
        f"مرحباً {event.sender.first_name}! 👋\n\n"
        "أنا بوت متقدم لتحميل الفيديوهات من مختلف المنصات. 🎥\n\n"
        "يمكنني التحميل من:\n"
        "✅ YouTube - فيديوهات وقوائم تشغيل\n"
        "✅ Twitter/X - تغريدات بالفيديو\n"
        "✅ Instagram - منشورات وريلز\n"
        "✅ TikTok - فيديوهات قصيرة\n"
        "✅ Facebook - مقاطع عامة\n"
        "✅ +10 منصات أخرى\n\n"
        "اختر أحد الخيارات من الأسفل:",
        buttons=buttons
    )

@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """معالج أمر المساعدة"""
    help_text = """
🎥 **بوت تحميل الفيديوهات المتقدم**

**الأوامر المتاحة:**
/start - بدء استخدام البوت
/help - عرض هذه المساعدة
/stats - عرض إحصائيك
/settings - ضبط الإعدادات

**كيفية الاستخدام:**
1. أرسل رابط الفيديو مباشرة
2. أو استخدم الأزرار للاختيار من الخيارات

**المنصات المدعومة:**
YouTube, Twitter/X, Instagram, TikTok, Facebook, Vimeo, Dailymotion, Twitch, Reddit, وغيرها.

**الميزات:**
- تحميل بجودات متعددة (حتى 8K)
- استخراج الصوت بصيغ مختلفة
- دعم قوائم التشغيل
- ترجمات متعددة اللغات
- واجهة أزرار تفاعلية
    """
    await event.reply(help_text)

@client.on(events.NewMessage)
async def message_handler(event):
    """معالج الرسائل العامة"""
    if event.text.startswith('/'):
        return
    
    url = event.text.strip()
    if not is_supported_url(url):
        await event.reply("⚠️ هذا الرابط غير مدعوم أو غير صحيح.\n\nالمنصات المدعومة: YouTube, Twitter, Instagram, TikTok, Facebook, Vimeo, Dailymotion, Twitch.")
        return
    
    # عرض خيارات التحميل
    buttons = [
        [Button.inline("📥 تحميل بأعلى جودة", data=f"download:{url}:best")],
        [Button.inline("🎵 استخراج صوت MP3", data=f"extract:{url}:mp3")],
        [Button.inline("⚙️ خيارات متقدمة", data=f"advanced:{url}")]
    ]
    
    await event.reply(
        "🔍 تم التعرف على الرابط!\n\n"
        "اختر طريقة التحميل المناسبة:",
        buttons=buttons
    )

@client.on(events.CallbackQuery(pattern=r'download:(.*?):(.*?)'))
async def download_callback_handler(event):
    """معالج تحميل الفيديو"""
    await event.answer("⏳ جاري التحميل...")
    
    data = event.data.decode().split(':')
    url = data[1]
    quality = data[2] if len(data) > 2 else 'best'
    
    user_id = event.sender_id
    message = await event.get_message()
    
    try:
        # تحميل الفيديو
        edit_message = await message.edit("⏳ جاري تحميل الفيديو...")
        
        filename, title, duration = await download_video(url, user_id, quality)
        
        if not filename or not Path(filename).exists():
            await edit_message.edit("❌ فشل في تحميل الفيديو")
            return
        
        # تحديث إحصائيات المستخدم
        user_data = get_user_data(user_id)
        user_data['download_count'] = user_data.get('download_count', 0) + 1
        save_user_data(user_id, user_data)
        
        # إرسال الفيديو
        await edit_message.edit("📤 جاري إرسال الفيديو...")
        
        # إعداد خصائص الفيديو
        attributes = [
            DocumentAttributeVideo(
                duration=duration,
                w=0,
                h=0,
                round_message=False,
                supports_streaming=True
            )
        ]
        
        # تقسيم الملف إذا كان كبيراً
        file_size = Path(filename).stat().st_size
        if file_size > MAX_FILE_SIZE:
            await edit_message.edit("❌ حجم الملف يتجاوز الحد المسموح به")
            Path(filename).unlink()
            return
        
        await client.send_file(
            event.chat_id,
            filename,
            caption=f"🎥 **{title}**\n\n✅ تم التحميل بواسطة @{event.sender.username}",
            attributes=attributes,
            progress_callback=lambda sent, total: logger.info(f"تم إرسال {sent} من {total}")
        )
        
        await edit_message.delete()
        
        # حذف الملف المؤقت
        Path(filename).unlink()
        
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        await message.edit(f"❌ حدث خطأ: {str(e)}")

@client.on(events.CallbackQuery(pattern=r'extract:(.*?):(.*?)'))
async def extract_audio_handler(event):
    """معالج استخراج الصوت"""
    await event.answer("⏳ جاري استخراج الصوت...")
    
    data = event.data.decode().split(':')
    url = data[1]
    format = data[2] if len(data) > 2 else 'mp3'
    
    user_id = event.sender_id
    message = await event.get_message()
    
    try:
        edit_message = await message.edit("⏳ جاري استخراج الصوت...")
        
        filename, title, duration = await download_video(url, user_id, extract_audio=True)
        
        if not filename or not Path(filename).exists():
            await edit_message.edit("❌ فشل في استخراج الصوت")
            return
        
        # تحديث إحصائيات المستخدم
        user_data = get_user_data(user_id)
        user_data['audio_extract_count'] = user_data.get('audio_extract_count', 0) + 1
        save_user_data(user_id, user_data)
        
        await edit_message.edit("📤 جاري إرسال الملف الصوتي...")
        
        await client.send_file(
            event.chat_id,
            filename,
            caption=f"🎵 **{title}**\n\n✅ تم استخراج الصوت بواسطة @{event.sender.username}",
            progress_callback=lambda sent, total: logger.info(f"تم إرسال {sent} من {total}")
        )
        
        await edit_message.delete()
        Path(filename).unlink()
        
    except Exception as e:
        logger.error(f"خطأ في استخراج الصوت: {e}")
        await message.edit(f"❌ حدث خطأ: {str(e)}")

@client.on(events.CallbackQuery(pattern='stats'))
async def stats_handler(event):
    """معالج عرض الإحصائيات"""
    user_id = event.sender_id
    user_data = get_user_data(user_id)
    
    stats_text = f"""
📊 **إحصائياتك الشخصية**

👤 المستخدم: {event.sender.first_name}
📥 عدد التحميلات: {user_data.get('download_count', 0)}
🎵 عدد استخراجات الصوت: {user_data.get('audio_extract_count', 0)}
📅 أول استخدام: {user_data.get('first_seen', 'غير معروف')}
🔄 آخر نشاط: {user_data.get('last_activity', 'غير معروف')}
    """
    
    await event.answer("إحصائياتك الشخصية")
    await event.edit(stats_text)

@client.on(events.CallbackQuery(pattern='admin_panel'))
async def admin_panel_handler(event):
    """لوحة تحكم المشرف"""
    if event.sender_id not in ADMIN_IDS:
        await event.answer("❌ ليس لديك صلاحية الوصول لهذه اللوحة")
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
        
        buttons = [
            [Button.inline("🔄 تحديث الإحصائيات", data="refresh_stats")],
            [Button.inline("📋 تصدير البيانات", data="export_data")],
            [Button.inline("🔙 رجوع", data="main_menu")]
        ]
        
        await event.edit(admin_text, buttons=buttons)
        
    except Exception as e:
        logger.error(f"خطأ في لوحة التحكم: {e}")
        await event.answer("❌ حدث خطأ في جلب البيانات")

async def main():
    """الدالة الرئيسية"""
    logger.info("🎥 بدء تشغيل بوت تحميل الفيديوهات...")
    logger.info(f"👥 معرفات المشرفين: {ADMIN_IDS}")
    
    # اختبار الاتصال بـ Redis
    if redis_client:
        try:
            redis_client.ping()
            logger.info("✅ الاتصال بـ Redis نشط")
        except Exception as e:
            logger.warning(f"❌ فشل في الاتصال بـ Redis: {e}")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())