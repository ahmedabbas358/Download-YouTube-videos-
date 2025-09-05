"""
Enhanced Telegram Video Downloader Bot
نسخة محسنة ومتطورة لبوت تحميل الفيديوهات من تليجرام
"""

import os
import asyncio
import logging
import json
import sqlite3
import tempfile
import time
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

# Telegram Bot imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Download engines
import yt_dlp
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import hashlib

# Configuration
@dataclass
class BotConfig:
    """تكوين البوت"""
    bot_token: str = os.getenv('BOT_TOKEN', '')
    admin_ids: List[int] = None
    max_file_size: int = int(os.getenv('MAX_FILE_SIZE', 2000))  # MB
    download_path: str = os.getenv('DOWNLOAD_PATH', './downloads')
    database_path: str = os.getenv('DATABASE_PATH', './bot.db')
    max_concurrent_downloads: int = int(os.getenv('MAX_CONCURRENT', 5))
    rate_limit_per_user: int = int(os.getenv('RATE_LIMIT', 10))  # per hour
    enable_playlist_download: bool = os.getenv('ENABLE_PLAYLIST', 'true').lower() == 'true'
    supported_platforms: List[str] = None
    
    def __post_init__(self):
        if self.admin_ids is None:
            admin_str = os.getenv('ADMIN_IDS', '')
            self.admin_ids = [int(x) for x in admin_str.split(',') if x.strip()]
        
        if self.supported_platforms is None:
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
                    is_premium BOOLEAN DEFAULT FALSE,
                    is_banned BOOLEAN DEFAULT FALSE,
                    settings TEXT DEFAULT '{}'
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
                
                CREATE INDEX IF NOT EXISTS idx_downloads_user_time 
                ON downloads(user_id, download_time);
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
                # ساعة جديدة، إعادة تعيين العداد
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
            
            # تحديث عداد التحميلات للمستخدم
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
        
        # إعدادات yt-dlp
        self.ydl_opts_base = {
            'format': 'best[height<=720]/best',
            'outtmpl': os.path.join(config.download_path, '%(title)s.%(ext)s'),
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['ar', 'en', 'fr', 'es', 'de'],
            'subtitlesformat': 'srt/vtt/best',
            'ignoreerrors': False,
            'no_warnings': False,
        }
    
    async def get_video_info(self, url: str) -> Optional[Dict]:
        """الحصول على معلومات الفيديو"""
        try:
            ydl_opts = {
                **self.ydl_opts_base,
                'quiet': True,
                'no_warnings': True,
            }
            
            loop = asyncio.get_event_loop()
            
            def extract_info():
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
        
        # ترتيب حسب الجودة
        formats.sort(key=lambda x: int(x['quality'].replace('p', '')) if x['quality'] != 'Unknown' else 0, reverse=True)
        return formats
    
    async def download_video(self, url: str, format_id: str = None, 
                           progress_callback=None) -> Tuple[bool, str, Dict]:
        """تحميل الفيديو مع إظهار التقدم"""
        try:
            filename = None
            info = {}
            
            # إعداد خيارات التحميل
            ydl_opts = {**self.ydl_opts_base}
            if format_id:
                ydl_opts['format'] = format_id
            
            # دالة لتتبع التقدم
            def progress_hook(d):
                if progress_callback:
                    progress_callback(d)
                
                if d['status'] == 'finished':
                    nonlocal filename, info
                    filename = d['filename']
                    
            ydl_opts['progress_hooks'] = [progress_hook]
            
            loop = asyncio.get_event_loop()
            
            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    return info_dict
            
            info = await loop.run_in_executor(self.executor, download)
            
            return True, filename, info
            
        except Exception as e:
            logging.error(f"Download error: {e}")
            return False, str(e), {}
    
    async def download_playlist(self, url: str, max_videos: int = 50,
                              progress_callback=None) -> List[Tuple[bool, str, Dict]]:
        """تحميل قائمة تشغيل"""
        try:
            # الحصول على معلومات قائمة التشغيل
            info = await self.get_video_info(url)
            if not info or 'entries' not in info:
                return [(False, "Invalid playlist", {})]
            
            entries = info['entries'][:max_videos]  # تحديد العدد الأقصى
            results = []
            
            # تحميل الفيديوهات بالتوازي مع الحد الأقصى للعمليات
            semaphore = asyncio.Semaphore(3)  # حد أقصى 3 تحميلات متزامنة
            
            async def download_single(entry):
                async with semaphore:
                    if not entry:
                        return False, "Empty entry", {}
                    
                    video_url = entry.get('url') or entry.get('webpage_url')
                    if not video_url:
                        return False, "No URL found", {}
                    
                    return await self.download_video(video_url, progress_callback=progress_callback)
            
            # تنفيذ التحميلات
            tasks = [download_single(entry) for entry in entries]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # معالجة النتائج
            processed_results = []
            for result in results:
                if isinstance(result, Exception):
                    processed_results.append((False, str(result), {}))
                else:
                    processed_results.append(result)
            
            return processed_results
            
        except Exception as e:
            logging.error(f"Playlist download error: {e}")
            return [(False, str(e), {})]
    
    async def extract_subtitles(self, url: str, languages: List[str] = None) -> Dict[str, str]:
        """استخراج ملفات الترجمة"""
        if languages is None:
            languages = ['ar', 'en']
        
        try:
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': languages,
                'subtitlesformat': 'srt',
                'skip_download': True,
                'outtmpl': os.path.join(self.config.download_path, 'subs', '%(title)s.%(ext)s'),
            }
            
            loop = asyncio.get_event_loop()
            
            def extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return info
            
            info = await loop.run_in_executor(self.executor, extract)
            
            # جمع مسارات ملفات الترجمة
            subtitle_files = {}
            if 'requested_subtitles' in info:
                for lang, sub_info in info['requested_subtitles'].items():
                    if sub_info and 'filepath' in sub_info:
                        subtitle_files[lang] = sub_info['filepath']
            
            return subtitle_files
            
        except Exception as e:
            logging.error(f"Subtitle extraction error: {e}")
            return {}

