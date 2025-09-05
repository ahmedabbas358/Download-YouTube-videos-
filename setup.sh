#!/bin/bash

echo "🚀 إعداد بوت تليجرام المتقدم للتحميل..."

# تحديث النظام
echo "🔄 تحديث حزم النظام..."
apt-get update -y

# تثبيت الاعتماديات النظامية
echo "📦 تثبيت الاعتماديات النظامية..."
apt-get install -y ffmpeg python3-pip redis-server

# إنشاء المجلدات اللازمة
echo "📁 إنشاء المجلدات..."
mkdir -p downloads logs data

# تثبيت اعتماديات بايثون
echo "🐍 تثبيت اعتماديات Python..."
pip install -r requirements.txt

# إعداد صلاحيات الملفات
chmod +x Main.py

echo "✅ تم الانتهاء من الإعداد!"
echo "🎯 يمكنك الآن تشغيل البوت بـ: python Main.py"