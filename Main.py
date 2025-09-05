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
        self.user_states[update.