#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram Theo Dõi Giá Bạc - Railway Version
"""

import asyncio
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, Tuple

import requests
import pytz
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cấu hình
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID', 'YOUR_GROUP_CHAT_ID')
PRICE_URL = "https://giabac.phuquygroup.vn/"
VN_TZ = pytz.timezone('Asia/Ho_Chi_Minh')
PORT = int(os.environ.get('PORT', 8000))

class SilverPriceBot:
    def __init__(self):
        self.price_history = []
        self.subscribers = set()
        self.last_prices = {}
        self.application = None
        
    async def fetch_silver_prices(self) -> Dict:
        """Lấy giá bạc từ website"""
        try:
            loop = asyncio.get_event_loop()
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(PRICE_URL, headers=headers, timeout=15)
            )
            
            if response.status_code == 200:
                return self.parse_silver_prices(response.text)
            else:
                logger.error(f"HTTP {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return {}
    
    def parse_silver_prices(self, html: str) -> Dict:
        """Parse giá bạc"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            prices = {}
            table_rows = soup.find_all('tr')
            
            for row in table_rows:
                cells = row.find_all('td')
                if len(cells) >= 4:
                    product = cells[0].get_text(strip=True)
                    unit = cells[1].get_text(strip=True)
                    buy_price = cells[2].get_text(strip=True)
                    sell_price = cells[3].get_text(strip=True)
                    
                    if buy_price and 'BẠC' in product.upper():
                        buy_price_num = self.parse_price(buy_price)
                        sell_price_num = self.parse_price(sell_price)
                        
                        if buy_price_num > 0:
                            prices[product] = {
                                'buy_price': buy_price_num,
                                'sell_price': sell_price_num if sell_price_num > 0 else None,
                                'unit': unit,
                                'timestamp': datetime.now(VN_TZ)
                            }
            
            return prices
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return {}
    
    def parse_price(self, price_str: str) -> int:
        if not price_str or price_str == '-':
            return 0
        numbers = re.findall(r'\d+', price_str.replace(',', '').replace('.', ''))
        return int(''.join(numbers)) if numbers else 0
    
    def format_price(self, price: int) -> str:
        return f"{price:,}".replace(',', '.')

bot = SilverPriceBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    await update.message.reply_text(
        "🏦 *Bot Giá Bạc Railway*\n\n"
        "📋 Commands:\n"
        "• /price - Giá hiện tại\n"
        "• /subscribe - Đăng ký thông báo\n"
        "• /status - Trạng thái bot",
        parse_mode='Markdown'
    )

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get current price"""
    await update.message.reply_text("🔄 Đang lấy giá...")
    
    prices = await bot.fetch_silver_prices()
    
    if prices:
        message = "💰 *GIÁ BẠC*\n\n"
        for product, data in list(prices.items())[:3]:  # Top 3 products
            message += f"🔸 {product[:30]}...\n" if len(product) > 30 else f"🔸 {product}\n"
            message += f"💵 Mua: {bot.format_price(data['buy_price'])} VND\n"
            if data['sell_price']:
                message += f"💴 Bán: {bot.format_price(data['sell_price'])} VND\n"
            message += "\n"
        
        message += f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m')}"
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Không lấy được dữ liệu")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribe to notifications"""
    user_id = update.effective_user.id
    bot.subscribers.add(user_id)
    await update.message.reply_text("🔔 Đã đăng ký!")

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot status"""
    await update.message.reply_text(
        f"🤖 Bot đang chạy\n"
        f"📊 {len(bot.price_history)} records\n"
        f"👥 {len(bot.subscribers)} subscribers\n"
        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m')}"
    )

# Health check server
from aiohttp import web

async def health_handler(request):
    """Health check endpoint"""
    return web.Response(
        text=f"OK - Bot running at {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
    )

async def root_handler(request):
    """Root endpoint"""
    status = {
        'status': 'running',
        'bot_name': 'Silver Price Bot',
        'timestamp': datetime.now(VN_TZ).isoformat(),
        'subscribers': len(bot.subscribers),
        'price_records': len(bot.price_history)
    }
    return web.json_response(status)

def create_web_app():
    """Tạo web app cho health check"""
    app = web.Application()
    app.router.add_get('/', root_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/status', root_handler)
    return app

async def monitoring_loop():
    """Background task theo dõi giá"""
    logger.info("🔄 Starting price monitoring...")
    
    while True:
        try:
            prices = await bot.fetch_silver_prices()
            
            if prices:
                # Lưu lịch sử
                bot.price_history.append({
                    'timestamp': datetime.now(VN_TZ),
                    'prices': prices
                })
                
                # Giữ 50 bản ghi gần nhất
                if len(bot.price_history) > 50:
                    bot.price_history = bot.price_history[-50:]
                
                # Kiểm tra thay đổi lớn
                main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
                if (main_product in prices and main_product in bot.last_prices):
                    current = prices[main_product]['buy_price']
                    last = bot.last_prices[main_product]['buy_price']
                    change_percent = abs((current - last) / last * 100)
                    
                    if change_percent > 2.0:
                        change = current - last
                        emoji = "📈" if change > 0 else "📉"
                        
                        alert = f"""
