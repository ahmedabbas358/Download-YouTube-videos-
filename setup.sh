#!/bin/bash
# setup.sh - سكريبت إعداد وتثبيت البوت المحسن

set -e  # إيقاف عند أي خطأ

# ألوان للإخراج
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# طباعة رسائل ملونة
print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# بداية السكريبت
echo -e "${BLUE}"
cat << "EOF"
╔══════════════════════════════════════════╗
║     Enhanced Telegram Video Downloader   ║
║              Bot Setup Script            ║
║                                          ║
║        النسخة المحسنة من بوت التحميل        ║
╚══════════════════════════════════════════╝
EOF
echo -e "${NC}"

# فحص نظام التشغيل
print_info "فحص نظام التشغيل..."
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    print_success "نظام Linux تم اكتشافه"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    print_success "نظام macOS تم اكتشافه"
else
    OS="windows"
    print_warning "نظام Windows - قد تحتاج لتثبيت يدوي لبعض المكونات"
fi

# فحص Python
print_info "فحص Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d" " -f2)
    print_success "Python $PYTHON_VERSION موجود"
    
    # فحص إصدار Python
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    
    if [[ $PYTHON_MAJOR -eq 3 && $PYTHON_MINOR -ge 8 ]]; then
        print_success "إصدار Python مناسب (3.8+)"
    else
        print_error "يتطلب Python 3.8 أو أحدث. الإصدار الحالي: $PYTHON_VERSION"
        exit 1
    fi
else
    print_error "Python غير موجود. يرجى تثبيت Python 3.8+"
    exit 1
fi

# فحص pip
print_info "فحص pip..."
if command -v pip3 &> /dev/null; then
    print_success "pip موجود"
else
    print_error "pip غير موجود. يرجى تثبيت pip"
    exit 1
fi

# تثبيت ffmpeg
print_info "فحص ffmpeg..."
if command -v ffmpeg &> /dev/null; then
    print_success "ffmpeg موجود"
else
    print_warning "ffmpeg غير موجود. جاري التثبيت..."
    
    if [[ $OS == "linux" ]]; then
        if command -v apt-get &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y ffmpeg
        elif command -v yum &> /dev/null; then
            sudo yum install -y ffmpeg
        elif command -v pacman &> /dev/null; then
            sudo pacman -S ffmpeg
        else
            print_error "لا يمكن تثبيت ffmpeg تلقائياً. يرجى تثبيته يدوياً"
        fi
    elif [[ $OS == "macos" ]]; then
        if command -v brew &> /dev/null; then
            brew install ffmpeg
        else
            print_error "Homebrew غير موجود. يرجى تثبيت ffmpeg يدوياً أو تثبيت Homebrew أولاً"
        fi
    fi
    
    if command -v ffmpeg &> /dev/null; then
        print_success "تم تثبيت ffmpeg بنجاح"
    else
        print_error "فشل تثبيت ffmpeg"
        exit 1
    fi
fi

# إنشاء البيئة الافتراضية
print_info "إنشاء البيئة الافتراضية..."
if [[ ! -d "venv" ]]; then
    python3 -m venv venv
    print_success "تم إنشاء البيئة الافتراضية"
else
    print_info "البيئة الافتراضية موجودة مسبقاً"
fi

# تفعيل البيئة الافتراضية
print_info "تفعيل البيئة الافتراضية..."
source venv/bin/activate

# تحديث pip
print_info "تحديث pip..."
pip install --upgrade pip

# تثبيت المتطلبات
print_info "تثبيت المتطلبات Python..."
if [[ -f "requirements.txt" ]]; then
    pip install -r requirements.txt
    print_success "تم تثبيت جميع المتطلبات"
else
    print_error "ملف requirements.txt غير موجود"
    exit 1
fi

# إنشاء المجلدات اللازمة
print_info "إنشاء المجلدات..."
mkdir -p downloads logs data config tests/files
print_success "تم إنشاء المجلدات"

# نسخ ملف التكوين
print_info "إعداد ملف التكوين..."
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        print_success "تم نسخ ملف .env من .env.example"
        print_warning "يرجى تعديل ملف .env وإضافة BOT_TOKEN و ADMIN_IDS"
    else
        print_error "ملف .env.example غير موجود"
    fi
else
    print_info "ملف .env موجود مسبقاً"
fi

# إعداد قاعدة البيانات
print_info "إعداد قاعدة البيانات..."
python3 -c "
import sqlite3
import os

db_path = './data/bot.db'
os.makedirs(os.path.dirname(db_path), exist_ok=True)

