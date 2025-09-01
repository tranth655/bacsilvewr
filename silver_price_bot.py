#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram Theo Dõi Giá Bạc
Lấy dữ liệu từ https://giabac.phuquygroup.vn/
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, time
from typing import Dict, Tuple

import pytz
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler
)

# ==============================
# Cấu hình logging
# ==============================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("SilverPriceBot")

# ==============================
# Cấu hình
# ==============================
BOT_TOKEN = "8315991420:AAFZhwx0xm96YJ84Auz-BQKZOyFCzPvvCug"   # Thay bằng token bot của bạn
GROUP_CHAT_ID = "-4959406359"                                   # ID group để gửi thông báo
PRICE_URL = "https://giabac.phuquygroup.vn/"
VN_TZ = pytz.timezone('Asia/Ho_Chi_Minh')

# ==============================
# Lớp xử lý giá bạc
# ==============================
class SilverPriceBot:
    def __init__(self):
        self.price_history = []   # list[{timestamp, prices}]
        self.subscribers = set()  # user_ids đăng ký
        self.last_prices = {}     # giá lần gần nhất để so sánh

    async def fetch_silver_prices(self) -> Dict:
        """Lấy giá bạc từ website"""
        try:
            loop = asyncio.get_event_loop()
            def _fetch():
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/119.0.0.0 Safari/537.36"
                }
                return requests.get(PRICE_URL, headers=headers, timeout=12)

            response = await loop.run_in_executor(None, _fetch)

            if response.status_code == 200:
                return self.parse_silver_prices(response.text)
            else:
                logger.error(f"HTTP {response.status_code} khi truy cập {PRICE_URL}")
                return {}
        except Exception as e:
            logger.error(f"Lỗi khi lấy dữ liệu: {e}")
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

                    # Chỉ lấy các dòng liên quan bạc & có giá
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

            return prices
        except Exception as e:
            logger.error(f"Lỗi parse HTML: {e}")
            return {}

    def parse_price(self, price_str: str) -> int:
        """Chuyển '1.234.000' → 1234000"""
        if not price_str or price_str == '-':
            return 0
        s = price_str.replace('.', '').replace(',', '')
        numbers = re.findall(r'\d+', s)
        return int(''.join(numbers)) if numbers else 0

    def format_price(self, price: int) -> str:
        """Format giá với dấu chấm ngăn nghìn"""
        return f"{price:,}".replace(',', '.')

    def calculate_spread(self, buy_price: int, sell_price: int) -> Tuple[int, float]:
        """Tính chênh lệch giá mua/bán"""
        if sell_price and sell_price > 0 and buy_price > 0:
            spread = sell_price - buy_price
            spread_percent = (spread / buy_price) * 100
            return spread, spread_percent
        return 0, 0.0


# Khởi tạo bot logic
bot = SilverPriceBot()

