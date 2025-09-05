"""
Enhanced Telegram Video Downloader Bot - الإصدار المحسن
نسخة محسنة ومتطورة مع إصلاح المشاكل الرئيسية
"""

import os
import asyncio
import logging
import sqlite3
import tempfile
import time
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse

# Telegram Bot imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

# Download engines
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

# Configuration
class BotConfig:
    """تكوين البوت"""
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN', '')
        admin_str = os.getenv('ADMIN_IDS', '')
        self.admin_ids = [int(x) for x in admin_str.split(',') if x.strip()]
        self.max_file_size = int(os.getenv('MAX_FILE_SIZE', 2000))  # MB
        self.download_path = os.getenv('DOWNLOAD_PATH', './downloads')
        self.database_path = os.getenv('DATABASE_PATH', './bot.db')
        self.max_concurrent_downloads = int(os.getenv('MAX_CONCURRENT', 5))
        self.rate_limit_per_user = int(os.getenv('RATE_LIMIT', 10))  # per hour
        self.enable_playlist_download = os.getenv('ENABLE_PLAYLIST', 'true').lower() == 'true'
        self.supported_platforms = [
            'youtube.com', 'youtu.be', 'twitter.com', 'x.com',
            'instagram.com', 'facebook.com', 'tiktok.com',
            'vimeo.com', 'dailymotion.com', 'twitch.tv'
        ]

# إعداد قاعدة البيانات
class DatabaseManager:
    """مدير قاعدة البيانات"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """إنشاء جداول قاعدة البيانات"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    joined_date TEXT,
                    downloads_count INTEGER DEFAULT 0,
                    is_banned BOOLEAN DEFAULT FALSE
                );
                
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    url TEXT,
                    title TEXT,
                    platform TEXT,
                    file_size INTEGER,
                    download_time TEXT,
                    status TEXT,
                    error_msg TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE TABLE IF NOT EXISTS rate_limits (
                    user_id INTEGER PRIMARY KEY,
                    hour_start INTEGER,
                    requests_count INTEGER DEFAULT 0
                );
            """)

    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        """إضافة مستخدم جديد"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users 
                (user_id, username, first_name, joined_date)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, first_name, datetime.now().isoformat()))

    def check_rate_limit(self, user_id: int, limit: int) -> bool:
        """فحص حد المعدل للمستخدم"""
        current_hour = int(time.time() // 3600)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT hour_start, requests_count 
                FROM rate_limits WHERE user_id = ?
            """, (user_id,))

            result = cursor.fetchone()

            if not result:
                conn.execute("""
                    INSERT INTO rate_limits (user_id, hour_start, requests_count)
                    VALUES (?, ?, 1)
                """, (user_id, current_hour))
                return True

            hour_start, requests_count = result

            if hour_start != current_hour:
                conn.execute("""
                    UPDATE rate_limits 
                    SET hour_start = ?, requests_count = 1
                    WHERE user_id = ?
                """, (current_hour, user_id))
                return True

            if requests_count >= limit:
                return False

            conn.execute("""
                UPDATE rate_limits 
                SET requests_count = requests_count + 1
                WHERE user_id = ?
            """, (user_id,))
            return True

    def log_download(self, user_id: int, url: str, title: str, 
                     platform: str, file_size: int, status: str, error_msg: str = None):
        """تسجيل عملية التحميل"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO downloads 
                (user_id, url, title, platform, file_size, download_time, status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, url, title, platform, file_size, 
                  datetime.now().isoformat(), status, error_msg))

            if status == 'completed':
                conn.execute("""
                    UPDATE users SET downloads_count = downloads_count + 1
                    WHERE user_id = ?
                """, (user_id,))