conn = sqlite3.connect(db_path)
print('✅ تم إنشاء قاعدة البيانات بنجاح')
conn.close()
"

# فحص التكوين
print_info "فحص التكوين..."
python3 -c "
import os
from dotenv import load_dotenv

load_dotenv()

bot_token = os.getenv('BOT_TOKEN')
admin_ids = os.getenv('ADMIN_IDS')

if bot_token and bot_token != 'your_bot_token_here':
    print('✅ BOT_TOKEN تم تعيينه')
else:
    print('⚠️  يرجى تعيين BOT_TOKEN في ملف .env')

if admin_ids and admin_ids != '123456789,987654321':
    print('✅ ADMIN_IDS تم تعيينها')
else:
    print('⚠️  يرجى تعيين ADMIN_IDS في ملف .env')
" 2>/dev/null || print_warning "يرجى تثبيت python-dotenv: pip install python-dotenv"

# إنشاء سكريبت التشغيل
print_info "إنشاء سكريبت التشغيل..."
cat > run_bot.sh << 'EOF'
#!/bin/bash
# سكريبت تشغيل البوت

# الانتقال لمجلد البوت
cd "$(dirname "$0")"

# تفعيل البيئة الافتراضية
source venv/bin/activate

# تشغيل البوت
python main.py
EOF

chmod +x run_bot.sh
print_success "تم إنشاء سكريبت التشغيل: ./run_bot.sh"

# إنشاء سكريبت التحديث
cat > update_bot.sh << 'EOF'
#!/bin/bash
# سكريبت تحديث البوت

echo "🔄 جاري تحديث البوت..."

# تفعيل البيئة الافتراضية
source venv/bin/activate

# تحديث المتطلبات
pip install --upgrade -r requirements.txt

# تحديث yt-dlp
pip install --upgrade yt-dlp

echo "✅ تم تحديث البوت بنجاح"
EOF

chmod +x update_bot.sh
print_success "تم إنشاء سكريبت التحديث: ./update_bot.sh"

# إنشاء systemd service (Linux فقط)
if [[ $OS == "linux" ]] && [[ -d "/etc/systemd/system" ]]; then
    print_info "إنشاء خدمة systemd..."
    
    SERVICE_FILE="telegram-bot.service"
    CURRENT_DIR=$(pwd)
    USER=$(whoami)
    
    cat > $SERVICE_FILE << EOF
[Unit]
Description=Enhanced Telegram Video Downloader Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$CURRENT_DIR
ExecStart=$CURRENT_DIR/run_bot.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    print_success "تم إنشاء ملف الخدمة: $SERVICE_FILE"
    print_info "لتثبيت الخدمة: sudo cp $SERVICE_FILE /etc/systemd/system/"
    print_info "لتفعيل الخدمة: sudo systemctl enable telegram-bot"
    print_info "لبدء الخدمة: sudo systemctl start telegram-bot"
fi

# اختبار سريع
print_info "إجراء اختبار سريع..."
python3 -c "
try:
    import telegram
    import yt_dlp
    import sqlite3
    import asyncio
    print('✅ جميع المكتبات الأساسية تعمل بشكل صحيح')
except ImportError as e:
    print(f'❌ خطأ في استيراد المكتبات: {e}')
"

# رسالة النهاية
echo -e "${GREEN}"
cat << "EOF"
╔══════════════════════════════════════════╗
║            تم الإعداد بنجاح! 🎉          ║
╠══════════════════════════════════════════╣
║                                          ║
║  الخطوات التالية:                        ║
║                                          ║
║  1️⃣  تعديل ملف .env                      ║
║     - إضافة BOT_TOKEN                   ║
║     - إضافة ADMIN_IDS                   ║
║                                          ║
║  2️⃣  تشغيل البوت:                       ║
║     ./run_bot.sh                        ║
║                                          ║
║  3️⃣  للتحديث:                           ║
║     ./update_bot.sh                     ║
║                                          ║
╚══════════════════════════════════════════╝
EOF
echo -e "${NC}"

print_info "للحصول على رمز البوت:"
echo -e "   ${BLUE}1. اذهب إلى @BotFather في تليجرام${NC}"
echo -e "   ${BLUE}2. أرسل /newbot${NC}"
echo -e "   ${BLUE}3. اتبع التعليمات${NC}"
echo -e "   ${BLUE}4. انسخ الرمز إلى ملف .env${NC}"

print_info "للحصول على معرف المستخدم:"
echo -e "   ${BLUE}أرسل رسالة لـ @userinfobot في تليجرام${NC}"

print_success "الإعداد مكتمل! 🚀"