🚨 *CẢNH BÁO GIÁ BẠC*

{emoji} Giá thay đổi {change_percent:.2f}%
💰 {bot.format_price(current)} VND
📊 {'+' if change > 0 else ''}{bot.format_price(change)} VND

🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m')}
                        """
                        
                        # Gửi cảnh báo
                        if GROUP_CHAT_ID and GROUP_CHAT_ID != 'YOUR_GROUP_CHAT_ID':
                            try:
                                await bot.application.bot.send_message(
                                    chat_id=GROUP_CHAT_ID,
                                    text=alert,
                                    parse_mode='Markdown'
                                )
                            except Exception as e:
                                logger.error(f"Group send error: {e}")
                
                bot.last_prices = prices
                logger.info(f"✅ Updated prices - {len(prices)} products")
            
            else:
                logger.warning("⚠️ No price data")
            
            # Chờ 30 phút
            await asyncio.sleep(30 * 60)
            
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            await asyncio.sleep(60)

async def main():
    """Main function"""
    # Validate config
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        logger.error("❌ BOT_TOKEN not configured!")
        return
    
    logger.info("🚀 Starting Silver Price Bot...")
    
    # Create Telegram app
    application = Application.builder().token(BOT_TOKEN).build()
    bot.application = application
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", get_price))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("status", get_status))
    
    # Create web app for health checks
    web_app = create_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    # Start web server
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Health server started on port {PORT}")
    
    # Start monitoring in background
    monitoring_task = asyncio.create_task(monitoring_loop())
    
    # Start telegram bot
    logger.info("🤖 Starting Telegram bot...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    finally:
        await application.stop()
        monitoring_task.cancel()

if __name__ == '__main__':
    asyncio.run(main()) requests
import pytz
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cấu hình từ environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID', 'YOUR_GROUP_CHAT_ID')
PRICE_URL = "https://giabac.phuquygroup.vn/"
VN_TZ = pytz.timezone('Asia/Ho_Chi_Minh')

# Port cho Railway/Render
PORT = int(os.environ.get('PORT', 8000))

class SilverPriceBot:
    def __init__(self):
        self.price_history = []
        self.subscribers = set()
        self.last_prices = {}
        self.application = None
        self.monitoring_task = None
        
    async def fetch_silver_prices(self) -> Dict:
        """Lấy giá bạc từ website"""
        try:
            loop = asyncio.get_event_loop()
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
                'Connection': 'keep-alive',
            }
            
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(PRICE_URL, headers=headers, timeout=15)
            )
            
            if response.status_code == 200:
                logger.info("✅ Lấy dữ liệu thành công")
                return self.parse_silver_prices(response.text)
            else:
                logger.error(f"❌ HTTP {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ Lỗi fetch: {e}")
            return {}
    
    def parse_silver_prices(self, html: str) -> Dict:
        """Parse giá bạc từ HTML"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            prices = {}
            table_rows = soup.find_all('tr')
            current_time = datetime.now(VN_TZ)
            
            for row in table_rows:
                cells = row.find_all('td')
                if len(cells) >= 4:
                    product = cells[0].get_text(strip=True)
                    unit = cells[1].get_text(strip=True)
                    buy_price = cells[2].get_text(strip=True)
                    sell_price = cells[3].get_text(strip=True)
                    
                    if buy_price and buy_price != '-' and 'BẠC' in product.upper():
                        buy_price_num = self.parse_price(buy_price)
                        sell_price_num = self.parse_price(sell_price)
                        
                        if buy_price_num > 0:
                            prices[product] = {
                                'unit': unit,
                                'buy_price': buy_price_num,
                                'sell_price': sell_price_num if sell_price_num > 0 else None,
                                'buy_price_str': buy_price,
                                'sell_price_str': sell_price if sell_price != '-' else 'Không mua',
                                'timestamp': current_time
                            }
            
            logger.info(f"📊 Parse được {len(prices)} sản phẩm")
            return prices
            
        except Exception as e:
            logger.error(f"❌ Lỗi parse: {e}")
            return {}
    
    def parse_price(self, price_str: str) -> int:
        """Chuyển đổi chuỗi giá thành số"""
        if not price_str or price_str == '-':
            return 0
        numbers = re.findall(r'\d+', price_str.replace(',', '').replace('.', ''))
        return int(''.join(numbers)) if numbers else 0
    
    def format_price(self, price: int) -> str:
        """Format giá với dấu phẩy"""
        return f"{price:,}".replace(',', '.')
    
    def calculate_spread(self, buy_price: int, sell_price: int) -> Tuple[int, float]:
        """Tính chênh lệch giá mua bán"""
        if sell_price and sell_price > 0:
            spread = sell_price - buy_price
            spread_percent = (spread / buy_price) * 100 if buy_price > 0 else 0
            return spread, spread_percent
        return 0, 0.0
    
    async def send_to_group(self, message: str):
        """Gửi tin nhắn đến group"""
        if GROUP_CHAT_ID and GROUP_CHAT_ID != 'YOUR_GROUP_CHAT_ID' and self.application:
            try:
                await self.application.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"✅ Đã gửi tin đến group")
            except Exception as e:
                logger.error(f"❌ Lỗi gửi group: {e}")
    
    async def price_monitoring_loop(self):
        """Vòng lặp theo dõi giá"""
        logger.info("🔄 Bắt đầu monitoring giá...")
        
        while True:
            try:
                # Lấy giá mới
                prices = await self.fetch_silver_prices()
                
                if prices:
                    # Lưu lịch sử
                    self.price_history.append({
                        'timestamp': datetime.now(VN_TZ),
                        'prices': prices.copy()
                    })
                    
                    # Giữ chỉ 100 bản ghi
                    if len(self.price_history) > 100:
                        self.price_history = self.price_history[-100:]
                    
                    # Kiểm tra thay đổi lớn
                    await self.check_price_alerts(prices)
                    
                    # Kiểm tra nếu đến giờ gửi tin định kỳ
                    await self.check_scheduled_updates(prices)
                    
                    # Cập nhật giá cuối
                    self.last_prices = prices.copy()
                    
                else:
                    logger.warning("⚠️ Không lấy được dữ liệu giá")
                
                # Chờ 30 phút
                await asyncio.sleep(30 * 60)
                
            except Exception as e:
                logger.error(f"❌ Lỗi trong monitoring loop: {e}")
                await asyncio.sleep(60)  # Chờ 1 phút nếu lỗi
    
    async def check_price_alerts(self, current_prices: Dict):
        """Kiểm tra cảnh báo thay đổi giá"""
        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        
        if (main_product in current_prices and 
            main_product in self.last_prices):
            
            current_price = current_prices[main_product]['buy_price']
            last_price = self.last_prices[main_product]['buy_price']
            
            change_percent = abs((current_price - last_price) / last_price * 100)
            
            if change_percent > 2.0:
                change = current_price - last_price
                change_emoji = "📈" if change > 0 else "📉"
                
                alert_message = f"""
🚨 *CẢNH BÁO THAY ĐỔI GIÁ BẠC*

{change_emoji} *{main_product}*

📊 Giá cũ: {self.format_price(last_price)} VND
📊 Giá mới: {self.format_price(current_price)} VND
📈 Thay đổi: {'+' if change > 0 else ''}{self.format_price(change)} VND ({change_percent:+.2f}%)

🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}
                """
                
                await self.send_to_group(alert_message)
                
                # Gửi cho subscribers cá nhân
                for user_id in self.subscribers.copy():
                    try:
                        await self.application.bot.send_message(
                            chat_id=user_id,
                            text=alert_message,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"❌ Lỗi gửi alert cho {user_id}: {e}")
                        self.subscribers.discard(user_id)
    
    async def check_scheduled_updates(self, prices: Dict):
        """Kiểm tra nếu đến giờ gửi tin định kỳ"""
        now = datetime.now(VN_TZ)
        
        # Chỉ gửi vào các thời điểm: 8:30, 12:00, 16:00
        target_times = [
            (8, 30), (12, 0), (16, 0)
        ]
        
        current_time = (now.hour, now.minute)
        
        # Kiểm tra nếu đúng thời điểm (trong khoảng 5 phút)
        for target_hour, target_minute in target_times:
            time_diff = abs((now.hour * 60 + now.minute) - (target_hour * 60 + target_minute))
            
            if time_diff <= 2:  # Trong vòng 2 phút
                await self.send_scheduled_update(prices)
                break
    
    async def send_scheduled_update(self, prices: Dict):
        """Gửi cập nhật định kỳ"""
        message = "🔔 *CẬP NHẬT GIÁ BẠC ĐỊNH KỲ*\n\n"
        
        main_products = [
            "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG",
            "BẠC THỎI PHÚ QUÝ 999 10 LƯỢNG, 5 LƯỢNG"
        ]
        
        for product in main_products:
            if product in prices:
                data = prices[product]
                message += f"🔸 *{product}*\n"
                message += f"   💵 Mua: {self.format_price(data['buy_price'])} VND\n"
                
                if data['sell_price']:
                    message += f"   💴 Bán: {self.format_price(data['sell_price'])} VND\n"
                    spread, spread_percent = self.calculate_spread(
                        data['buy_price'], data['sell_price']
                    )
                    message += f"   📊 Chênh lệch: {self.format_price(spread)} VND ({spread_percent:.2f}%)\n"
                
                message += "\n"
        
        # Tính biến động
        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        if (main_product in prices and main_product in self.last_prices):
            current_price = prices[main_product]['buy_price']
            last_price = self.last_prices[main_product]['buy_price']
            change = current_price - last_price
            change_percent = (change / last_price * 100) if last_price > 0 else 0
            
            if abs(change_percent) > 0.1:
                change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                message += f"{change_emoji} *Biến động:* {'+' if change > 0 else ''}{self.format_price(change)} VND ({change_percent:+.2f}%)\n\n"
        
        message += f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
        message += f"🌐 *Nguồn:* giabac.phuquygroup.vn"
        
        await self.send_to_group(message)

