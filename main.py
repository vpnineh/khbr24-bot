import os
import json
import time
import logging
import cloudscraper
import html
import re
import concurrent.futures
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from dateutil import parser

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'ایران OR خاورمیانه OR آمریکا OR اسرائیل OR اقتصاد OR دلار OR فناوری OR هوش مصنوعی',
    'TARGET_SOURCES': [
        'iranintl.com', 'bbc.com/persian', 'radiofarda.com', 'independentpersian.com',
        'dw.com/fa', 'euronews.com/pe', 'digiato.com', 'tejaratnews.com'
    ],
    'FILES': {
        'NEWS': 'news_exclusive.json',
    },
    'TELEGRAM': {
        'BOT_TOKEN': os.environ.get('BOT_TOKEN'), 
        'CHANNEL_ID': os.environ.get('TARGET_CHANNEL') 
    },
    'TIMEOUT': 15, # کاهش تایم‌اوت برای افزایش سرعت
    'MAX_WORKERS': 8, # افزایش شدید مالتی‌تسکینگ (۸ پردازش همزمان)
    'AI_RETRIES': 2,
    'MIN_IMPORTANCE': 3,
    'MAX_NEWS_AGE_HOURS': 24,
    'HISTORY_SIZE': 300
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class ExclusiveNewsRadar:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(browser='chrome') 
        self.existing_news = self._load_existing_news()
        self.seen_urls = set()
        self.seen_titles = set()
        
        for item in self.existing_news:
            if item.get('url'):
                self.seen_urls.add(self._clean_url(item['url']))
            if item.get('title_fa'):
                self.seen_titles.add(self._normalize_text(item['title_fa']))
                
    def _clean_url(self, url):
        if not url: return ""
        try:
            parsed = urlparse(url)
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
            return clean.rstrip('/')
        except: return url

    def _normalize_text(self, text):
        if not text: return ""
        return re.sub(r'\W+', '', text).lower()

    def _load_existing_news(self):
        if not os.path.exists(CONFIG['FILES']['NEWS']): return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except: return []

    def fetch_duckduckgo(self, query):
        results = []
        try:
            ddgs = DDGS()
            ddg_gen = ddgs.news(keywords=query, region='wt-wt', safesearch="off", timelimit="d", max_results=7)
            for r in ddg_gen:
                results.append({
                    'title': r.get('title'),
                    'url': r.get('url'),
                    'description': r.get('body'),
                    'image': r.get('image'),
                    'published date': r.get('date')
                })
        except Exception as e: logger.error(f"DDG Error: {e}")
        return results

    def get_combined_news(self):
        all_entries = []
        for domain in CONFIG['TARGET_SOURCES']: 
            try:
                query = f"site:{domain} (ایران OR جنگ OR اقتصاد OR فناوری OR سیاسی)"
                site_res = self.fetch_duckduckgo(query)
                all_entries.extend(site_res)
            except: pass
        return all_entries

    def scrape_article_text(self, final_url, fallback_snippet):
        try:
            if final_url.lower().endswith('.pdf'): return fallback_snippet, None
            resp = self.scraper.get(final_url, timeout=CONFIG['TIMEOUT'])
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]): tag.extract()
            
            og_img = soup.find("meta", property="og:image")
            img_url = og_img["content"] if og_img else None

            article_body = soup.find('div', class_=re.compile(r'(article|story|body|content|news-text)'))
            if article_body:
                text = article_body.get_text(separator=' ').strip()
            else:
                text = " ".join([p.get_text().strip() for p in soup.find_all('p')])
            
            clean_text = re.sub(r'\s+', ' ', text)
            return clean_text[:1500] if len(clean_text) > 100 else fallback_snippet, img_url
        except: return fallback_snippet, None

    def analyze_with_ai(self, headline, full_text):
        system_prompt = (
            "You are a modern, smart, and exclusive news curator for a premium Persian Telegram channel. "
            "You read news and summarize them for your audience.\n\n"
            "RULES:\n"
            "- Tone: Conversational, friendly, yet highly professional and accurate (خودمونی ولی تخصصی). Speak like a smart friend breaking down complex news.\n"
            "- No Clichés: Avoid generic news phrases.\n"
            "- Never mention the source or publisher.\n"
            "- Summary: EXACTLY 1 or 2 highly impactful sentences. Get straight to the point.\n"
            "- Title: Catchy, bold, and modern.\n"
            "- Importance: Score 1 to 5.\n\n"
            "JSON OUTPUT FORMAT STRICTLY:\n"
            '{"title_fa": "Title", "summary": "Conversational summary.", "importance": integer, "tags": ["tag1", "tag2"]}'
        )

        for attempt in range(CONFIG['AI_RETRIES']):
            try:
                resp = self.scraper.post(
                    "https://text.pollinations.ai/openai",
                    json={
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"HEADLINE: {headline}\nTEXT: {full_text[:1000]}"}
                        ],
                        "temperature": 0.3,
                        "jsonMode": True
                    }, timeout=25
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if 'title_fa' in data and 'summary' in data: return data
            except Exception as e:
                logger.error(f"AI Error: {e}")
        return None

    def process_item(self, entry):
        raw_title = entry.get('title', '').rsplit(' - ', 1)[0].strip()
        final_url = entry.get('url')
        clean_final_url = self._clean_url(final_url)

        if clean_final_url in self.seen_urls or self._normalize_text(raw_title) in self.seen_titles:
            return None
        
        text, scraped_img = self.scrape_article_text(final_url, entry.get('description', raw_title))
        best_image = scraped_img if scraped_img else entry.get('image')

        ai = self.analyze_with_ai(raw_title, text)
        if not ai: return None
        
        if int(ai.get('importance', 1)) < CONFIG['MIN_IMPORTANCE']:
            return None 

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa', raw_title),
            "summary": ai.get('summary', ''),
            "tags": ai.get('tags', []),
            "importance": int(ai.get('importance', 1)),
            "url": final_url, 
            "clean_url": clean_final_url, 
            "image": best_image,
            "timestamp": ts
        }

    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id: return

        for item in items:
            title = html.escape(str(item.get('title_fa', '')))
            summary = html.escape(str(item.get('summary', '')))
            image_url = item.get('image')
            
            tags = item.get('tags', [])
            tags_str = " ".join([f"#{t.replace(' ', '_')}" for t in set(tags)])

            caption = (
                f"⚡️ <b>{title}</b>\n\n"
                f"💬 {summary}\n\n"
                f"🏷 {tags_str}\n\n"
                f"🆔 @khbr24"
            )

            payload = {
                "chat_id": chat_id, 
                "parse_mode": "HTML",
                "caption": caption[:1024]
            }

            try:
                if image_url and image_url.startswith('http'):
                    api_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    payload["photo"] = image_url
                    resp = self.scraper.post(api_url, json=payload)
                    if resp.status_code != 200: raise Exception("Photo fail")
                else: raise Exception("No image")
            except:
                api_url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload.pop("photo", None)
                payload.pop("caption", None)
                payload["text"] = caption
                payload["disable_web_page_preview"] = True
                self.scraper.post(api_url, json=payload)
            
            time.sleep(1) # کاهش تاخیر بین پیام‌ها

    def save_news(self, new_items):
        all_news = new_items + self.existing_news
        seen_u = set()
        unique_news = []
        for item in all_news:
            u = item.get('clean_url')
            if u and u not in seen_u:
                seen_u.add(u)
                unique_news.append(item)
        
        unique_news.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        final_list = unique_news[:CONFIG['HISTORY_SIZE']]
        
        with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
            json.dump(final_list, f, indent=4, ensure_ascii=False)
        return final_list

    def run(self):
        logger.info(">>> Fast Radar Started...")
        results = self.get_combined_news()
        candidates = []
        
        for item in results:
            clean_u = self._clean_url(item.get('url', ''))
            if clean_u in self.seen_urls: continue
            candidates.append(item)

        new_processed_items = []
        if candidates:
            # استفاده از پردازش موازی بالا (۸ تسک همزمان)
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
                futures = {exc.submit(self.process_item, i): i for i in candidates}
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res:
                        new_processed_items.append(res)
                        self.seen_urls.add(res['clean_url'])

        if new_processed_items:
            self.existing_news = self.save_news(new_processed_items)
            new_processed_items.sort(key=lambda x: x['importance'], reverse=True)
            self.send_digest_to_telegram(new_processed_items)
            logger.info(f">>> {len(new_processed_items)} items published.")
        else:
            logger.info(">>> No new valid items.")

if __name__ == "__main__":
    ExclusiveNewsRadar().run()
