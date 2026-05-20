import os
import requests
from bs4 import BeautifulSoup
import re
import time

# ==========================================
# تنظیمات اصلی ربات (دریافت از سکرت‌های گیت‌هاب)
# ==========================================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
TARGET_CHANNEL = os.environ.get('TARGET_CHANNEL')

MAX_HISTORY = 100          
CHECK_LAST_N_POSTS = 10    
DUPLICATE_CHARS = 50 

# کلمات کلیدی برای تشخیص پست‌های تبلیغاتی
AD_KEYWORDS = [
    "تخفیف", "خرید", "فروش", "ارزان", "تبلیغات", "اسپانسر", "کلیک کنید", 
    "ثبت نام", "کسب درآمد", "پراکسی", "proxy", "vpn", "فیلترشکن", 
    "فیلتر شکن", "جهت سفارش", "لینک در بیو", "ارز دیجیتال", "ترید", "سیگنال رایگان"
]
# ==========================================

def load_channels():
    if not os.path.exists('channels.txt'):
        print("Error: channels.txt not found!")
        return []
    with open('channels.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def get_history():
    if not os.path.exists('history.txt'): 
        return []
    with open('history.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines()]

def save_history(history):
    with open('history.txt', 'w', encoding='utf-8') as f:
        for item in history[-MAX_HISTORY:]:
            f.write(item.replace('\n', ' ') + '\n')

def is_ad(text, channel):
    text_lower = text.lower()
    channel_lower = channel.lower()
    
    for keyword in AD_KEYWORDS:
        if keyword in text_lower:
            print(f"-> پست تبلیغاتی مسدود شد (دلیل: {keyword})")
            return True
    
    text_without_self = text_lower.replace(f"@{channel_lower}", "").replace(f"t.me/{channel_lower}", "")
    
    if text_without_self.count('t.me/') > 1 or text_without_self.count('@') > 2:
        print("-> پست تبلیغاتی مسدود شد (دلیل: تگ کانال‌های دیگر)")
        return True
        
    return False

def is_duplicate(new_text, history):
    if not new_text or len(new_text) < 10: 
        return True
    
    snippet = new_text[:DUPLICATE_CHARS].strip()
    for old_text in history:
        if snippet in old_text:
            return True
    return False

def send_to_telegram(text, image_url, video_url):
    if not BOT_TOKEN or not TARGET_CHANNEL:
        print("Error: BOT_TOKEN or TARGET_CHANNEL is missing from Secrets!")
        return
        
    if len(text) > 1024 and (image_url or video_url):
        text = text[:1020] + "..."
        
    try:
        if video_url:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
            payload = {"chat_id": TARGET_CHANNEL, "video": video_url, "caption": text}
        elif image_url:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {"chat_id": TARGET_CHANNEL, "photo": image_url, "caption": text}
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TARGET_CHANNEL, "text": text[:4096]}
            
        response = requests.post(url, data=payload)
        
        if not response.json().get('ok'):
            print(f"Telegram API Error: {response.text}")
            
        time.sleep(2) 
    except Exception as e:
        print(f"Error sending message: {e}")

def main():
    channels = load_channels()
    if not channels:
        return
        
    history = get_history()
    new_posts = []

    for channel in channels:
        print(f"\nChecking {channel}...")
        try:
            r = requests.get(f"https://t.me/s/{channel}")
            soup = BeautifulSoup(r.text, 'html.parser')
            messages = soup.find_all('div', class_='tgme_widget_message')
            
            for msg in messages[-CHECK_LAST_N_POSTS:]:
                text_div = msg.find('div', class_='tgme_widget_message_text')
                text = text_div.get_text(separator='\n').strip() if text_div else ""
                
                if not text or is_ad(text, channel) or is_duplicate(text, history):
                    continue
                    
                image_url = None
                photo_wrap = msg.find('a', class_='tgme_widget_message_photo_wrap')
                if photo_wrap:
                    style = photo_wrap.get('style', '')
                    match = re.search(r"background-image:url\('(.+?)'\)", style)
                    if match: image_url = match.group(1)
                    
                video_url = None
                video_wrap = msg.find('video', class_='tgme_widget_message_video')
                if video_wrap:
                    video_url = video_wrap.get('src')
                    
                new_posts.append({
                    'text': text, 'image': image_url, 'video': video_url
                })
                history.append(text.replace('\n', ' '))
                
        except Exception as e:
            print(f"Error checking {channel}: {e}")
            
    if new_posts:
        print(f"\nSending {len(new_posts)} new posts to Telegram...")
    else:
        print("\nNo new valid posts found.")

    for post in new_posts:
        send_to_telegram(post['text'], post['image'], post['video'])
        
    save_history(history)

if __name__ == "__main__":
    main()
