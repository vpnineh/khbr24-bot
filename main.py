import os
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import pickle
from sentence_transformers import SentenceTransformer, util

# ==========================================
# ۱. تنظیمات پایه‌ای و متغیرها
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
TARGET_CHANNEL = os.environ.get('TARGET_CHANNEL')

MAX_HISTORY = 150          
CHECK_LAST_N_POSTS = 10    
# افزایش به 0.92: یعنی خبرها باید 92 درصد شبیه هم باشند تا تکراری محسوب شوند (اجازه عبور خبرهای بیشتر)
SIMILARITY_THRESHOLD = 0.92 

AD_KEYWORDS = [
    "تخفیف", "خرید", "فروش", "ارزان", "تبلیغات", "اسپانسر", "کلیک کنید", 
    "ثبت نام", "کسب درآمد", "پراکسی", "proxy", "vpn", "فیلترشکن", 
    "جهت سفارش", "ارز دیجیتال", "ترید", "سیگنال رایگان"
]

SPAM_PHRASES = [
    "لینک گروه", "جوین", "عضو شوید", "کانال ما", "ادامه مطلب", 
    "بیشتر بخوانید", "سابسکرایب", "حمایت از ما", "👇", "👆", "لینک زیر", "آیدی زیر"
]

# ==========================================
# ۲. بارگذاری هوش مصنوعی
# ==========================================
logger.info("در حال بارگذاری مدل هوش مصنوعی (NLP)...")
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