# حالات المحادثة
WAITING_URL, SELECTING_FORMAT, SELECTING_SUBTITLES = range(3)

class TelegramBot:
    """البوت الرئيسي مع واجهة متطورة"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.db = DatabaseManager(config.database_path)
        self.downloader = EnhancedDownloader(config)
        
        # إنشاء مجلدات التحميل
        os.makedirs(config.download_path, exist_ok=True)
        os.makedirs(os.path.join(config.download_path, 'subs'), exist_ok=True)
        
        # تخزين مؤقت لحالات المستخدمين
        self.user_states = {}
        self.active_downloads = {}
    
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

🚀 <b>بوت التحميل المتطور</b> - النسخة الاحترافية

<b>✅ المميزات:</b>
• 🎬 تحميل من منصات متعددة (YouTube, Twitter, Instagram, TikTok...)
• 📋 دعم قوائم التشغيل مع التحميل الجماعي
• 🎭 ترجمات متعددة (تلقائية وأصلية)
• 🎯 جودات مختلفة حتى 4K/8K
• ⚡ تحميل سريع ومتوازي
• 📊 إحصائيات مفصلة

<b>🔥 طريقة الاستخدام:</b>
1️⃣ أرسل رابط الفيديو
2️⃣ اختر الجودة والصيغة
3️⃣ حدد الترجمات المطلوبة
4️⃣ احصل على الملف!

<b>💡 أوامر مفيدة:</b>
/help - المساعدة الشاملة
/stats - إحصائياتك الشخصية
/settings - الإعدادات
/formats - الصيغ المدعومة
        """
        
        keyboard = [
            [InlineKeyboardButton("📋 المساعدة", callback_data="help"),
             InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings")],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"),
             InlineKeyboardButton("🌟 المميزات", callback_data="features")]
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
📚 <b>دليل الاستخدام الشامل</b>

<b>🎯 المنصات المدعومة:</b>
• YouTube & YouTube Music
• Twitter/X & Instagram
• TikTok & Facebook
• Vimeo & Dailymotion
• Twitch & المزيد...

<b>📋 تحميل قوائم التشغيل:</b>
• أرسل رابط القائمة مباشرة
• اختر عدد الفيديوهات (حتى 50)
• تحميل جماعي مع شريط التقدم
• حفظ الترتيب الأصلي

<b>🎭 الترجمات:</b>
• ترجمات أصلية من المنصة
• ترجمات تلقائية بلغات متعددة
• صيغ: SRT, VTT, ASS
• تحميل منفصل أو مع الفيديو

<b>🎬 الجودات المتاحة:</b>
• 144p - 8K حسب المتاح
• صوت فقط (MP3, M4A)
• فيديو بدون صوت
• أفضل جودة تلقائياً