# ==============================
# Command handlers
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start"""
    welcome_text = (
        "🏦 *Chào mừng đến với Bot Giá Bạc!*\n\n"
        "Bot này giúp bạn theo dõi giá bạc từ Phú Quý Group và tính toán chênh lệch giá.\n\n"
        "📋 *Các lệnh có sẵn:*\n"
        "• /price - Xem giá bạc hiện tại\n"
        "• /history - Xem lịch sử giá (24h gần nhất)\n"
        "• /subscribe - Đăng ký nhận thông báo tự động\n"
        "• /unsubscribe - Hủy đăng ký thông báo\n"
        "• /spread - Xem chênh lệch giá mua/bán\n"
        "• /help - Hiển thị trợ giúp\n\n"
        "🔄 *Tự động cập nhật mỗi 30 phút*\n"
        "📊 *Dữ liệu từ:* giabac.phuquygroup.vn"
    )

    keyboard = [
        [
            InlineKeyboardButton("📈 Giá hiện tại", callback_data='current_price'),
            InlineKeyboardButton("📊 Chênh lệch", callback_data='spread')
        ],
        [
            InlineKeyboardButton("🔔 Đăng ký thông báo", callback_data='subscribe'),
            InlineKeyboardButton("📚 Lịch sử", callback_data='history')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def get_current_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /price"""
    await update.message.reply_text("🔄 Đang lấy giá bạc mới nhất...")

    prices = await bot.fetch_silver_prices()
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu giá. Vui lòng thử lại sau.")
        return

    # Lưu lịch sử
    bot.price_history.append({
        'timestamp': datetime.now(VN_TZ),
        'prices': prices.copy()
    })
    if len(bot.price_history) > 100:
        bot.price_history = bot.price_history[-100:]

    message = "💰 *GIÁ BẠC HÔM NAY*\n\n"
    for product, data in prices.items():
        spread, spread_percent = bot.calculate_spread(
            data['buy_price'], data['sell_price'] if data['sell_price'] else 0
        )
        message += f"🔸 *{product}*\n"
        message += f"   📊 Đơn vị: {data['unit']}\n"
        message += f"   💵 Mua vào: {bot.format_price(data['buy_price'])} VND\n"
        if data['sell_price']:
            message += f"   💴 Bán ra: {bot.format_price(data['sell_price'])} VND\n"
            message += f"   📈 Chênh lệch: {bot.format_price(spread)} VND ({spread_percent:.2f}%)\n"
        else:
            message += f"   💴 Bán ra: {data['sell_price_str']}\n"
        message += "\n"

    message += f"🕐 Cập nhật: {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
    await update.message.reply_text(message, parse_mode='Markdown')

async def get_price_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /history"""
    if not bot.price_history:
        await update.message.reply_text("📊 Chưa có dữ liệu lịch sử giá.")
        return

    now = datetime.now(VN_TZ)
    yesterday = now - timedelta(hours=24)
    recent_history = [h for h in bot.price_history if h['timestamp'] >= yesterday]
    if not recent_history:
        await update.message.reply_text("📊 Không có dữ liệu trong 24h gần đây.")
        return

    message = "📈 *LỊCH SỬ GIÁ BẠC (24H)*\n\n"
    main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"

    for record in recent_history[-10:]:
        if main_product in record['prices']:
            data = record['prices'][main_product]
            time_str = record['timestamp'].strftime('%H:%M %d/%m')
            message += f"🕐 *{time_str}*\n"
            message += f"   Mua: {bot.format_price(data['buy_price'])} VND\n"
            if data['sell_price']:
                message += f"   Bán: {bot.format_price(data['sell_price'])} VND\n"
            message += "\n"

    if len(recent_history) >= 2:
        latest = recent_history[-1]['prices'].get(main_product)
        previous = recent_history[-2]['prices'].get(main_product)
        if latest and previous:
            change = latest['buy_price'] - previous['buy_price']
            change_percent = (change / previous['buy_price']) * 100 if previous['buy_price'] else 0
            change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            message += f"\n{change_emoji} *Biến động gần nhất:*\n"
            message += f"   {'+' if change > 0 else ''}{bot.format_price(change)} VND ({change_percent:+.2f}%)"

    await update.message.reply_text(message, parse_mode='Markdown')

async def calculate_spread(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /spread"""
    prices = await bot.fetch_silver_prices()
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu giá.")
        return

    message = "📊 *CHÊNH LỆCH GIÁ MUA/BÁN*\n\n"
    for product, data in prices.items():
        if data['sell_price']:
            spread, spread_percent = bot.calculate_spread(data['buy_price'], data['sell_price'])
            message += f"🔸 *{product}*\n"
            message += f"   💵 Mua: {bot.format_price(data['buy_price'])} VND\n"
            message += f"   💴 Bán: {bot.format_price(data['sell_price'])} VND\n"
            message += f"   📈 Chênh lệch: {bot.format_price(spread)} VND\n"
            message += f"   📊 Tỷ lệ: {spread_percent:.2f}%\n\n"

    await update.message.reply_text(message, parse_mode='Markdown')

async def subscribe_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot.subscribers.add(user_id)
    await update.message.reply_text(
        "🔔 *Đã đăng ký thành công!*\n\n"
        "Bạn sẽ nhận được thông báo khi:\n"
        "• Giá thay đổi > 2%\n"
        "• Cập nhật giá định kỳ (8:30, 12:00, 16:00)\n\n"
        "Dùng /unsubscribe để hủy đăng ký.",
        parse_mode='Markdown'
    )

