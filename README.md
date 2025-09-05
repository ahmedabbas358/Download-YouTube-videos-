# Download-YouTube-videos-

# 🎥 Enhanced Telegram Video Downloader Bot

<div align="center">

![Bot Banner](https://img.shields.io/badge/Telegram-Bot-blue?style=for-the-badge&logo=telegram)
![Python](https://img.shields.io/badge/Python-3.8+-green?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)

**نسخة محسنة واحترافية من بوت تحميل الفيديوهات من تليجرام**

[المميزات](#-المميزات) • [التثبيت](#️-التثبيت) • [الاستخدام](#-الاستخدام) • [التكوين](#️-التكوين) • [المساهمة](#-المساهمة)

</div>

---

## 🌟 المميزات

### 📱 منصات متعددة
- ✅ **YouTube** - فيديوهات وقوائم تشغيل كاملة
- ✅ **Twitter/X** - تغريدات بالفيديو
- ✅ **Instagram** - منشورات وريلز
- ✅ **TikTok** - فيديوهات قصيرة
- ✅ **Facebook** - مقاطع عامة
- ✅ **Vimeo** - محتوى احترافي
- ✅ **Dailymotion** - مقاطع متنوعة
- ✅ **Twitch** - كليبات ومقاطع

### 🎬 خيارات تحميل متقدمة
- 🎯 **جودات متعددة**: من 144p حتى 8K
- 🎵 **استخراج الصوت**: MP3, M4A, WAV
- 📋 **قوائم التشغيل**: تحميل جماعي ذكي
- ⚡ **تحميل متوازي**: سرعة فائقة
- 📊 **شريط التقدم**: متابعة فورية

### 🎭 ترجمات شاملة
- 🌍 **لغات متعددة**: العربية، الإنجليزية، الفرنسية...
- 🤖 **ترجمة تلقائية**: بواسطة YouTube AI
- 📝 **صيغ متنوعة**: SRT, VTT, ASS
- 💾 **تحميل منفصل**: ترجمات بدون فيديو
- 🎯 **اختيار ذكي**: حسب تفضيلات المستخدم

### 🔧 إدارة متطورة
- 👨‍💼 **لوحة إدارة**: إحصائيات وتحكم كامل
- 📊 **تحليلات**: معدلات النجاح والاستخدام
- 🚦 **حد المعدل**: حماية من الإفراط
- 🛡️ **أمان متقدم**: حماية من البريد المزعج
- 📱 **واجهة تفاعلية**: أزرار ذكية

---

## 🏗️ التثبيت

### الطريقة السريعة (مستحسنة)

```bash
# استنساخ المشروع
git clone https://github.com/your-repo/enhanced-telegram-bot.git
cd enhanced-telegram-bot

# تشغيل سكريبت الإعداد
chmod +x setup.sh
./setup.sh
```

### التثبيت اليدوي

<details>
<summary>انقر لعرض خطوات التثبيت اليدوي</summary>

#### 1. متطلبات النظام
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3 python3-pip ffmpeg git

# CentOS/RHEL
sudo yum install -y python3 python3-pip ffmpeg git

# macOS (مع Homebrew)
brew install python ffmpeg git
```

#### 2. إعداد Python
```bash
# إنشاء بيئة افتراضية
python3 -m venv venv

# تفعيل البيئة
source venv/bin/activate  # Linux/Mac
# أو
venv\Scripts\activate  # Windows

# تحديث pip
pip install --upgrade pip
```

#### 3. تثبيت المتطلبات
```bash
pip install -r requirements.txt
```

#### 4. إنشاء المجلدات
```bash
mkdir -p downloads logs data config tests
```

</details>

### 🐳 Docker (للإنتاج)

```bash
# بناء الصورة
docker-compose build

# تشغيل الخدمات
docker-compose up -d

# مراقبة السجلات
docker-compose logs 