import asyncio
import logging
import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

import requests
import pytz
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError, Forbidden, BadRequest

# ========= Settings =========
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("silver-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "YOUR_GROUP_CHAT_ID")
PRICE_URL = "https://giabac.phuquygroup.vn/"
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")
PORT = int(os.environ.get("PORT", 8000))
POLL_SECONDS = int(str(os.environ.get("POLL_SECONDS", "60")).strip().strip('"').strip("'"))

# Persist file (on Railway code dir). You can change to "/mnt/data/subscribers.json"
SUBS_FILE = Path("/app/subscribers.json")

def load_subscribers() -> set[int]:
    try:
        if SUBS_FILE.exists():
            data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
            subs = set(int(x) for x in data)
            logger.info("✅ Loaded %d subscribers from %s", len(subs), SUBS_FILE)
            return subs
    except Exception as e:
        logger.error("❌ Load subs error: %s", e)
    return set()

def save_subscribers(subs: set[int]) -> None:
    try:
        SUBS_FILE.write_text(json.dumps(sorted(list(subs))), encoding="utf-8")
        logger.info("💾 Saved %d subscribers to %s", len(subs), SUBS_FILE)
    except Exception as e:
        logger.error("❌ Save subs error: %s", e)

class SilverPriceBot:
    def __init__(self):
        self.price_history = []   # list[{"timestamp": dt, "prices": {...}}]
        self.subscribers = load_subscribers()
        self.last_prices: Dict[str, Dict] = {}
        self.application = None
        self.monitoring_task = None
        self.group_notification_enabled = True  # Flag để bật/tắt thông báo group

    # -------- scraping --------
    async def fetch_silver_prices(self) -> Dict[str, Dict]:
        """
        Fetch & parse prices from website.
        Sử dụng nhiều phương pháp để lấy dữ liệu tươi mới.
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Method 1: Thử với cache-busting và headers mạnh mẽ hơn
            timestamp = int(datetime.now().timestamp() * 1000)
            cache_bust_url = f"{PRICE_URL}?_t={timestamp}&refresh=1"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "DNT": "1",
            }
            
            # Tạo session mới mỗi lần để tránh cache
            session = requests.Session()
            session.headers.update(headers)
            
            resp = await loop.run_in_executor(
                None, 
                lambda: session.get(
                    cache_bust_url, 
                    timeout=20,
                    allow_redirects=True
                )
            )
            
            if resp.status_code == 200:
                logger.info("✅ Successfully fetched data from %s", cache_bust_url)
                prices = self.parse_prices(resp.text)
                
                # Nếu không parse được dữ liệu, thử method 2
                if not prices:
                    logger.warning("⚠️ Method 1 failed, trying alternative approach...")
                    return await self.fetch_prices_alternative()
                
                return prices
            else:
                logger.error("❌ HTTP %s when fetching prices", resp.status_code)
                # Thử method alternative
                return await self.fetch_prices_alternative()
                
        except Exception as e:
            logger.exception("❌ Fetch error (Method 1): %s", e)
            # Fallback to alternative method
            return await self.fetch_prices_alternative()
    
    async def fetch_prices_alternative(self) -> Dict[str, Dict]:
        """
        Alternative method: Thử lấy dữ liệu với cách khác
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Method 2: Simulate browser behavior more closely
            session = requests.Session()
            
            # First request to get cookies/session
            headers1 = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            
            # Đôi khi cần request trang chính trước
            main_resp = await loop.run_in_executor(
                None, 
                lambda: session.get(PRICE_URL, headers=headers1, timeout=15)
            )
            
            # Đợi một chút để simulate human behavior
            await asyncio.sleep(1)
            
            # Request lại với headers khác
            headers2 = headers1.copy()
            headers2.update({
                "Cache-Control": "max-age=0",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Referer": PRICE_URL,
            })
            
            timestamp = int(datetime.now().timestamp())
            refresh_url = f"{PRICE_URL}?v={timestamp}"
            
            resp = await loop.run_in_executor(
                None, 
                lambda: session.get(refresh_url, headers=headers2, timeout=15)
            )
            
            if resp.status_code == 200:
                logger.info("✅ Alternative method successful")
                return self.parse_prices(resp.text)
            else:
                logger.error("❌ Alternative method HTTP %s", resp.status_code)
                return {}
                
        except Exception as e:
            logger.exception("❌ Alternative fetch error: %s", e)
            # Method 3: Last resort - try different URL patterns
            return await self.fetch_prices_last_resort()
    
    async def fetch_prices_last_resort(self) -> Dict[str, Dict]:
        """
        Last resort: Thử các URL pattern khác có thể có
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Các URL có thể thử
            alternative_urls = [
                f"{PRICE_URL}index.html",
                f"{PRICE_URL}home",
                f"{PRICE_URL}?nocache={int(datetime.now().timestamp())}",
                PRICE_URL.rstrip('/') + '/',
            ]
            
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 "
                              "(KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
                "Accept": "*/*",
                "Accept-Language": "vi-VN,vi;q=0.9",
                "Cache-Control": "no-cache",
            }
            
            for url in alternative_urls:
                try:
                    logger.info("🔄 Trying URL: %s", url)
                    resp = await loop.run_in_executor(
                        None, 
                        lambda u=url: requests.get(u, headers=headers, timeout=10)
                    )
                    
                    if resp.status_code == 200:
                        prices = self.parse_prices(resp.text)
                        if prices:
                            logger.info("✅ Success with URL: %s", url)
                            return prices
                            
                except Exception as e:
                    logger.debug("URL %s failed: %s", url, e)
                    continue
            
            logger.error("❌ All methods failed to fetch prices")
            return {}
            
        except Exception as e:
            logger.exception("❌ Last resort error: %s", e)
            return {}

    def parse_prices(self, html: str) -> Dict[str, Dict]:
        """
        Parse prices from HTML with enhanced detection.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            prices: Dict[str, Dict] = {}
            now = datetime.now(VN_TZ)
            
            logger.debug("HTML content length: %d chars", len(html))
            
            # Method 1: Tìm trong table
            tables_found = 0
            rows_processed = 0
            
            for table in soup.find_all("table"):
                tables_found += 1
                for row in table.find_all("tr"):
                    rows_processed += 1
                    tds = row.find_all("td")
                    if len(tds) < 3:  # Giảm yêu cầu xuống 3 cột
                        continue
                        
                    # Lấy text từ các cột
                    texts = [td.get_text(strip=True) for td in tds]
                    product = texts[0]
                    
                    # Kiểm tra nếu là sản phẩm bạc
                    if not any(keyword in product.upper() for keyword in ["BẠC", "SILVER", "AG"]):
                        continue
                    
                    logger.debug("Found silver product: %s with %d columns", product, len(texts))
                    
                    # Parse giá - flexible column detection
                    unit = texts[1] if len(texts) > 1 else ""
                    buy_price = 0
                    sell_price = 0
                    
                    # Tìm cột có giá (số có dấu phẩy hoặc chấm)
                    for i, text in enumerate(texts[1:], 1):  # Bỏ qua cột đầu (tên sản phẩm)
                        price_val = self._parse_price_num(text)
                        if price_val > 0:
                            if buy_price == 0:
                                buy_price = price_val
                                logger.debug("Buy price found in column %d: %s", i, text)
                            elif sell_price == 0:
                                sell_price = price_val
                                logger.debug("Sell price found in column %d: %s", i, text)
                            break
                    
                    if buy_price > 0:
                        prices[product] = {
                            "unit": unit,
                            "buy_price": buy_price,
                            "sell_price": sell_price if sell_price > 0 else None,
                            "timestamp": now,
                        }
                        logger.debug("✅ Added %s: buy=%d, sell=%s", 
                                   product, buy_price, sell_price or "None")
            
            logger.info("Parse summary: %d tables, %d rows, %d silver products found", 
                       tables_found, rows_processed, len(prices))
            
            # Method 2: Nếu không tìm thấy trong table, thử tìm trong div/span
            if not prices:
                logger.warning("No prices found in tables, trying alternative parsing...")
                prices = self.parse_prices_alternative(soup, now)
            
            # Method 3: Nếu vẫn không có, check xem có phải trang cần JavaScript không
            if not prices:
                self.diagnose_page_content(html)
            
            return prices
            
        except Exception as e:
            logger.exception("❌ Parse error: %s", e)
            return {}
    
    def parse_prices_alternative(self, soup, now) -> Dict[str, Dict]:
        """Alternative parsing method for different HTML structures"""
        prices = {}
        try:
            # Tìm tất cả text chứa "bạc" và số
            all_text = soup.get_text()
            lines = [line.strip() for line in all_text.split('\n') if line.strip()]
            
            current_product = None
            for i, line in enumerate(lines):
                if any(keyword in line.upper() for keyword in ["BẠC", "SILVER", "AG"]):
                    current_product = line
                    logger.debug("Found potential product: %s", line)
                    
                    # Tìm giá trong 3 dòng tiếp theo
                    for j in range(i+1, min(i+4, len(lines))):
                        price_val = self._parse_price_num(lines[j])
                        if price_val > 0:
                            prices[current_product] = {
                                "unit": "",
                                "buy_price": price_val,
                                "sell_price": None,
                                "timestamp": now,
                            }
                            logger.debug("✅ Alternative method found %s: %d", current_product, price_val)
                            break
            
            return prices
        except Exception as e:
            logger.error("Alternative parsing failed: %s", e)
            return {}
    
    def diagnose_page_content(self, html: str):
        """Diagnose page content to understand why parsing failed"""
        try:
            logger.info("🔍 Diagnosing page content...")
            
            # Check for common indicators
            indicators = {
                "JavaScript required": ["javascript", "js", "script"],
                "Loading placeholder": ["loading", "đang tải", "please wait"],
                "Error page": ["error", "404", "not found", "lỗi"],
                "Redirect": ["redirect", "chuyển hướng"],
            }
            
            html_lower = html.lower()
            for category, keywords in indicators.items():
                if any(keyword in html_lower for keyword in keywords):
                    logger.warning("⚠️ Page contains %s indicators", category)
            
            # Log sample content
            soup = BeautifulSoup(html, "html.parser")
            text_content = soup.get_text()[:500]  # First 500 chars
            logger.info("📝 Sample content: %s", text_content)
            
            # Count tables and rows
            tables = soup.find_all("table")
            logger.info("📊 Found %d tables", len(tables))
            
            if tables:
                for i, table in enumerate(tables[:3]):  # Check first 3 tables
                    rows = table.find_all("tr")
                    logger.info("Table %d: %d rows", i+1, len(rows))
                    
                    if rows:
                        first_row = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
                        logger.info("First row: %s", first_row)
            
        except Exception as e:
            logger.error("Diagnosis failed: %s", e)

    @staticmethod
    def _parse_price_num(price_str: str) -> int:
        if not price_str or price_str == "-":
            return 0
        numbers = re.findall(r"\d+", price_str.replace(",", "").replace(".", ""))
        return int("".join(numbers)) if numbers else 0

    @staticmethod
    def fmt(price: int) -> str:
        return f"{price:,}".replace(",", ".")

    @staticmethod
    def spread(buy: int, sell: int | None) -> Tuple[int, float]:
        if sell and sell > 0 and buy > 0:
            sp = sell - buy
            pct = sp / buy * 100
            return sp, pct
        return 0, 0.0

    # -------- IMPROVED GROUP NOTIFICATIONS --------
    async def send_to_group(self, text: str) -> bool:
        """
        Gửi tin nhắn lên group với error handling tốt hơn
        Returns True nếu gửi thành công, False nếu thất bại
        """
        if not self.group_notification_enabled:
            logger.info("Group notifications disabled")
            return False
            
        if not GROUP_CHAT_ID or GROUP_CHAT_ID == "YOUR_GROUP_CHAT_ID":
            logger.warning("GROUP_CHAT_ID not configured")
            return False
            
        if not self.application:
            logger.error("Bot application not initialized")
            return False

        try:
            await self.application.bot.send_message(
                GROUP_CHAT_ID, 
                text, 
                parse_mode="Markdown",
                disable_web_page_preview=True  # Tránh preview link
            )
            logger.info("✅ Sent notification to group: %s", GROUP_CHAT_ID)
            return True
            
        except Forbidden as e:
            logger.error("❌ Bot bị kick khỏi group hoặc không có quyền gửi tin nhắn: %s", e)
            self.group_notification_enabled = False  # Tạm tắt để tránh spam log
            return False
            
        except BadRequest as e:
            logger.error("❌ Group Chat ID không hợp lệ: %s", e)
            return False
            
        except TelegramError as e:
            logger.error("❌ Lỗi Telegram khi gửi lên group: %s", e)
            return False
            
        except Exception as e:
            logger.error("❌ Unexpected error sending to group: %s", e)
            return False

    async def notify_change(self, product: str, prev: Dict, cur: Dict):
        """
        Notify immediately when price changes (buy and/or sell).
        ALWAYS sends to group first, then to subscribers.
        """
        prev_buy = prev.get("buy_price", 0)
        cur_buy = cur.get("buy_price", 0)
        prev_sell = prev.get("sell_price", None)
        cur_sell = cur.get("sell_price", None)

        # Tính toán thay đổi
        delta_buy = cur_buy - prev_buy
        pct = (delta_buy / prev_buy * 100) if prev_buy else 0.0
        emoji = "📈" if delta_buy > 0 else "📉" if delta_buy < 0 else "↔️"

        # Tạo message
        lines = [
            "🚨 *CẢNH BÁO: GIÁ BẠC THAY ĐỔI* 🚨",
            f"\n🔸 *{product}*",
            f"💵 Mua: {self.fmt(prev_buy)} ➜ {self.fmt(cur_buy)} VND ({pct:+.2f}%) {emoji}",
        ]
        
        # Thêm thông tin giá bán nếu có
        if prev_sell is not None or cur_sell is not None:
            prev_sell_txt = self.fmt(prev_sell) if prev_sell else "—"
            cur_sell_txt = self.fmt(cur_sell) if cur_sell else "—"
            if prev_sell and cur_sell:
                sell_delta = cur_sell - prev_sell
                sell_pct = (sell_delta / prev_sell * 100) if prev_sell else 0.0
                sell_emoji = "📈" if sell_delta > 0 else "📉" if sell_delta < 0 else "↔️"
                lines.append(f"💴 Bán: {prev_sell_txt} ➜ {cur_sell_txt} VND ({sell_pct:+.2f}%) {sell_emoji}")
            else:
                lines.append(f"💴 Bán: {prev_sell_txt} ➜ {cur_sell_txt} VND")

        # Thêm chênh lệch hiện tại
        sp, spct = self.spread(cur_buy, cur_sell)
        if sp:
            lines.append(f"📊 Chênh lệch: {self.fmt(sp)} VND ({spct:.2f}%)")

        lines.append(f"\n🕐 {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}")
        lines.append(f"🔄 Kiểm tra mỗi {POLL_SECONDS}s")
        
        msg = "\n".join(lines)

        # GỬI LÊN GROUP TRƯỚC (ưu tiên cao nhất)
        group_sent = await self.send_to_group(msg)
        if group_sent:
            logger.info("🎯 Price change notification sent to group successfully")
        else:
            logger.warning("⚠️ Failed to send notification to group")

        # Sau đó gửi cho subscribers cá nhân
        failed_subscribers = []
        successful_sends = 0
        
        for uid in self.subscribers.copy():
            try:
                await self.application.bot.send_message(
                    uid, 
                    msg, 
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                successful_sends += 1
            except (Forbidden, BadRequest):
                # User đã block bot hoặc xóa chat
                failed_subscribers.append(uid)
            except Exception as e:
                logger.error("Error sending to subscriber %s: %s", uid, e)
                failed_subscribers.append(uid)

        # Xóa subscribers không active
        if failed_subscribers:
            for uid in failed_subscribers:
                self.subscribers.discard(uid)
            save_subscribers(self.subscribers)
            logger.info("Removed %d inactive subscribers", len(failed_subscribers))

        logger.info("📱 Sent to %d subscribers successfully", successful_sends)

    # -------- monitor loop --------
    async def monitor_loop(self):
        logger.info("🔄 Start monitoring every %ss ...", POLL_SECONDS)
        consecutive_errors = 0
        max_consecutive_errors = 3  # Giảm xuống để phản ứng nhanh hơn
        last_successful_fetch = None
        
        while True:
            try:
                logger.info("🔄 Fetching silver prices... (attempt %d)", consecutive_errors + 1)
                current = await self.fetch_silver_prices()
                
                if current:
                    consecutive_errors = 0  # Reset error counter
                    last_successful_fetch = datetime.now(VN_TZ)
                    
                    # Save history
                    self.price_history.append({
                        "timestamp": datetime.now(VN_TZ), 
                        "prices": current.copy()
                    })
                    self.price_history = self.price_history[-200:]  # Keep last 200 records

                    # Compare & notify - ĐÂY LÀ PHẦN QUAN TRỌNG NHẤT
                    await self.compare_and_notify(current)

                    # Update last prices
                    self.last_prices = current.copy()
                    logger.info("✅ Monitor cycle completed - tracking %d products", len(current))
                    
                    # Log product names for debugging
                    product_names = list(current.keys())
                    logger.debug("Products: %s", product_names)
                    
                else:
                    consecutive_errors += 1
                    logger.warning("⚠️ No price data fetched (attempt %d/%d)", 
                                 consecutive_errors, max_consecutive_errors)
                    
                    if consecutive_errors >= max_consecutive_errors:
                        # Send error notification to group
                        time_since_last = "chưa bao giờ" if not last_successful_fetch else \
                                        str(datetime.now(VN_TZ) - last_successful_fetch)
                        
                        error_msg = (
                            "🚨 *CẢNH BÁO HỆ THỐNG*\n\n"
                            f"❌ Bot không thể lấy dữ liệu giá bạc sau {consecutive_errors} lần thử\n"
                            f"⏰ Lần cuối thành công: {time_since_last} trước\n"
                            f"🌐 Website: {PRICE_URL}\n"
                            f"🕐 {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}\n\n"
                            "🔧 *Có thể do:*\n"
                            "• Website cần JavaScript để load dữ liệu\n"
                            "• Website thay đổi cấu trúc HTML\n" 
                            "• Kết nối mạng gặp vấn đề\n"
                            "• Website tạm thời không truy cập được\n\n"
                            "🔄 Bot sẽ tiếp tục thử..."
                        )
                        await self.send_to_group(error_msg)
                        
                        # Reset consecutive errors để không spam notifications
                        consecutive_errors = max_consecutive_errors - 1

                await asyncio.sleep(POLL_SECONDS)
                
            except Exception as e:
                consecutive_errors += 1
                logger.exception("❌ Monitor loop error (attempt %d): %s", consecutive_errors, e)
                
                if consecutive_errors >= max_consecutive_errors:
                    error_msg = (
                        f"🚨 *LỖI HỆ THỐNG NGHIÊM TRỌNG*\n\n"
                        f"💥 Bot gặp lỗi: {str(e)[:150]}...\n"
                        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}\n\n"
                        "🔄 Bot sẽ tiếp tục thử sau 60 giây..."
                    )
                    await self.send_to_group(error_msg)
                
                # Wait longer on consecutive errors
                wait_time = min(30 * consecutive_errors, 300)  # Max 5 minutes
                logger.info("😴 Waiting %d seconds before retry...", wait_time)
                await asyncio.sleep(wait_time)

    async def compare_and_notify(self, current: Dict[str, Dict]):
        """
        Compare current vs last prices; notify immediately if ANY change detected.
        """
        if not self.last_prices:
            logger.info("🏁 First run - establishing baseline with %d products", len(current))
            return  # First run: establish baseline

        changes_detected = 0
        
        # Kiểm tra sản phẩm hiện tại
        for product, cur in current.items():
            if product not in self.last_prices:
                # Sản phẩm mới xuất hiện
                logger.info("🆕 New product detected: %s", product)
                await self.notify_change(product, {"buy_price": 0, "sell_price": None}, cur)
                changes_detected += 1
                continue

            prev = self.last_prices[product]
            
            # Kiểm tra thay đổi giá mua HOẶC giá bán
            buy_changed = cur["buy_price"] != prev.get("buy_price", 0)
            sell_changed = (cur.get("sell_price") or 0) != (prev.get("sell_price") or 0)
            
            if buy_changed or sell_changed:
                logger.info("📊 Price change detected for %s: buy %s, sell %s", 
                           product, 
                           "changed" if buy_changed else "unchanged",
                           "changed" if sell_changed else "unchanged")
                await self.notify_change(product, prev, cur)
                changes_detected += 1

        # Kiểm tra sản phẩm bị xóa
        for product in self.last_prices:
            if product not in current:
                logger.info("❌ Product removed: %s", product)
                # Có thể gửi thông báo sản phẩm bị xóa nếu muốn

        if changes_detected > 0:
            logger.info("🎯 Total price changes detected and notified: %d", changes_detected)
        else:
            logger.debug("✅ No price changes detected")

# ========= Global bot instance =========
bot = SilverPriceBot()

# ========= Telegram handlers =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🏦 *Bot Giá Bạc Tự Động* 🤖\n\n"
        "Bot theo dõi liên tục và *báo ngay lập tức* khi giá thay đổi!\n\n"
        "📋 *Lệnh có sẵn:*\n"
        "• /price - Xem giá hiện tại\n"
        "• /subscribe - Đăng ký nhận cảnh báo\n"
        "• /unsubscribe - Hủy đăng ký\n"
        "• /status - Trạng thái bot\n"
        "• /test - Test thông báo group\n\n"
        f"🔄 *Kiểm tra mỗi:* {POLL_SECONDS} giây\n"
        "🎯 *Báo cáo:* Group + subscribers"
    )
    kb = [
        [InlineKeyboardButton("📈 Giá hiện tại", callback_data="price"),
         InlineKeyboardButton("🔔 Đăng ký", callback_data="subscribe")],
        [InlineKeyboardButton("🔕 Hủy đăng ký", callback_data="unsubscribe"),
         InlineKeyboardButton("📊 Trạng thái", callback_data="status")],
    ]
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang lấy dữ liệu giá bạc...")
    prices = await bot.fetch_silver_prices()
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu. Vui lòng thử lại sau.")
        return

    lines = ["💰 *GIÁ BẠC HIỆN TẠI*\n"]
    for product, d in prices.items():
        lines.append(f"🔸 *{product}* ({d['unit']})")
        lines.append(f"   💵 Mua: {bot.fmt(d['buy_price'])} VND")
        if d["sell_price"]:
            sp, pct = bot.spread(d['buy_price'], d['sell_price'])
            lines.append(f"   💴 Bán: {bot.fmt(d['sell_price'])} VND")
            lines.append(f"   📊 Chênh lệch: {bot.fmt(sp)} VND ({pct:.2f}%)")
        lines.append("")
    lines.append(f"🕐 Cập nhật: {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}")
    lines.append(f"🔄 Tự động kiểm tra mỗi {POLL_SECONDS}s")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to check website content"""
    await update.message.reply_text("🔍 Debugging website content...")
    
    try:
        # Fetch raw HTML
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cache-Control": "no-cache",
        }
        resp = requests.get(PRICE_URL, headers=headers, timeout=10)
        
        # Basic info
        info = [
            f"🌐 *Website Debug Info*",
            f"URL: {PRICE_URL}",
            f"Status: {resp.status_code}",
            f"Content-Length: {len(resp.text)} chars",
        ]
        
        # Check for JavaScript indicators
        html_lower = resp.text.lower()
        if "script" in html_lower:
            info.append("⚠️ Contains JavaScript")
        if "loading" in html_lower or "đang tải" in html_lower:
            info.append("⚠️ Contains loading indicators")
            
        # Parse and count elements
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        info.append(f"📊 Tables found: {len(tables)}")
        
        # Sample content
        text_sample = soup.get_text()[:200].replace('\n', ' ')
        info.append(f"📝 Sample: {text_sample}")
        
        await update.message.reply_text("\n".join(info))
        
        # Try to parse prices
        prices = bot.parse_prices(resp.text)
        if prices:
            price_info = [f"✅ *Found {len(prices)} products:*"]
            for name, data in list(prices.items())[:5]:  # Show first 5
                price_info.append(f"• {name}: {bot.fmt(data['buy_price'])}")
            await update.message.reply_text("\n".join(price_info))
        else:
            await update.message.reply_text("❌ No silver prices detected")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Debug failed: {str(e)}")

async def cmd_force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force check prices immediately"""
    await update.message.reply_text("🔄 Force checking prices...")
    
    try:
        current = await bot.fetch_silver_prices()
        if current:
            # Compare with last prices if available
            if bot.last_prices:
                await bot.compare_and_notify(current)
                await update.message.reply_text(f"✅ Force check completed! Found {len(current)} products.")
            else:
                bot.last_prices = current.copy()
                await update.message.reply_text(f"✅ Baseline established with {len(current)} products.")
        else:
            await update.message.reply_text("❌ No prices fetched. Check /debug for details.")
    except Exception as e:
        await update.message.reply_text(f"❌ Force check failed: {str(e)}")

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot.subscribers.add(uid)
    save_subscribers(bot.subscribers)
    await update.message.reply_text(
        "🔔 *Đã đăng ký thành công!*\n\n"
        "Bạn sẽ nhận được thông báo ngay khi giá bạc thay đổi.\n"
        f"👥 Tổng subscribers: {len(bot.subscribers)}"
    )

