import os
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import pickle
from sentence_transformers import SentenceTransformer, util

# ==========================================
# ۱. تنظیمات پایه‌ای
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
TARGET_CHANNEL = os.environ.get('TARGET_CHANNEL')

MAX_HISTORY = 150          
CHECK_LAST_N_POSTS = 10    
SIMILARITY_THRESHOLD = 0.92 

CHANNEL_SIGNATURE = "@Khbr24"

AD_KEYWORDS = [
    "تخفیف", "خرید", "فروش", "ارزان", "تبلیغات", "اسپانسر", "کلیک کنید", 
    "ثبت نام", "کسب درآمد", "پراکسی", "proxy", "vpn", "فیلترشکن", 
    "جهت سفارش", "ارز دیجیتال", "ترید", "سیگنال رایگان"
]

SPAM_PHRASES = [
    "لینک گروه", "جوین", "عضو شوید", "کانال ما", "ادامه مطلب", 
    "بیشتر بخوانید", "سابسکرایب", "حمایت از ما", "👇", "👆", "لینک زیر", "آیدی زیر"
]

logger.info("در حال بارگذاری مدل هوش مصنوعی (NLP)...")
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

# ==========================================
# ۲. توابع پردازش متن (فرمت رسمی و بدون ایموجی)
# ==========================================
def extract_text_from_html(text_div):
    if not text_div:
        return ""
    for br in text_div.find_all("br"):
        br.replace_with("\n")
    return text_div.get_text().strip()

def clean_and_format_text(text):
    # حذف تمام ایموجی‌ها و نمادهای غیرمتنی
    # فقط حروف فارسی/انگلیسی، اعداد، نیم‌فاصله و علائم نگارشی استاندارد مجاز هستند
    text = re.sub(r'[^\w\s\u0600-\u06FF\u200C\.\:\-\؛\،\؟\?!\'\"»«\(\)\[\]\/\\\%\+\=\*\#\@\|]', '', text)
    
    lines = text.split('\n')
    clean_lines = []
    
    for line in lines:
        if any(spam in line for spam in SPAM_PHRASES):
            continue
            
        line = re.sub(r'@[a-zA-Z0-9_]+', '', line)
        line = re.sub(r'(https?://)?(www\.)?(t\.me|telegram\.me)/[^\s]+', '', line)
        
        line = line.strip()
        if line != "":
            clean_lines.append(line)
            
    # چسباندن خطوط و تبدیل هرچندتا اینترِ پشت‌سر‌هم به فقط "یک" اینتر (برای فشردگی کامل متن)
    pure_text = '\n'.join(clean_lines)
    pure_text = re.sub(r'\n{2,}', '\n', pure_text).strip()
    
    # قالب نهایی دقیقاً مطابق درخواست شما
    footer = f"\n━━━━━━━━━━━━\n🆔  {CHANNEL_SIGNATURE}"
    
    # محدودیت ۱۰۲۴ کاراکتری کپشن در تلگرام
    max_len = 1024 - len(footer) - 5 
    if len(pure_text) > max_len:
        pure_text = pure_text[:max_len].rsplit(' ', 1)[0] + "..."
        
    return pure_text + footer

# ==========================================
# ۳. توابع ارتباط با تلگرام
# ==========================================
def send_to_telegram(text, image_url, video_url):
    if not BOT_TOKEN or not TARGET_CHANNEL:
        logger.error("توکن یا آیدی کانال تنظیم نشده است!")
        return False
        
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TARGET_CHANNEL}
    files = {}
    media_downloaded = False
    
    try:
        if video_url:
            res = requests.get(video_url, timeout=30)
            if res.status_code == 200:
                files['video'] = ('video.mp4', res.content)
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
                payload['caption'] = text
                media_downloaded = True
                
        elif image_url:
            res = requests.get(image_url, timeout=20)
            if res.status_code == 200:
                files['photo'] = ('image.jpg', res.content)
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                payload['caption'] = text
                media_downloaded = True

        if not media_downloaded:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload['text'] = text

        if files:
            response = requests.post(url, data=payload, files=files)
        else:
            response = requests.post(url, data=payload)
            
        result = response.json()
        
        if not result.get('ok'):
            if result.get('error_code') == 429:
                retry_after = result.get('parameters', {}).get('retry_after', 10)
                logger.warning(f"محدودیت سرعت تلگرام! توقف برای {retry_after} ثانیه...")
                time.sleep(retry_after + 1)
                return send_to_telegram(text, image_url, video_url)
            logger.error(f"خطای تلگرام: {result.get('description')}")
            return False
            
        # تاخیر ۱۰ ثانیه‌ای ثابت برای امنیت ربات
        time.sleep(10) 
        return True
        
    except Exception as e:
        logger.error(f"خطا در ارسال به تلگرام: {e}")
        return False

# ==========================================
# ۴. توابع دیتابیس و هوش مصنوعی
# ==========================================
def load_channels():
    if not os.path.exists('channels.txt'):
        return []
    with open('channels.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def get_history():
    if not os.path.exists('history.pkl'): 
        return []
    try:
        with open('history.pkl', 'rb') as f:
            data = pickle.load(f)
            return [emb for emb in data if emb is not None]
    except:
        return []

def save_history(history_list):
    clean_history = [emb for emb in history_list if emb is not None]
    with open('history.pkl', 'wb') as f:
        pickle.dump(clean_history[-MAX_HISTORY:], f)

def is_semantically_duplicate(new_text, history_embeddings):
    new_embedding = model.encode(new_text, convert_to_tensor=True)
    if not history_embeddings:
        return False, new_embedding
    for past_emb in history_embeddings:
        if past_emb is None: continue
        cosine_score = util.cos_sim(new_embedding, past_emb)[0][0].item()
        if cosine_score >= SIMILARITY_THRESHOLD:
            return True, new_embedding 
    return False, new_embedding

def is_ad(text):
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in AD_KEYWORDS): return True
    text_without_links = re.sub(r'https?://\S+', '', text_lower)
    if text_without_links.count('@') > 2 or text_lower.count('t.me/') > 1: return True
    return False

# ==========================================
# ۵. حلقه اصلی برنامه
# ==========================================
def main():
    channels = load_channels()
    if not channels:
        return
        
    history_embeddings = get_history()
    new_posts = []
    total_channels = len(channels)

    logger.info(f"شروع استخراج از {total_channels} کانال...")

    for i, channel in enumerate(channels, 1):
        logger.info(f"[{int((i/total_channels)*100)}%] بررسی کانال: {channel}")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            r = requests.get(f"https://t.me/s/{channel}", headers=headers, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            messages = soup.find_all('div', class_='tgme_widget_message')
            
            for msg in messages[-CHECK_LAST_N_POSTS:]:
                text_div = msg.find('div', class_='tgme_widget_message_text')
                
                raw_text = extract_text_from_html(text_div)
                
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
                history_embeddings.append(embedding) 
                
        except Exception as e:
            logger.error(f"خطا در خواندن کانال {channel}: {e}")
            
    if new_posts:
        logger.info(f"آماده‌سازی {len(new_posts)} خبر جدید...")
        success_count = 0
        for post in new_posts:
            if send_to_telegram(post['text'], post['image'], post['video']):
                success_count += 1
                
        logger.info(f"پایان عملیات: {success_count} خبر ارسال شد.")
        save_history(history_embeddings)
    else:
        logger.info("عملیات پایان یافت: خبر جدیدی یافت نشد.")

if __name__ == "__main__":
    main()