# Khởi tạo bot
bot = SilverPriceBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start"""
    welcome_text = """
🏦 *Chào mừng đến với Bot Giá Bạc!*

Bot theo dõi giá bạc từ Phú Quý Group 24/7

📋 *Các lệnh:*
• /price - Giá hiện tại
• /history - Lịch sử 24h
• /subscribe - Đăng ký thông báo
• /spread - Chênh lệch giá
• /status - Trạng thái bot

🔄 *Tự động:*
• Cập nhật: 8:30, 12:00, 16:00
• Cảnh báo thay đổi > 2%
    """
    
    keyboard = [
        [
            InlineKeyboardButton("📈 Giá hiện tại", callback_data='price'),
            InlineKeyboardButton("📊 Chênh lệch", callback_data='spread')
        ],
        [
            InlineKeyboardButton("🔔 Đăng ký", callback_data='subscribe'),
            InlineKeyboardButton("📚 Lịch sử", callback_data='history')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)

async def get_current_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /price"""
    await update.message.reply_text("🔄 Đang lấy giá...")
    
    prices = await bot.fetch_silver_prices()
    
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu. Kiểm tra kết nối.")
        return
    
    message = "💰 *GIÁ BẠC HIỆN TẠI*\n\n"
    
    for product, data in prices.items():
        spread, spread_percent = bot.calculate_spread(
            data['buy_price'], 
            data['sell_price'] if data['sell_price'] else 0
        )
        
        message += f"🔸 *{product}*\n"
        message += f"   💵 Mua: {bot.format_price(data['buy_price'])} VND\n"
        
        if data['sell_price']:
            message += f"   💴 Bán: {bot.format_price(data['sell_price'])} VND\n"
            message += f"   📊 Chênh lệch: {bot.format_price(spread)} VND ({spread_percent:.2f}%)\n"
        
        message += "\n"
    
    message += f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def subscribe_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Đăng ký thông báo"""
    user_id = update.effective_user.id
    bot.subscribers.add(user_id)
    
    await update.message.reply_text(
        "🔔 *Đăng ký thành công!*\n\n"
        "Nhận thông báo khi giá thay đổi > 2%",
        parse_mode='Markdown'
    )

async def unsubscribe_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy đăng ký"""
    user_id = update.effective_user.id
    bot.subscribers.discard(user_id)
    await update.message.reply_text("🔕 Đã hủy đăng ký!")

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra trạng thái bot"""
    status_message = f"""