async def cmd_unsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot.subscribers.discard(uid)
    save_subscribers(bot.subscribers)
    await update.message.reply_text("🔕 Đã hủy đăng ký thành công.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        f"🤖 *TRẠNG THÁI BOT*\n\n"
        f"📊 Lịch sử: {len(bot.price_history)} records\n"
        f"👥 Subscribers: {len(bot.subscribers)} users\n"
        f"⏱️ Polling: mỗi {POLL_SECONDS}s\n"
        f"📡 Group notifications: {'✅' if bot.group_notification_enabled else '❌'}\n"
        f"🏷️ Products tracking: {len(bot.last_prices)}\n\n"
        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}"
    )
    await update.message.reply_text(txt, parse_mode="Markdown")

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if q.data == "price":
        await cmd_price(update, context)
    elif q.data == "subscribe":
        uid = q.from_user.id
        bot.subscribers.add(uid)
        save_subscribers(bot.subscribers)
        await q.edit_message_text(
            f"🔔 Đã đăng ký thành công!\n\n"
            f"👥 Tổng subscribers: {len(bot.subscribers)}"
        )
    elif q.data == "unsubscribe":
        uid = q.from_user.id
        bot.subscribers.discard(uid)
        save_subscribers(bot.subscribers)
        await q.edit_message_text("🔕 Đã hủy đăng ký thành công.")
    elif q.data == "status":
        await cmd_status(update, context)