<b>⚙️ الإعدادات:</b>
• تفضيلات الجودة الافتراضية
• اللغات المفضلة للترجمة
• تفعيل/إلغاء الإشعارات
• حد حجم الملف

<b>🔧 الأوامر:</b>
/start - البداية
/help - هذه المساعدة  
/stats - إحصائياتك
/settings - الإعدادات
/cancel - إلغاء العملية الحالية
/admin - لوحة الإدارة (للمديرين فقط)
        """
        
        keyboard = [
            [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_main")]
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
        
        # فحص حد المعدل
        if not self.db.check_rate_limit(user_id, self.config.rate_limit_per_user):
            await update.message.reply_text(
                "⏰ <b>تم تجاوز الحد المسموح!</b>\n\n"
                f"يمكنك تحميل حتى {self.config.rate_limit_per_user} ملفات في الساعة.\n"
                "حاول مرة أخرى لاحقاً.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # فحص دعم المنصة
        if not self.is_supported_platform(url):
            await update.message.reply_text(
                "❌ <b>منصة غير مدعومة</b>\n\n"
                "المنصات المدعومة:\n" + 
                "\n".join(f"• {platform}" for platform in self.config.supported_platforms),
                parse_mode=ParseMode.HTML
            )
            return
        
        # رسالة انتظار
        loading_msg = await update.message.reply_text(
            "🔍 <b>جاري تحليل الرابط...</b>\n"
            "⏳ يرجى الانتظار...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # الحصول على معلومات الفيديو
            info = await self.downloader.get_video_info(url)
            
            if not info:
                await loading_msg.edit_text(
                    "❌ <b>خطأ في تحليل الرابط</b>\n\n"
                    "تأكد من صحة الرابط وأنه متاح للعامة.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # فحص إذا كانت قائمة تشغيل
            if 'entries' in info and len(info['entries']) > 1:
                await self.handle_playlist(update, context, url, info, loading_msg)
                return
            
            # فيديو واحد - عرض الخيارات
            await self.show_video_options(update, context, url, info, loading_msg)
            
        except Exception as e:
            logging.error(f"Error handling URL: {e}")
            await loading_msg.edit_text(
                "❌ <b>حدث خطأ أثناء معالجة الرابط</b>\n\n"
                f"تفاصيل الخطأ: {str(e)}",
                parse_mode=ParseMode.HTML
            )
    
    async def show_video_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                               url: str, info: Dict, loading_msg):
        """عرض خيارات الفيديو"""
        title = info.get('title', 'Unknown Title')[:50]
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        
        # تنسيق المدة
        duration_str = f"{duration//60}:{duration%60:02d}" if duration else "غير محدد"
        
        # الحصول على الصيغ المتاحة
        formats = await self.downloader.get_available_formats(url)
        
        info_text = f"""
🎬 <b>{title}</b>

📺 <b>القناة:</b> {uploader}
⏱️ <b>المدة:</b> {duration_str}
🌐 <b>المنصة:</b> {urlparse(url).netloc}

<b>اختر طريقة التحميل:</b>
        """
        
        keyboard = [
            [InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"video_{hash(url)}")],
            [InlineKeyboardButton("🎵 صوت فقط", callback_data=f"audio_{hash(url)}")],
            [InlineKeyboardButton("📝 ترجمات فقط", callback_data=f"subs_{hash(url)}")],
            [InlineKeyboardButton("📦 الكل معاً", callback_data=f"all_{hash(url)}")],
            [InlineKeyboardButton("⚙️ خيارات متقدمة", callback_data=f"advanced_{hash(url)}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # حفظ معلومات الرابط
        self.user_states[update.effective_user.id] = {
            'url': url,
            'info': info,
            'formats': formats
        }
        
        await loading_msg.edit_text(
            info_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def handle_playlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            url: str, info: Dict, loading_msg):
        """معالجة قوائم التشغيل"""
        if not self.config.enable_playlist_download:
            await loading_msg.edit_text(
                "❌ <b>تحميل قوائم التشغيل معطل</b>\n\n"
                "راجع المدير لتفعيل هذه الميزة.",
                parse_mode=ParseMode.HTML
            )
            return
        
        playlist_title = info.get('title', 'قائمة تشغيل')
        entries_count = len(info['entries'])
        uploader = info.get('uploader', 'Unknown')
        
        info_text = f"""