🤖 *TRẠNG THÁI BOT*

🔄 Bot đang chạy: ✅
📊 Số lịch sử: {len(bot.price_history)} bản ghi
👥 Subscribers: {len(bot.subscribers)} người
🕐 Uptime: {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}

🌐 Nguồn: giabac.phuquygroup.vn
📡 Hosting: Railway/Render
    """
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý inline buttons"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'price':
        await query.edit_message_text("🔄 Đang lấy giá...")
        prices = await bot.fetch_silver_prices()
        
        if prices:
            main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
            if main_product in prices:
                data = prices[main_product]
                message = f"""
💰 *GIÁ BẠC HIỆN TẠI*

🔸 *{main_product}*
💵 Mua: {bot.format_price(data['buy_price'])} VND
💴 Bán: {bot.format_price(data['sell_price'])} VND

🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}
                """
                await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Không thể lấy dữ liệu")
    
    elif query.data == 'subscribe':
        user_id = query.from_user.id
        bot.subscribers.add(user_id)
        await query.edit_message_text("🔔 Đã đăng ký thông báo!")

async def keep_alive_server():
    """Server đơn giản để keep Railway/Render alive"""
    from aiohttp import web
    
    async def health_check(request):
        return web.Response(
            text=f"🤖 Silver Price Bot is running!\n"
                 f"⏰ {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
                 f"📊 History: {len(bot.price_history)} records\n"
                 f"👥 Subscribers: {len(bot.subscribers)}"
        )
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"🌐 Health server started on port {PORT}")

async def main():
    """Hàm chính"""
    # Kiểm tra cấu hình
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        logger.error("❌ Chưa cấu hình BOT_TOKEN!")
        print("🔧 Hãy set environment variable: BOT_TOKEN=your_token_here")
        return
    
    # Tạo application
    application = Application.builder().token(BOT_TOKEN).build()
    bot.application = application
    
    # Thêm handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", get_current_price))
    application.add_handler(CommandHandler("subscribe", subscribe_notifications))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_notifications))
    application.add_handler(CommandHandler("status", get_status))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Khởi động monitoring trong background
    bot.monitoring_task = asyncio.create_task(bot.price_monitoring_loop())
    
    # Khởi động health server cho Railway/Render
    await keep_alive_server()
    
    logger.info("🤖 Bot Giá Bạc đang khởi động...")
    logger.info("📊 Nguồn: https://giabac.phuquygroup.vn/")
    logger.info(f"🌐 Health server: http://0.0.0.0:{PORT}")
    
    # Chạy bot
    await application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    asyncio.run(main())