# ========= Health server =========
from aiohttp import web

async def _health(request):
    return web.Response(
        text=(
            "🤖 Silver Price Bot is running!\n"
            f"⏰ {datetime.now(VN_TZ).strftime('%H:%M:%S - %d/%m/%Y')}\n"
            f"📊 History: {len(bot.price_history)} records\n"
            f"👥 Subscribers: {len(bot.subscribers)} users\n"
            f"⏱️ Poll interval: {POLL_SECONDS}s\n"
            f"📡 Group notifications: {'enabled' if bot.group_notification_enabled else 'disabled'}\n"
            f"🏷️ Products tracking: {len(bot.last_prices)}"
        ),
        content_type='text/plain'
    )

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("🌐 Health server started on port %s", PORT)

# ========= main =========
async def main():
    # Health server first
    await start_health_server()
    logger.info("🌐 Health server started on port %s", PORT)

    # Validate configuration
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN missing. Set BOT_TOKEN environment variable.")
        logger.info("Running health server only...")
        try:
            await asyncio.Future()  # Keep running for health checks
        except asyncio.CancelledError:
            return

    if not GROUP_CHAT_ID or GROUP_CHAT_ID == "YOUR_GROUP_CHAT_ID":
        logger.warning("⚠️ GROUP_CHAT_ID not set. Group notifications will be disabled.")
        bot.group_notification_enabled = False

    # Initialize bot
    application = Application.builder().token(BOT_TOKEN).build()
    bot.application = application

    # Add handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("subscribe", cmd_sub))
    application.add_handler(CommandHandler("unsubscribe", cmd_unsub))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("test", cmd_test))  # NEW: Test command
    application.add_handler(CallbackQueryHandler(on_button))

    # Start monitoring loop
    bot.monitoring_task = asyncio.create_task(bot.monitor_loop())

    logger.info("🚀 Bot starting (polling mode)...")
    logger.info("🎯 Group notifications: %s", "enabled" if bot.group_notification_enabled else "disabled")
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Future()  # Keep running
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("🛑 Shutting down bot...")
        if bot.monitoring_task:
            bot.monitoring_task.cancel()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