async def unsubscribe_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot.subscribers.discard(user_id)
    await update.message.reply_text("🔕 Đã hủy đăng ký thông báo thành công!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *HƯỚNG DẪN SỬ DỤNG BOT GIÁ BẠC*\n\n"
        "📋 *Các lệnh chính:*\n"
        "• `/price` - Xem giá bạc hiện tại\n"
        "• `/history` - Lịch sử giá 24h\n"
        "• `/spread` - Chênh lệch giá mua/bán\n"
        "• `/subscribe` - Đăng ký thông báo tự động\n"
        "• `/unsubscribe` - Hủy đăng ký thông báo\n\n"
        "🔔 *Thông báo tự động:*\n"
        "• Cập nhật giá định kỳ: 8:30, 12:00, 16:00\n"
        "• Cảnh báo khi giá thay đổi > 2%\n\n"
        "📊 *Thông tin hiển thị:*\n"
        "• Giá mua vào và bán ra\n"
        "• Chênh lệch tuyệt đối và phần trăm\n"
        "• Biến động so với lần cập nhật trước\n"
        "• Thời gian cập nhật gần nhất\n\n"
        "🌐 *Nguồn dữ liệu:* giabac.phuquygroup.vn\n"
        "❓ *Cần hỗ trợ?* Liên hệ admin bot."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def get_group_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /groupinfo - Lấy thông tin group"""
    chat = update.effective_chat
    if chat.type in ['group', 'supergroup']:
        info_message = (
            "ℹ️ *THÔNG TIN GROUP*\n\n"
            f"📝 Tên: {chat.title}\n"
            f"🆔 Chat ID: `{chat.id}`\n"
            f"👥 Loại: {chat.type}\n\n"
            "💡 *Hướng dẫn:*\n"
            "Để bot gửi thông báo tự động cho group này,\n"
            f"hãy cập nhật `GROUP_CHAT_ID = \"{chat.id}\"` trong code."
        )
        await update.message.reply_text(info_message, parse_mode='Markdown')
    else:
        await update.message.reply_text(
            "ℹ️ Lệnh này chỉ hoạt động trong group.\n"
            "Hãy thêm bot vào group và chạy lệnh /groupinfo để lấy Chat ID."
        )

# ==============================
# Callback buttons
# ==============================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'current_price':
        await query.edit_message_text("🔄 Đang lấy giá mới nhất...")
        prices = await bot.fetch_silver_prices()
        if prices:
            message = "💰 *GIÁ BẠC HIỆN TẠI*\n\n"
            main_products = [
                "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG",
                "BẠC THỎI PHÚ QUÝ 999 10 LƯỢNG, 5 LƯỢNG"
            ]
            for product in main_products:
                if product in prices:
                    data = prices[product]
                    message += f"🔸 *{product}*\n"
                    message += f"   💵 Mua: {bot.format_price(data['buy_price'])} VND\n"
                    if data['sell_price']:
                        message += f"   💴 Bán: {bot.format_price(data['sell_price'])} VND\n"
                    message += "\n"

            message += f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
            await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Không thể lấy dữ liệu giá.")

    elif query.data == 'spread':
        await query.edit_message_text("🔄 Đang tính chênh lệch...")
        prices = await bot.fetch_silver_prices()
        if prices:
            message = "📊 *CHÊNH LỆCH GIÁ*\n\n"
            for product, data in prices.items():
                if data['sell_price']:
                    spread, spread_percent = bot.calculate_spread(
                        data['buy_price'], data['sell_price']
                    )
                    title = (f"{product[:30]}..." if len(product) > 30 else product)
                    message += f"🔸 *{title}*\n"
                    message += f"   📈 {bot.format_price(spread)} VND ({spread_percent:.2f}%)\n\n"
            await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Không thể tính chênh lệch.")

    elif query.data == 'subscribe':
        user_id = query.from_user.id
        bot.subscribers.add(user_id)
        await query.edit_message_text("🔔 Đã đăng ký thông báo thành công!")

    elif query.data == 'history':
        if bot.price_history:
            message = "📈 *LỊCH SỬ GIÁ GẦN ĐÂY*\n\n"
            main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
            recent_records = bot.price_history[-5:]
            for record in recent_records:
                if main_product in record['prices']:
                    data = record['prices'][main_product]
                    time_str = record['timestamp'].strftime('%H:%M %d/%m')
                    message += f"🕐 {time_str}: {bot.format_price(data['buy_price'])} VND\n"
            await query.edit_message_text(message, parse_mode='Markdown')
        else:
            await query.edit_message_text("📊 Chưa có dữ liệu lịch sử.")

# ==============================
# Jobs (scheduler)
# ==============================
async def scheduled_price_check(context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra giá mỗi 30 phút, nếu biến động > 2% thì cảnh báo"""
    try:
        prices = await bot.fetch_silver_prices()
        if not prices:
            return

        # Lưu lịch sử
        current_record = {
            'timestamp': datetime.now(VN_TZ),
            'prices': prices.copy()
        }
        bot.price_history.append(current_record)

        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"

        if (main_product in prices and main_product in bot.last_prices):
            current_price = prices[main_product]['buy_price']
            last_price = bot.last_prices[main_product]['buy_price']

            if last_price > 0:
                change_percent = abs((current_price - last_price) / last_price * 100)
            else:
                change_percent = 0

            if change_percent > 2.0:
                change = current_price - last_price
                change_emoji = "📈" if change > 0 else "📉"

                alert_message = (
                    "🚨 *CẢNH BÁO THAY ĐỔI GIÁ BẠC*\n\n"
                    f"{change_emoji} *{main_product}*\n\n"
                    f"📊 Giá cũ: {bot.format_price(last_price)} VND\n"
                    f"📊 Giá mới: {bot.format_price(current_price)} VND\n"
                    f"📈 Thay đổi: {'+' if change > 0 else ''}{bot.format_price(change)} VND ({change_percent:+.2f}%)\n\n"
                    f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
                )

                # Gửi cho group
                if GROUP_CHAT_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=GROUP_CHAT_ID,
                            text=alert_message,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Không thể gửi thông báo cho group {GROUP_CHAT_ID}: {e}")

                # Gửi cho subscribers
                for user_id in bot.subscribers.copy():
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=alert_message,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Không thể gửi thông báo cho {user_id}: {e}")
                        bot.subscribers.discard(user_id)

        # Cập nhật giá cuối
        bot.last_prices = prices.copy()

    except Exception as e:
        logger.error(f"Lỗi trong scheduled_price_check: {e}")

async def send_scheduled_update(context: ContextTypes.DEFAULT_TYPE):
    """Gửi cập nhật định kỳ cho group (8:30, 12:00, 16:00)"""
    try:
        prices = await bot.fetch_silver_prices()
        if not prices:
            return

        message = "🔔 *CẬP NHẬT GIÁ BẠC ĐỊNH KỲ*\n\n"
        main_products = [
            "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG",
            "BẠC THỎI PHÚ QUÝ 999 10 LƯỢNG, 5 LƯỢNG",
            "ĐỒNG BẠC MỸ NGHỆ PHÚ QUÝ 999"
        ]

        for product in main_products:
            if product in prices:
                data = prices[product]
                message += f"🔸 *{product}*\n"
                message += f"   💵 Mua: {bot.format_price(data['buy_price'])} VND\n"
                if data['sell_price']:
                    message += f"   💴 Bán: {bot.format_price(data['sell_price'])} VND\n"
                    spread, spread_percent = bot.calculate_spread(
                        data['buy_price'], data['sell_price']
                    )
                    message += f"   📊 Chênh lệch: {bot.format_price(spread)} VND ({spread_percent:.2f}%)\n"
                else:
                    message += f"   💴 Bán: {data['sell_price_str']}\n"
                message += "\n"

        # Biến động so với lần cập nhật trước cho sản phẩm chính
        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        if (main_product in prices and main_product in bot.last_prices):
            current_price = prices[main_product]['buy_price']
            last_price = bot.last_prices[main_product]['buy_price']
            change = current_price - last_price
            change_percent = (change / last_price * 100) if last_price > 0 else 0
            if abs(change_percent) > 0.1:
                change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                message += f"{change_emoji} *Biến động:* {'+' if change > 0 else ''}{bot.format_price(change)} VND ({change_percent:+.2f}%)\n\n"

        message += f"🕐 *Thời gian:* {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
        message += f"🌐 *Nguồn:* giabac.phuquygroup.vn"

        if GROUP_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"Đã gửi cập nhật định kỳ cho group {GROUP_CHAT_ID}")
            except Exception as e:
                logger.error(f"Không thể gửi cập nhật cho group {GROUP_CHAT_ID}: {e}")

        # Cập nhật làm mốc cho lần sau
        bot.last_prices = prices.copy()

    except Exception as e:
        logger.error(f"Lỗi trong send_scheduled_update: {e}")