# محرك التحميل المتقدم
class EnhancedDownloader:
    """محرك التحميل المتقدم مع دعم منصات متعددة"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=config.max_concurrent_downloads)

        # إعدادات yt-dlp محدثة
        self.ydl_opts_base = {
            'format': 'best[height<=720]',
            'outtmpl': os.path.join(config.download_path, '%(title).50s.%(ext)s'),
            'writethumbnail': True,
            'ignoreerrors': False,
            'no_warnings': False,
            'quiet': True,
            'extract_flat': False,
        }

    async def get_video_info(self, url: str) -> Optional[Dict]:
        """الحصول على معلومات الفيديو"""
        try:
            loop = asyncio.get_event_loop()

            def extract_info():
                ydl_opts = {
                    **self.ydl_opts_base,
                    'skip_download': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await loop.run_in_executor(self.executor, extract_info)
            return info

        except Exception as e:
            logging.error(f"Error extracting info: {e}")
            return None

    async def get_available_formats(self, url: str) -> List[Dict]:
        """الحصول على الصيغ المتاحة للتحميل"""
        info = await self.get_video_info(url)
        if not info:
            return []

        formats = []
        for fmt in info.get('formats', []):
            if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                quality = fmt.get('height', 0)
                size_mb = fmt.get('filesize', 0) / (1024 * 1024) if fmt.get('filesize') else 0

                formats.append({
                    'format_id': fmt['format_id'],
                    'ext': fmt['ext'],
                    'quality': f"{quality}p" if quality else "Unknown",
                    'size_mb': round(size_mb, 1),
                    'note': fmt.get('format_note', ''),
                    'filesize': fmt.get('filesize', 0)
                })

        formats.sort(key=lambda x: int(x['quality'].replace('p', '')) if x['quality'] != 'Unknown' else 0, reverse=True)
        return formats

    async def download_video(self, url: str, format_id: str = None, 
                           progress_callback=None) -> Tuple[bool, str, Dict]:
        """تحميل الفيديو مع إظهار التقدم"""
        try:
            ydl_opts = {**self.ydl_opts_base}
            if format_id:
                ydl_opts['format'] = format_id

            loop = asyncio.get_event_loop()

            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info_dict)
                    return True, filename, info_dict

            success, filename, info = await loop.run_in_executor(self.executor, download)
            return success, filename, info

        except Exception as e:
            logging.error(f"Download error: {e}")
            return False, str(e), {}

    async def download_audio_only(self, url: str, progress_callback=None) -> Tuple[bool, str, Dict]:
        """تحميل الصوت فقط"""
        try:
            ydl_opts = {
                **self.ydl_opts_base,
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }

            loop = asyncio.get_event_loop()

            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info_dict).replace('.webm', '.mp3').replace('.m4a', '.mp3')
                    return True, filename, info_dict

            success, filename, info = await loop.run_in_executor(self.executor, download)
            return success, filename, info

        except Exception as e:
            logging.error(f"Audio download error: {e}")
            return False, str(e), {}

    async def extract_subtitles(self, url: str, languages: List[str] = None) -> Dict[str, str]:
        """استخراج ملفات الترجمة - الإصدار المحسن"""
        if languages is None:
            languages = ['ar', 'en']

        try:
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': languages,
                'subtitlesformat': 'srt',
                'skip_download': True,
                'outtmpl': os.path.join(self.config.download_path, 'subs', '%(title).50s.%(ext)s'),
                'quiet': True,
            }

            loop = asyncio.get_event_loop()

            def extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    
                    subtitle_files = {}
                    if 'requested_subtitles' in info and info['requested_subtitles']:
                        for lang, sub_info in info['requested_subtitles'].items():
                            if sub_info and os.path.exists(sub_info.get('filepath', '')):
                                subtitle_files[lang] = sub_info['filepath']
                    
                    # إذا لم يتم العثور على ترجمات مطلوبة، ابحث عن أي ترجمات متاحة
                    if not subtitle_files and 'subtitles' in info:
                        for lang, subs in info['subtitles'].items():
                            if subs and lang in languages:
                                # حاول إنشاء مسار ملف الترجمة
                                base_path = os.path.join(self.config.download_path, 'subs', f"{info['title']}.{lang}.srt")
                                if os.path.exists(base_path):
                                    subtitle_files[lang] = base_path
                    
                    return subtitle_files

            subtitle_files = await loop.run_in_executor(self.executor, extract)
            return subtitle_files

        except Exception as e:
            logging.error(f"Subtitle extraction error: {e}")
            return {}

class TelegramBot:
    """البوت الرئيسي مع واجهة متطورة"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.db = DatabaseManager(config.database_path)
        self.downloader = EnhancedDownloader(config)

        os.makedirs(config.download_path, exist_ok=True)
        os.makedirs(os.path.join(config.download_path, 'subs'), exist_ok=True)

        self.user_states = {}

    def is_admin(self, user_id: int) -> bool:
        """فحص صلاحيات الإدارة"""
        return user_id in self.config.admin_ids

    def is_supported_platform(self, url: str) -> bool:
        """فحص دعم المنصة"""
        try:
            domain = urlparse(url).netloc.lower()
            return any(platform in domain for platform in self.config.supported_platforms)
        except:
            return False

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر البداية"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)

        welcome_text = f"""
🎥 <b>مرحباً {user.first_name}!</b>

🚀 <b>بوت التحميل المتطور</b> - النسخة المحسنة

<b>✅ المميزات:</b>
• 🎬 تحميل من منصات متعددة
• 📋 دعم قوائم التشغيل
• 🎭 ترجمات متعددة
• 🎯 جودات مختلفة
• ⚡ تحميل سريع

<b>🔥 طريقة الاستخدام:</b>
1️⃣ أرسل رابط الفيديو
2️⃣ اختر الجودة
3️⃣ احصل على الملف!
        """

        keyboard = [
            [InlineKeyboardButton("📋 المساعدة", callback_data="help"),
             InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر المساعدة"""
        help_text = """
📚 <b>دليل الاستخدام</b>

<b>🎯 المنصات المدعومة:</b>
• YouTube
• Twitter/X 
• Instagram
• TikTok 
• Facebook
• وغيرها...

<b>📋 طريقة الاستخدام:</b>
• أرسل رابط الفيديو
• اختر نوع التحميل (فيديو/صوت/ترجمة)
• انتظر حتى يكتمل التحميل
• استلم الملف

<b>🔧 الأوامر:</b>
/start - بدء البوت
/help - المساعدة  
/stats - إحصائياتك
/cancel - إلغاء العملية
        """

        keyboard = [
            [InlineKeyboardButton("🔙 العودة", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة الروابط المرسلة"""
        user_id = update.effective_user.id
        url = update.message.text.strip()

        if not self.db.check_rate_limit(user_id, self.config.rate_limit_per_user):
            await update.message.reply_text(
                "⏰ <b>تم تجاوز الحد المسموح!</b>\n\n"
                f"يمكنك تحميل حتى {self.config.rate_limit_per_user} ملفات في الساعة.",
                parse_mode=ParseMode.HTML
            )
            return

        if not self.is_supported_platform(url):
            await update.message.reply_text(
                "❌ <b>منصة غير مدعومة</b>\n\n"
                "المنصات المدعومة:\n" + 
                "\n".join(f"• {platform}" for platform in self.config.supported_platforms),
                parse_mode=ParseMode.HTML
            )
            return

        loading_msg = await update.message.reply_text(
            "🔍 <b>جاري تحليل الرابط...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            info = await self.downloader.get_video_info(url)

            if not info:
                await loading_msg.edit_text(
                    "❌ <b>خطأ في تحليل الرابط</b>",
                    parse_mode=ParseMode.HTML
                )
                return

            if 'entries' in info and len(info['entries']) > 1:
                await self.handle_playlist(update, info, loading_msg)
                return

            await self.show_video_options(update, url, info, loading_msg)

        except Exception as e:
            logging.error(f"Error handling URL: {e}")
            await loading_msg.edit_text(
                "❌ <b>حدث خطأ أثناء معالجة الرابط</b>",
                parse_mode=ParseMode.HTML
            )

    async def show_video_options(self, update: Update, url: str, info: Dict, loading_msg):
        """عرض خيارات الفيديو"""
        title = info.get('title', 'Unknown Title')[:50]
        duration = info.get('duration', 0)
        duration_str = f"{duration//60}:{duration%60:02d}" if duration else "غير محدد"

        info_text = f"""
🎬 <b>{title}</b>

⏱️ <b>المدة:</b> {duration_str}
🌐 <b>المنصة:</b> {urlparse(url).netloc}

<b>اختر طريقة التحميل:</b>
        """

        keyboard = [
            [InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"video_{hash(url)}")],
            [InlineKeyboardButton("🎵 صوت فقط", callback_data=f"audio_{hash(url)}")],
            [InlineKeyboardButton("📝 ترجمات فقط", callback_data=f"subs_{hash(url)}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        self.user_states[update.effective_user.id] = {
            'url': url,
            'info': info
        }

        await loading_msg.edit_text(
            info_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج الأزرار التفاعلية"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        data = query.data

        if data == "help":
            await self.show_help_callback(query)
        elif data == "stats":
            await self.show_stats_callback(query)
        elif data == "back_main":
            await self.back_to_main(query)
        elif data == "cancel":
            await self.cancel_operation(query)
        elif data.startswith("video_"):
            await self.download_video_callback(query)
        elif data.startswith("audio_"):
            await self.download_audio_callback(query)
        elif data.startswith("subs_"):
            await self.download_subtitles_callback(query)

    async def download_video_callback(self, query):
        """معالجة تحميل الفيديو"""
        user_id = query.from_user.id

        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return

        user_data = self.user_states[user_id]
        url = user_data['url']

        progress_msg = await query.edit_message_text(
            "🚀 <b>بدء التحميل...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            success, result, info = await self.downloader.download_video(url)

            if success:
                file_size = os.path.getsize(result) if os.path.exists(result) else 0
                self.db.log_download(
                    user_id, url, info.get('title', 'Unknown'),
                    urlparse(url).netloc, file_size, 'completed'
                )

                if file_size < self.config.max_file_size * 1024 * 1024:
                    with open(result, 'rb') as video_file:
                        await query.message.reply_document(
                            video_file,
                            caption=f"🎬 {info.get('title', 'فيديو')}"
                        )
                    await progress_msg.edit_text(
                        "✅ <b>تم التحميل بنجاح!</b>",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await progress_msg.edit_text(
                        f"✅ <b>تم التحميل بنجاح!</b>\n\n"
                        f"⚠️ الملف كبير جداً للإرسال ({file_size/(1024*1024):.1f} MB)",
                        parse_mode=ParseMode.HTML
                    )
            else:
                self.db.log_download(
                    user_id, url, user_data['info'].get('title', 'Unknown'),
                    urlparse(url).netloc, 0, 'failed', result
                )
                await progress_msg.edit_text(
                    f"❌ <b>فشل التحميل</b>\n\n{result}",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            logging.error(f"Download error: {e}")
            await progress_msg.edit_text(
                f"❌ <b>خطأ في التحميل</b>\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )

        if user_id in self.user_states:
            del self.user_states[user_id]

    async def download_audio_callback(self, query):
        """معالجة تحميل الصوت فقط"""
        user_id = query.from_user.id

        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return

        user_data = self.user_states[user_id]
        url = user_data['url']

        progress_msg = await query.edit_message_text(
            "🎵 <b>جاري تحميل الصوت...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            success, result, info = await self.downloader.download_audio_only(url)

            if success:
                file_size = os.path.getsize(result) if os.path.exists(result) else 0
                self.db.log_download(
                    user_id, url, info.get('title', 'Unknown'),
                    urlparse(url).netloc, file_size, 'completed'
                )

                if file_size < self.config.max_file_size * 1024 * 1024:
                    with open(result, 'rb') as audio_file:
                        await query.message.reply_document(
                            audio_file,
                            caption=f"🎵 {info.get('title', 'صوت')}"
                        )
                    await progress_msg.edit_text(
                        "✅ <b>تم تحميل الصوت بنجاح!</b>",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await progress_msg.edit_text(
                        f"✅ <b>تم التحميل بنجاح!</b>\n\n"
                        f"⚠️ الملف كبير جداً للإرسال ({file_size/(1024*1024):.1f} MB)",
                        parse_mode=ParseMode.HTML
                    )
            else:
                self.db.log_download(
                    user_id, url, user_data['info'].get('title', 'Unknown'),
                    urlparse(url).netloc, 0, 'failed', result
                )
                await progress_msg.edit_text(
                    f"❌ <b>فشل تحميل الصوت</b>\n\n{result}",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            logging.error(f"Audio download error: {e}")
            await progress_msg.edit_text(
                f"❌ <b>خطأ في تحميل الصوت</b>\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )

        if user_id in self.user_states:
            del self.user_states[user_id]

    async def download_subtitles_callback(self, query):
        """معالجة تحميل الترجمات فقط"""
        user_id = query.from_user.id

        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return

        user_data = self.user_states[user_id]
        url = user_data['url']

        progress_msg = await query.edit_message_text(
            "📝 <b>جاري استخراج الترجمات...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            subtitle_files = await self.downloader.extract_subtitles(url, ['ar', 'en'])

            if subtitle_files:
                await progress_msg.edit_text(
                    f"✅ <b>تم استخراج {len(subtitle_files)} ملف ترجمة</b>",
                    parse_mode=ParseMode.HTML
                )

                for lang, filepath in subtitle_files.items():
                    if os.path.exists(filepath):
                        with open(filepath, 'rb') as sub_file:
                            await query.message.reply_document(
                                sub_file,
                                caption=f"📝 ترجمة {lang.upper()}"
                            )
            else:
                await progress_msg.edit_text(
                    "❌ <b>لم يتم العثور على ترجمات</b>",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            logging.error(f"Subtitle extraction error: {e}")
            await progress_msg.edit_text(
                f"❌ <b>خطأ في استخراج الترجمات</b>\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )

        if user_id in self.user_states:
            del self.user_states[user_id]

    async def show_help_callback(self, query):
        """عرض المساعدة في الوضع التفاعلي"""
        help_text = """
📚 <b>دليل الاستخدام</b>

• أرسل رابط أي فيديو من المنصات المدعومة
• اختر نوع التحميل المطلوب
• انتظر حتى اكتمال التحميل
• استلم الملف عبر البوت

🔧 <b>الأوامر المتاحة:</b>
/start - بدء استخدام البوت
/help - عرض المساعدة
/stats - عرض الإحصائيات
/cancel - إلغاء العملية الحالية
        """

        keyboard = [
            [InlineKeyboardButton("🔙 العودة", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def show_stats_callback(self, query):
        """إظهار الإحصائيات"""
        user_id = query.from_user.id

        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute("""
                SELECT downloads_count, joined_date FROM users WHERE user_id = ?
            """, (user_id,))
            user_data = cursor.fetchone()

            cursor = conn.execute("""
                SELECT COUNT(*) as total, 
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM downloads WHERE user_id = ?
            """, (user_id,))
            download_stats = cursor.fetchone()

        if user_data:
            downloads_count, joined_date = user_data
            join_date = datetime.fromisoformat(joined_date).strftime('%Y-%m-%d')
        else:
            downloads_count, join_date = 0, "غير متاح"

        if download_stats:
            total_attempts, completed = download_stats
            success_rate = (completed / total_attempts * 100) if total_attempts > 0 else 0
        else:
            total_attempts = completed = success_rate = 0

        stats_text = f"""
📊 <b>إحصائياتك الشخصية</b>

• تاريخ الانضمام: {join_date}
• إجمالي التحميلات: {downloads_count}
• المحاولات الكلية: {total_attempts}
• النجح: {completed}
• معدل النجاح: {success_rate:.1f}%
        """

        keyboard = [
            [InlineKeyboardButton("🔙 العودة", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            stats_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def back_to_main(self, query):
        """العودة إلى القائمة الرئيسية"""
        user = query.from_user
        welcome_text = f"""
🎥 <b>مرحباً بعودتك {user.first_name}!</b>

اختر أحد الخيارات:
        """

        keyboard = [
            [InlineKeyboardButton("📋 المساعدة", callback_data="help"),
             InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def cancel_operation(self, query):
        """إلغاء العملية الحالية"""
        user_id = query.from_user.id
        if user_id in self.user_states:
            del self.user_states[user_id]

        await query.edit_message_text(
            "❌ <b>تم إلغاء العملية</b>\n\n"
            "يمكنك إرسال رابط جديد عندما تكون مستعداً.",
            parse_mode=ParseMode.HTML
        )

    async def handle_playlist(self, update: Update, info: Dict, loading_msg):
        """معالجة قوائم التشغيل (إصدار مبسط)"""
        if not self.config.enable_playlist_download:
            await loading_msg.edit_text(
                "❌ <b>تحميل قوائم التشغيل معطل حالياً</b>",
                parse_mode=ParseMode.HTML
            )
            return

        playlist_title = info.get('title', 'قائمة تشغيل')
        entries_count = len(info['entries'])

        info_text = f"""
📋 <b>قائمة تشغيل</b>

📌 <b>العنوان:</b> {playlist_title}
🔢 <b>عدد الفيديوهات:</b> {entries_count}

⚠️ <b>تحميل قوائم التشغيل غير متاح في هذه النسخة</b>
        """

        await loading_msg.edit_text(
            info_text,
            parse_mode=ParseMode.HTML
        )

    def run(self):
        """تشغيل البوت"""
        application = Application.builder().token(self.config.bot_token).build()

        # معالجات الأوامر
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("stats", self.show_stats_callback))
        application.add_handler(CommandHandler("cancel", self.cancel_operation))

        # معالج الروابط
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_url
        ))

        # معالج الأزرار
        application.add_handler(CallbackQueryHandler(self.callback_handler))

        print("🚀 البوت جاهز للعمل!")
        application.run_polling()

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

if __name__ == "__main__":
    config = BotConfig()

    if not config.bot_token:
        print("❌ خطأ: لم يتم تعيين BOT_TOKEN")
        print("يرجى إنشاء ملف .env وإضافة: BOT_TOKEN=your_bot_token_here")
        exit(1)

    bot = TelegramBot(config)

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 إيقاف البوت...")
    except Exception as e:
        logging.error(f"خطأ في تشغيل البوت: {e}")