📋 <b>قائمة تشغيل</b>

📌 <b>العنوان:</b> {playlist_title}
📺 <b>القناة:</b> {uploader}  
🔢 <b>عدد الفيديوهات:</b> {entries_count}

⚠️ <b>تنبيه:</b> قد يستغرق التحميل وقتاً طويلاً حسب حجم القائمة.

<b>اختر عدد الفيديوهات للتحميل:</b>
        """
        
        keyboard = [
            [InlineKeyboardButton("5️⃣ أول 5 فيديوهات", callback_data=f"playlist_{hash(url)}_5")],
            [InlineKeyboardButton("🔟 أول 10 فيديوهات", callback_data=f"playlist_{hash(url)}_10")],
            [InlineKeyboardButton("2️⃣0️⃣ أول 20 فيديو", callback_data=f"playlist_{hash(url)}_20")],
            [InlineKeyboardButton("5️⃣0️⃣ الكل (حتى 50)", callback_data=f"playlist_{hash(url)}_50")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # حفظ معلومات القائمة
        self.user_states[update.effective_user.id] = {
            'url': url,
            'info': info,
            'type': 'playlist'
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
        
        user_id = update.effective_user.id
        data = query.data
        
        if data == "help":
            await self.show_help_callback(query)
        elif data == "settings":
            await self.show_settings_callback(query)
        elif data == "stats":
            await self.show_stats_callback(query)
        elif data == "features":
            await self.show_features_callback(query)
        elif data == "back_main":
            await self.back_to_main(query)
        elif data == "cancel":
            await self.cancel_operation(query)
        elif data.startswith("video_") or data.startswith("audio_") or data.startswith("subs_") or data.startswith("all_"):
            await self.handle_download_choice(query, data)
        elif data.startswith("playlist_"):
            await self.handle_playlist_download(query, data)
        elif data.startswith("format_"):
            await self.handle_format_selection(query, data)
        elif data.startswith("lang_"):
            await self.handle_language_selection(query, data)
        elif data.startswith("admin_"):
            await self.handle_admin_action(query, data)
    
    async def handle_download_choice(self, query, data: str):
        """معالجة اختيار نوع التحميل"""
        user_id = query.from_user.id
        
        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return
        
        choice_type = data.split('_')[0]
        user_data = self.user_states[user_id]
        url = user_data['url']
        
        if choice_type == "video":
            await self.show_quality_selection(query, user_data)
        elif choice_type == "audio":
            await self.download_audio_only(query, url)
        elif choice_type == "subs":
            await self.show_subtitle_languages(query, user_data)
        elif choice_type == "all":
            await self.download_everything(query, user_data)
        elif choice_type == "advanced":
            await self.show_advanced_options(query, user_data)
    
    async def show_quality_selection(self, query, user_data: Dict):
        """عرض خيارات الجودة"""
        formats = user_data.get('formats', [])
        
        if not formats:
            await query.edit_message_text(
                "❌ لا توجد صيغ متاحة للتحميل.",
                parse_mode=ParseMode.HTML
            )
            return
        
        text = "🎬 <b>اختر جودة الفيديو:</b>\n\n"
        keyboard = []
        
        for fmt in formats[:8]:  # عرض أول 8 صيغ
            quality_text = f"📺 {fmt['quality']} ({fmt['ext'].upper()})"
            if fmt['size_mb'] > 0:
                quality_text += f" - {fmt['size_mb']} MB"
            
            keyboard.append([InlineKeyboardButton(
                quality_text,
                callback_data=f"format_{fmt['format_id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data="back_options")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def show_subtitle_languages(self, query, user_data: Dict):
        """عرض خيارات لغات الترجمة"""
        text = """
📝 <b>اختر لغات الترجمة:</b>

يمكنك اختيار لغة واحدة أو أكثر:
        """
        
        keyboard = [
            [InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
             InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")],
            [InlineKeyboardButton("🇫🇷 Français", callback_data="lang_fr"),
             InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es")],
            [InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_de"),
             InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🌍 جميع اللغات المتاحة", callback_data="lang_all")],
            [InlineKeyboardButton("✅ تأكيد التحميل", callback_data="confirm_subs")],
            [InlineKeyboardButton("🔙 العودة", callback_data="back_options")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # تخزين اللغات المختارة
        if 'selected_languages' not in user_data:
            user_data['selected_languages'] = []
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def handle_playlist_download(self, query, data: str):
        """معالجة تحميل قائمة التشغيل"""
        user_id = query.from_user.id
        
        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return
        
        parts = data.split('_')
        max_videos = int(parts[2])
        
        user_data = self.user_states[user_id]
        url = user_data['url']
        
        # تأكيد التحميل
        confirm_text = f"""