async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Gửi báo cáo tổng kết hàng ngày lúc 18:00"""
    try:
        prices = await bot.fetch_silver_prices()
        if not prices:
            return

        today_start = datetime.now(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        today_records = [h for h in bot.price_history if h['timestamp'] >= today_start]

        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        if main_product in prices:
            current_data = prices[main_product]
            today_prices = [
                r['prices'][main_product]['buy_price']
                for r in today_records if main_product in r['prices']
            ]

            if today_prices:
                highest = max(today_prices)
                lowest = min(today_prices)
                current = current_data['buy_price']

                summary = (
                    "📊 *BÁO CÁO GIÁ BẠC CUỐI NGÀY*\n\n"
                    f"🔸 *{main_product}*\n\n"
                    f"📈 Cao nhất: {bot.format_price(highest)} VND\n"
                    f"📉 Thấp nhất: {bot.format_price(lowest)} VND\n"
                    f"💰 Hiện tại: {bot.format_price(current)} VND\n\n"
                    f"📊 Biên độ dao động: {bot.format_price(highest - lowest)} VND\n"
                    f"📊 Tỷ lệ dao động: {((highest - lowest) / lowest * 100):.2f}%\n\n"
                    f"🕐 Cập nhật: {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
                )

                # Gửi group
                if GROUP_CHAT_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=GROUP_CHAT_ID,
                            text=summary,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Không thể gửi báo cáo cho group {GROUP_CHAT_ID}: {e}")

                # Gửi subscribers
                for user_id in bot.subscribers.copy():
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=summary,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Không thể gửi báo cáo cho {user_id}: {e}")
                        bot.subscribers.discard(user_id)
    except Exception as e:
        logger.error(f"Lỗi trong daily_summary: {e}")

# ==============================
# Main
# ==============================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", get_current_price))
    application.add_handler(CommandHandler("history", get_price_history))
    application.add_handler(CommandHandler("spread", calculate_spread))
    application.add_handler(CommandHandler("subscribe", subscribe_notifications))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_notifications))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("groupinfo", get_group_info))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Schedulers
    job_queue = application.job_queue

    # Kiểm tra giá mỗi 30 phút
    job_queue.run_repeating(
        scheduled_price_check,
        interval=timedelta(minutes=30),
        first=timedelta(seconds=10),
        name="scheduled_price_check"
    )

    # Tạo các mốc giờ theo VN_TZ (time có tzinfo)
    update_times = [
        time(8, 30, tzinfo=VN_TZ),
        time(12, 0, tzinfo=VN_TZ),
        time(16, 0, tzinfo=VN_TZ),
    ]
    for t in update_times:
        job_queue.run_daily(
            send_scheduled_update,
            time=t,
            name=f"scheduled_update_{t.hour:02d}{t.minute:02d}"
        )

    # Báo cáo cuối ngày lúc 18:00 (VN_TZ)
    job_queue.run_daily(
        daily_summary,
        time=time(18, 0, tzinfo=VN_TZ),
        name="daily_summary_1800"
    )

    print("🤖 Bot Giá Bạc đang khởi động...")
    print("📊 Nguồn dữ liệu: https://giabac.phuquygroup.vn/")
    print("🔄 Cập nhật mỗi 30 phút")

    application.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
