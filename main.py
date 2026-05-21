import os
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import pickle
from sentence_transformers import SentenceTransformer, util

# ==========================================
# تنظیمات اصلی
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
TARGET_CHANNEL = os.environ.get('TARGET_CHANNEL')
CHANNEL_SIGNATURE = "@YourChannelID" # آیدی کانال خود را اینجا قرار دهید

MAX_HISTORY = 100          
CHECK_LAST_N_POSTS = 10    
SIMILARITY_THRESHOLD = 0.85 # درصد شباهت برای تشخیص خبر تکراری (۸۵ درصد)

AD_KEYWORDS = [
    "تخفیف", "خرید", "فروش", "ارزان", "تبلیغات", "اسپانسر", "کلیک کنید", 
    "ثبت نام", "کسب درآمد", "پراکسی", "proxy", "vpn", "فیلترشکن", 
    "جهت سفارش", "لینک در بیو", "ارز دیجیتال", "ترید", "سیگنال رایگان"
]
# ==========================================

# بارگذاری مدل هوش مصنوعی (در اجرای اول دانلود می‌شود، در دفعات بعد از کش می‌خواند)
logger.info("در حال بارگذاری مدل هوش مصنوعی (NLP)...")
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

def load_channels():
    if not os.path.exists('channels.txt'):
        logger.error("فایل channels.txt پیدا نشد!")
        return []
    with open('channels.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def get_history():
    """خواندن تاریخچه بردارهای معنایی از فایل Pickle"""
    if not os.path.exists('history.pkl'): 
        return []
    try:
        with open('history.pkl', 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        logger.error(f"خطا در خواندن فایل تاریخچه: {e}")
        return []

def save_history(history_list):
    """ذخیره بردارهای جدید (حذف قدیمی‌ها برای جلوگیری از افزایش حجم)"""
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
    """مقایسه معنایی خبر جدید با اخبار قبلی با استفاده از کسینوس شباهت"""
    if not history_embeddings:
        return False, None
        
    # تبدیل متن جدید به بردار ریاضی
    new_embedding = model.encode(new_text, convert_to_tensor=True)
    
    for past_emb in history_embeddings:
        # مقایسه شباهت بردار جدید با بردارهای ذخیره شده
        cosine_score = util.cos_sim(new_embedding, past_emb)[0][0].item()
        if cosine_score >= SIMILARITY_THRESHOLD:
            return True, new_embedding # خبر تکراری است
            
    return False, new_embedding # خبر جدید است

def clean_and_format_text(text):
    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'(https?://)?(www\.)?(t\.me|telegram\.me)/[^\s]+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    footer = f"\n\n━━━━━━━━━━━━\n🆔 {CHANNEL_SIGNATURE}"
    max_len = 1024 - len(footer) - 5 
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0] + "..."
        
    return text + footer

def send_to_telegram(text, image_url, video_url):
    if not BOT_TOKEN or not TARGET_CHANNEL:
        logger.error("اطلاعات ربات ناقص است!")
        return False
        
    try:
        payload = {"chat_id": TARGET_CHANNEL, "caption": text, "parse_mode": "HTML"}
        
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
                time.sleep(retry_after + 1)
                return send_to_telegram(text, image_url, video_url)
            logger.error(f"خطای تلگرام: {result.get('description')}")
            return False
            
        time.sleep(2)
        return True
    except Exception as e:
        logger.error(f"خطا در ارسال: {e}")
        return False

def main():
    channels = load_channels()
    if not channels:
        return
        
    history_embeddings = get_history()
    new_posts = []

    for channel in channels:
        logger.info(f"در حال بررسی {channel}...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            r = requests.get(f"https://t.me/s/{channel}", headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            messages = soup.find_all('div', class_='tgme_widget_message')
            
            for msg in messages[-CHECK_LAST_N_POSTS:]:
                text_div = msg.find('div', class_='tgme_widget_message_text')
                raw_text = text_div.get_text(separator='\n').strip() if text_div else ""
                
                if not raw_text or len(raw_text) < 20 or is_ad(raw_text):
                    continue
                
                # بررسی تکراری بودن بر اساس معنا و لحن
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
                # اضافه کردن موقت به حافظه برای مقایسه با خبرهای بعدی در همین حلقه
                history_embeddings.append(embedding) 
                
        except Exception as e:
            logger.error(f"خطا در کانال {channel}: {e}")
            
    if new_posts:
        logger.info(f"ارسال {len(new_posts)} پست جدید...")
        for post in new_posts:
            send_to_telegram(post['text'], post['image'], post['video'])
            
        save_history(history_embeddings)
    else:
        logger.info("پست جدیدی یافت نشد.")

if __name__ == "__main__":
    main()