📋 <b>تأكيد تحميل قائمة التشغيل</b>

🔢 <b>عدد الفيديوهات:</b> {max_videos}
⚠️ <b>تقدير الوقت:</b> {max_videos * 2} - {max_videos * 5} دقيقة
💾 <b>تقدير الحجم:</b> {max_videos * 100} - {max_videos * 500} MB

<b>هل تريد المتابعة?</b>
        """
        
        keyboard = [
            [InlineKeyboardButton("✅ نعم، ابدأ التحميل", 
                                callback_data=f"confirm_playlist_{max_videos}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            confirm_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def start_playlist_download(self, query, max_videos: int):
        """بدء تحميل قائمة التشغيل"""
        user_id = query.from_user.id
        user_data = self.user_states[user_id]
        url = user_data['url']
        
        # إنشاء رسالة التقدم
        progress_msg = await query.edit_message_text(
            "🚀 <b>بدء تحميل قائمة التشغيل...</b>\n"
            "⏳ يرجى الانتظار، قد يستغرق هذا بعض الوقت...",
            parse_mode=ParseMode.HTML
        )
        
        # متغيرات التتبع
        completed = 0
        failed = 0
        start_time = time.time()
        
        def update_progress(current, total, status="جاري التحميل"):
            nonlocal completed, failed
            if status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
            
            elapsed = time.time() - start_time
            progress_percent = (completed + failed) / total * 100
            
            progress_text = f"""
📋 <b>تحميل قائمة التشغيل</b>

📊 <b>التقدم:</b> {completed + failed}/{total} ({progress_percent:.1f}%)
✅ <b>مكتمل:</b> {completed}
❌ <b>فشل:</b> {failed}
⏱️ <b>الوقت المنقضي:</b> {elapsed/60:.1f} دقيقة

{'▓' * int(progress_percent/5)}{'░' * (20-int(progress_percent/5))}
            """
            
            return progress_text
        
        try:
            # تحميل القائمة
            results = await self.downloader.download_playlist(
                url, 
                max_videos,
                progress_callback=lambda d: None  # يمكن تحسينه لاحقاً
            )
            
            # تحديث نهائي
            completed_files = []
            failed_files = []
            
            for i, (success, filename_or_error, info) in enumerate(results):
                if success and filename_or_error:
                    completed_files.append(filename_or_error)
                    self.db.log_download(
                        user_id, url, info.get('title', f'Video {i+1}'),
                        urlparse(url).netloc, 0, 'completed'
                    )
                else:
                    failed_files.append(filename_or_error)
                    self.db.log_download(
                        user_id, url, f'Video {i+1}',
                        urlparse(url).netloc, 0, 'failed', filename_or_error
                    )
            
            # إرسال النتيجة النهائية
            final_text = f"""
🎉 <b>اكتمل تحميل قائمة التشغيل!</b>

✅ <b>مكتمل بنجاح:</b> {len(completed_files)}
❌ <b>فشل:</b> {len(failed_files)}
⏱️ <b>إجمالي الوقت:</b> {(time.time() - start_time)/60:.1f} دقيقة

📁 <b>الملفات محفوظة في مجلد التحميلات</b>
            """
            
            await progress_msg.edit_text(final_text, parse_mode=ParseMode.HTML)
            
            # إرسال الملفات إذا كانت صغيرة
            for file_path in completed_files[:5]:  # أول 5 ملفات فقط
                try:
                    if os.path.exists(file_path) and os.path.getsize(file_path) < 50 * 1024 * 1024:  # أقل من 50MB
                        with open(file_path, 'rb') as file:
                            await query.message.reply_document(file)
                except Exception as e:
                    logging.error(f"Error sending file {file_path}: {e}")
            
        except Exception as e:
            logging.error(f"Playlist download error: {e}")
            await progress_msg.edit_text(
                f"❌ <b>خطأ في تحميل قائمة التشغيل</b>\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )
        
        # تنظيف حالة المستخدم
        if user_id in self.user_states:
            del self.user_states[user_id]
    
    async def download_single_video(self, query, format_id: str = None):
        """تحميل فيديو واحد"""
        user_id = query.from_user.id
        
        if user_id not in self.user_states:
            await query.edit_message_text(
                "❌ انتهت صلاحية الجلسة. أرسل الرابط مرة أخرى.",
                parse_mode=ParseMode.HTML
            )
            return
        
        user_data = self.user_states[user_id]
        url = user_data['url']
        info = user_data['info']
        
        # رسالة التحميل
        progress_msg = await query.edit_message_text(
            "🚀 <b>بدء التحميل...</b>\n"
            "⏳ يرجى الانتظار...",
            parse_mode=ParseMode.HTML
        )
        
        start_time = time.time()
        last_update = 0
        
        def progress_callback(d):
            nonlocal last_update
            if time.time() - last_update < 2:  # تحديث كل ثانيتين
                return
            last_update = time.time()
            
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '0%')
                speed = d.get('_speed_str', 'غير محدد')
                eta = d.get('_eta_str', 'غير محدد')
                
                progress_text = f"""