# ==========================================
# ۳. توابع کمکی و پردازشی
# ==========================================
def load_channels():
    if not os.path.exists('channels.txt'):
        logger.error("فایل channels.txt پیدا نشد!")
        return []
    with open('channels.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def get_history():
    if not os.path.exists('history.pkl'): 
        return []
    try:
        with open('history.pkl', 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        logger.error(f"خطا در خواندن فایل تاریخچه: {e}")
        return []

def save_history(history_list):
    with open('history.pkl', 'wb') as f:
        pickle.dump(history_list[-MAX_HISTORY:], f)

def is_ad(text):
    text_lower = text.lower()
    for keyword in AD_KEYWORDS:
        if keyword in text_lower:
            return True
    
    text_without_links = re.sub(r'https?://\S+', '', text_lower)
    if text_without_links.count('@') > 2 or text_lower.count('t.me/') > 1:
        return True
    return False

def is_semantically_duplicate(new_text, history_embeddings):
    if not history_embeddings:
        return False, None
        
    new_embedding = model.encode(new_text, convert_to_tensor=True)
    
    for past_emb in history_embeddings:
        cosine_score = util.cos_sim(new_embedding, past_emb)[0][0].item()
        if cosine_score >= SIMILARITY_THRESHOLD:
            return True, new_embedding 
            
    return False, new_embedding

def clean_and_format_text(text):
    """پاکسازی فوق‌پیشرفته متن برای جلوگیری از فاصله‌های اضافه و حذف لینک‌ها"""
    # جدا کردن خطوط بر اساس اینتر
    lines = text.split('\n')
    clean_lines = []
    
    for line in lines:
        # اگر خط شامل کلمات اسپم بود، کاملا رد شود
        if any(spam in line for spam in SPAM_PHRASES):
            continue
            
        # حذف آیدی‌ها و لینک‌های تلگرامی
        line = re.sub(r'@[a-zA-Z0-9_]+', '', line)
        line = re.sub(r'(https?://)?(www\.)?(t\.me|telegram\.me)/[^\s]+', '', line)
        
        # حذف فاصله‌های خالی ابتدا و انتها
        line = line.strip()
        
        # فقط در صورتی که خط خالی نبود اضافه شود
        if line != "":
            clean_lines.append(line)
            
    # چسباندن خطوط فقط با یک اینتر (جلوگیری از ایجاد فاصله‌های وحشتناک بین ایموجی و متن)
    pure_text = '\n'.join(clean_lines)
    
    # قالب و امضای نهایی شما
    footer = "\n━━━━━━━━━━━━\nاخبار دست اول ایران و جهان\n🆔 @VPNine1"
    
    # مدیریت محدودیت ۱۰۲۴ کاراکتری کپشن
    max_len = 1024 - len(footer) - 5 
    if len(pure_text) > max_len:
        pure_text = pure_text[:max_len].rsplit(' ', 1)[0] + "..."
        
    return pure_text + footer

def send_to_telegram(text, image_url, video_url):
    if not BOT_TOKEN or not TARGET_CHANNEL:
        logger.error("اطلاعات ربات (Token/Channel) در سکرت‌ها تنظیم نشده است!")
        return False
        
    try:
        payload = {"chat_id": TARGET_CHANNEL, "caption": text}
        
        if video_url:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
            payload["video"] = video_url
        elif image_url:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload["photo"] = image_url
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload["text"] = text
            del payload["caption"]
            
        response = requests.post(url, data=payload)
        result = response.json()
        
        if not result.get('ok'):
            if result.get('error_code') == 429:
                retry_after = result.get('parameters', {}).get('retry_after', 5)
                logger.warning(f"محدودیت سرعت تلگرام! توقف برای {retry_after} ثانیه...")
                time.sleep(retry_after + 1)
                return send_to_telegram(text, image_url, video_url)
            logger.error(f"خطای تلگرام: {result.get('description')}")
            return False
            
        time.sleep(2) # وقفه برای جلوگیری از بلاک شدن ربات
        return True
    except Exception as e:
        logger.error(f"خطا در ارتباط با API تلگرام: {e}")
        return False

# ==========================================
# ۴. بدنه اصلی برنامه
# ==========================================
def main():
    channels = load_channels()
    if not channels:
        return
        
    history_embeddings = get_history()
    new_posts = []
    total_channels = len(channels)

    logger.info(f"شروع استخراج از {total_channels} کانال خبری...")

    for i, channel in enumerate(channels, 1):
        logger.info(f"[{int((i/total_channels)*100)}%] بررسی کانال: {channel}")
        
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            r = requests.get(f"https://t.me/s/{channel}", headers=headers, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            messages = soup.find_all('div', class_='tgme_widget_message')
            
            for msg in messages[-CHECK_LAST_N_POSTS:]:
                text_div = msg.find('div', class_='tgme_widget_message_text')
                # استفاده از فاصله به جای اینتر برای جلوگیری از جدا شدن ایموجی‌ها در HTML اولیه
                raw_text = text_div.get_text(separator='\n').strip() if text_div else ""
                
                if not raw_text or len(raw_text) < 20 or is_ad(raw_text):
                    continue
                
                is_duplicate, embedding = is_semantically_duplicate(raw_text, history_embeddings)
                
                if is_duplicate:
                    continue
                    
                image_url = None
                photo_wrap = msg.find('a', class_='tgme_widget_message_photo_wrap')
                if photo_wrap:
                    match = re.search(r"background-image:url\('(.+?)'\)", photo_wrap.get('style', ''))
                    if match: image_url = match.group(1)
                    
                video_url = None
                video_wrap = msg.find('video', class_='tgme_widget_message_video')
                if video_wrap:
                    video_url = video_wrap.get('src')
                    
                final_text = clean_and_format_text(raw_text)
                
                new_posts.append({
                    'text': final_text, 
                    'image': image_url, 
                    'video': video_url,
                    'embedding': embedding
                })
                # اضافه کردن موقت به حافظه برای جلوگیری از ارسال دو خبر مشابه در یک اجرا
                history_embeddings.append(embedding) 
                
        except Exception as e:
            logger.error(f"خطا در خواندن کانال {channel}: {e}")
            
    if new_posts:
        logger.info(f"آماده‌سازی {len(new_posts)} خبر جدید برای انتشار...")
        success_count = 0
        for post in new_posts:
            if send_to_telegram(post['text'], post['image'], post['video']):
                success_count += 1
                
        logger.info(f"پایان عملیات: {success_count} خبر با موفقیت منتشر شد.")
        save_history(history_embeddings)
    else:
        logger.info("عملیات پایان یافت: خبر جدید و معتبری یافت نشد.")

if __name__ == "__main__":
    main()