📥 <b>جاري التحميل...</b>

📊 <b>التقدم:</b> {percent}
🚀 <b>السرعة:</b> {speed}
⏱️ <b>الوقت المتبقي:</b> {eta}

{'▓' * int(float(percent.replace('%', ''))//5)}{'░' * (20-int(float(percent.replace('%', ''))//5))}
                """
                
                # تحديث async
                asyncio.create_task(progress_msg.edit_text(
                    progress_text, parse_mode=ParseMode.HTML
                ))
        
        try:
            # تحميل الفيديو
            success, result, download_info = await self.downloader.download_video(
                url, format_id, progress_callback
            )
            
            if success and result:
                # تسجيل التحميل الناجح
                file_size = os.path.getsize(result) if os.path.exists(result) else 0
                self.db.log_download(
                    user_id, url, info.get('title', 'Unknown'),
                    urlparse(url).netloc, file_size, 'completed'
                )
                
                # إرسال الملف
                if file_size < self.config.max_file_size * 1024 * 1024:
                    await progress_msg.edit_text(
                        "📤 <b>جاري إرسال الملف...</b>",
                        parse_mode=ParseMode.HTML
                    )
                    
                    with open(result, 'rb') as video_file:
                        await query.message.reply_document(
                            video_file,
                            caption=f"🎬 {info.get('title', 'فيديو')}\n"
                                   f"📁 الحجم: {file_size/(1024*1024):.1f} MB"
                        )
                    
                    await progress_msg.edit_text(
                        "✅ <b>تم التحميل بنجاح!</b>",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await progress_msg.edit_text(
                        f"✅ <b>تم التحميل بنجاح!</b>\n\n"
                        f"⚠️ الملف كبير جداً للإرسال ({file_size/(1024*1024):.1f} MB)\n"
                        f"📁 محفوظ في: {result}",
                        parse_mode=ParseMode.HTML
                    )
            else:
                # تسجيل فشل التحميل
                self.db.log_download(
                    user_id, url, info.get('title', 'Unknown'),
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
        
        # تنظيف
        if user_id in self.user_states:
            del self.user_states[user_id]
    
    async def download_subtitles_only(self, query, languages: List[str]):
        """تحميل الترجمات فقط"""
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
            "📝 <b>جاري استخراج الترجمات...</b>\n"
            "⏳ يرجى الانتظار...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            subtitle_files = await self.downloader.extract_subtitles(url, languages)
            
            if subtitle_files:
                await progress_msg.edit_text(
                    f"✅ <b>تم استخراج {len(subtitle_files)} ملف ترجمة</b>",
                    parse_mode=ParseMode.HTML
                )
                
                # إرسال ملفات الترجمة
                for lang, filepath in subtitle_files.items():
                    if os.path.exists(filepath):
                        with open(filepath, 'rb') as sub_file:
                            await query.message.reply_document(
                                sub_file,
                                caption=f"📝 ترجمة {lang.upper()}"
                            )
            else:
                await progress_msg.edit_text(
                    "❌ <b>لم يتم العثور على ترجمات</b>\n\n"
                    "قد لا تكون الترجمات متاحة لهذا الفيديو.",
                    parse_mode=ParseMode.HTML
                )
        
        except Exception as e:
            logging.error(f"Subtitle extraction error: {e}")
            await progress_msg.edit_text(
                f"❌ <b>خطأ في استخراج الترجمات</b>\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )
        
        # تنظيف
        if user_id in self.user_states:
            del self.user_states[user_id]
    
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
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                       SUM(file_size) as total_size
                FROM downloads WHERE user_id = ?
            """, (user_id,))
            download_stats = cursor.fetchone()
        
        if user_data:
            downloads_count, joined_date = user_data
            join_date = datetime.fromisoformat(joined_date).strftime('%Y-%m-%d')
        else:
            downloads_count, join_date = 0, "غير متاح"
        
        if download_stats:
            total_attempts, completed, total_size = download_stats
            success_rate = (completed / total_attempts * 100) if total_attempts > 0 else 0
            total_size_mb = (total_size or 0) / (1024 * 1024)
        else:
            total_attempts = completed = success_rate = total_size_mb = 0
        
        stats_text = f"""
📊 <b>إحصائياتك الشخصية</b>

👤 <b>معلومات الحساب:</b>
• تاريخ الانضمام: {join_date}
• إجمالي التحميلات: {downloads_count}

📈 <b>إحصائيات التحميل:</b>
• المحاولات الكلية: {total_attempts}
• النجح: {completed}
• معدل النجاح: {success_rate:.1f}%
• الحجم الكلي: {total_size_mb:.1f} MB

🏆 <b>مستوى المستخدم:</b>
{"🥉 مبتدئ" if downloads_count < 10 else "🥈 متوسط" if downloads_count < 50 else "🥇 متقدم" if downloads_count < 100 else "💎 محترف"}
        """
        
        keyboard = [
            [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            stats_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """لوحة تحكم المدير"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ ليس لديك صلاحيات إدارية.")
            return
        
        # إحصائيات عامة
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            cursor = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM downloads 
                WHERE download_time > datetime('now', '-24 hours')
            """)
            daily_stats = cursor.fetchone()
        
        admin_text = f"""
👨‍💼 <b>لوحة التحكم الإدارية</b>

📊 <b>الإحصائيات العامة:</b>
• إجمالي المستخدمين: {total_users}
• التحميلات اليوم: {daily_stats[0] if daily_stats else 0}
• نجح اليوم: {daily_stats[1] if daily_stats else 0}

⚙️ <b>إعدادات النظام:</b>
• حد حجم الملف: {self.config.max_file_size} MB
• التحميل المتوازي: {self.config.max_concurrent_downloads}
• حد المعدل: {self.config.rate_limit_per_user}/ساعة
        """
        
        keyboard = [
            [InlineKeyboardButton("📊 إحصائيات مفصلة", callback_data="admin_detailed_stats")],
            [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("⚙️ إعدادات النظام", callback_data="admin_settings")],
            [InlineKeyboardButton("📋 سجل الأخطاء", callback_data="admin_logs")],
            [InlineKeyboardButton("🔄 إعادة تشغيل", callback_data="admin_restart")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            admin_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def run(self):
        """تشغيل البوت"""
        # إعداد التطبيق
        application = Application.builder().token(self.config.bot_token).build()
        
        # معالجات الأوامر
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("admin", self.admin_command))
        
        # معالج الروابط
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_url
        ))
        
        # معالج الأزرار
        application.add_handler(CallbackQueryHandler(self.callback_handler))
        
        # إعداد الأوامر في تليجرام
        async def post_init(app):
            await app.bot.set_my_commands([
                BotCommand("start", "بدء استخدام البوت"),
                BotCommand("help", "المساعدة والإرشادات"),
                BotCommand("stats", "إحصائياتك الشخصية"),
                BotCommand("admin", "لوحة الإدارة (للمديرين)")
            ])
        
        application.post_init = post_init
        
        print("🚀 البوت جاهز للعمل!")
        print(f"📊 المنصات المدعومة: {', '.join(self.config.supported_platforms)}")
        
        # تشغيل البوت
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# نقطة البداية
if __name__ == "__main__":
    # تحميل التكوين
    config = BotConfig()
    
    if not config.bot_token:
        print("❌ خطأ: لم يتم تعيين BOT_TOKEN")
        print("يرجى إنشاء ملف .env وإضافة: BOT_TOKEN=your_bot_token_here")
        exit(1)
    
    # إنشاء وتشغيل البوت
    bot = TelegramBot(config)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 إيقاف البوت...")
    except Exception as e:
        logging.error(f"خطأ في تشغيل البوت: {e}")
        print(f"❌ خطأ في تشغيل البوت: {e}")