#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram Theo Dõi Giá Bạc - Railway Version (fixed async)
- Health server luôn trả 200 tại /health để tránh 503
- PTB v20.7 (polling, async-friendly)
- Tính chênh lệch (spread) giữa giá mua/bán
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Dict, Tuple

import requests
import pytz
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ========= Cấu hình logging =========
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("silver-bot")

# ========= Biến môi trường / cấu hình =========
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "YOUR_GROUP_CHAT_ID")
PRICE_URL = "https://giabac.phuquygroup.vn/"
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")
PORT = int(os.environ.get("PORT", 8000))  # Railway cung cấp PORT

# ========= Bot logic =========
class SilverPriceBot:
    def __init__(self):
        self.price_history = []   # list[{"timestamp": dt, "prices": {...}}]
        self.subscribers = set()  # user ids
        self.last_prices = {}     # map sản phẩm -> dict giá lần trước
        self.application = None   # sẽ gán khi tạo Application
        self.monitoring_task = None

    async def fetch_silver_prices(self) -> Dict:
        """Lấy giá bạc từ website."""
        try:
            loop = asyncio.get_event_loop()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
            resp = await loop.run_in_executor(
                None, lambda: requests.get(PRICE_URL, headers=headers, timeout=15)
            )
            if resp.status_code == 200:
                return self.parse_silver_prices(resp.text)
            logger.error("HTTP %s khi lấy giá", resp.status_code)
            return {}
        except Exception as e:
            logger.exception("Fetch error: %s", e)
            return {}

    def parse_silver_prices(self, html: str) -> Dict:
        """Parse giá từ HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            prices: Dict[str, Dict] = {}
            now = datetime.now(VN_TZ)

            for row in soup.find_all("tr"):
                tds = row.find_all("td")
                if len(tds) < 4:
                    continue
                product = tds[0].get_text(strip=True)
                unit = tds[1].get_text(strip=True)
                buy_raw = tds[2].get_text(strip=True)
                sell_raw = tds[3].get_text(strip=True)

                # Chỉ lấy các dòng có chữ "BẠC"
                if "BẠC" not in product.upper():
                    continue
                buy = self.parse_price(buy_raw)
                sell = self.parse_price(sell_raw)

                if buy > 0:
                    prices[product] = {
                        "unit": unit,
                        "buy_price": buy,
                        "sell_price": sell if sell > 0 else None,
                        "timestamp": now,
                    }

            logger.info("Parse được %d sản phẩm", len(prices))
            return prices
        except Exception as e:
            logger.exception("Parse error: %s", e)
            return {}

    @staticmethod
    def parse_price(price_str: str) -> int:
        """Chuyển chuỗi giá thành số nguyên VND."""
        if not price_str or price_str == "-":
            return 0
        # Bỏ dấu chấm, phẩy -> giữ số
        numbers = re.findall(r"\d+", price_str.replace(",", "").replace(".", ""))
        return int("".join(numbers)) if numbers else 0

    @staticmethod
    def format_price(price: int) -> str:
        """Format số theo 1.234.567"""
        return f"{price:,}".replace(",", ".")

    @staticmethod
    def calculate_spread(buy_price: int, sell_price: int | None) -> Tuple[int, float]:
        """Tính chênh lệch (bán - mua) & % theo mua."""
        if sell_price and sell_price > 0 and buy_price > 0:
            spread = sell_price - buy_price
            pct = (spread / buy_price) * 100
            return spread, pct
        return 0, 0.0

    async def send_to_group(self, message: str):
        """Gửi tin tới GROUP_CHAT_ID nếu có."""
        if (
            GROUP_CHAT_ID
            and GROUP_CHAT_ID != "YOUR_GROUP_CHAT_ID"
            and self.application
        ):
            try:
                await self.application.bot.send_message(
                    chat_id=GROUP_CHAT_ID, text=message, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error("Lỗi gửi group: %s", e)

    async def price_monitoring_loop(self):
        """Vòng lặp lấy giá/ gửi cảnh báo/ gửi định kỳ."""
        logger.info("Bắt đầu monitoring giá...")
        while True:
            try:
                prices = await self.fetch_silver_prices()
                if prices:
                    # Lưu lịch sử (giới hạn 100)
                    self.price_history.append(
                        {"timestamp": datetime.now(VN_TZ), "prices": prices.copy()}
                    )
                    self.price_history = self.price_history[-100:]

                    # Cảnh báo thay đổi > 2% cho sản phẩm chính
                    await self.check_price_alerts(prices)
                    # Gửi bảng tin định kỳ theo mốc giờ
                    await self.check_scheduled_updates(prices)

                    # Lưu last
                    self.last_prices = prices.copy()
                else:
                    logger.warning("Không lấy được dữ liệu giá")

                await asyncio.sleep(30 * 60)  # 30 phút
            except Exception as e:
                logger.exception("Lỗi monitoring: %s", e)
                await asyncio.sleep(60)

    async def check_price_alerts(self, current_prices: Dict):
        """Cảnh báo nếu biến động >2% đối với sản phẩm chính."""
        main_product = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        if main_product in current_prices and main_product in self.last_prices:
            cur = current_prices[main_product]["buy_price"]
            last = self.last_prices[main_product]["buy_price"]
            if last > 0:
                change_pct = abs((cur - last) / last * 100)
                if change_pct > 2.0:
                    delta = cur - last
                    emoji = "📈" if delta > 0 else "📉"
                    msg = (
                        f"🚨 *CẢNH BÁO THAY ĐỔI GIÁ BẠC*\n\n"
                        f"{emoji} *{main_product}*\n"
                        f"📊 Cũ: {self.format_price(last)} VND\n"
                        f"📊 Mới: {self.format_price(cur)} VND\n"
                        f"📈 Biến động: {'+' if delta>0 else ''}{self.format_price(delta)} VND ({(delta/last*100):+.2f}%)\n\n"
                        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
                    )
                    await self.send_to_group(msg)
                    # Gửi subscribers
                    for uid in self.subscribers.copy():
                        try:
                            await self.application.bot.send_message(uid, msg, parse_mode="Markdown")
                        except Exception:
                            self.subscribers.discard(uid)

    async def check_scheduled_updates(self, prices: Dict):
        """Gửi định kỳ gần các mốc 08:30, 12:00, 16:00 (±2 phút)."""
        now = datetime.now(VN_TZ)
        target = [(8, 30), (12, 0), (16, 0)]
        for hh, mm in target:
            if abs((now.hour * 60 + now.minute) - (hh * 60 + mm)) <= 2:
                await self.send_scheduled_update(prices)
                break

    async def send_scheduled_update(self, prices: Dict):
        """Nội dung cập nhật định kỳ (có spread)."""
        lines = ["🔔 *CẬP NHẬT GIÁ BẠC ĐỊNH KỲ*\n"]
        main_products = [
            "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG",
            "BẠC THỎI PHÚ QUÝ 999 10 LƯỢNG, 5 LƯỢNG",
        ]
        for name in main_products:
            if name in prices:
                d = prices[name]
                lines.append(f"🔸 *{name}*")
                lines.append(f"   💵 Mua: {self.format_price(d['buy_price'])} VND")
                if d["sell_price"]:
                    lines.append(f"   💴 Bán: {self.format_price(d['sell_price'])} VND")
                    sp, pct = self.calculate_spread(d["buy_price"], d["sell_price"])
                    lines.append(f"   📊 Chênh lệch: {self.format_price(sp)} VND ({pct:.2f}%)")
                lines.append("")

        # Biến động so với lần lưu gần nhất
        base = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
        if base in prices and base in self.last_prices:
            cur = prices[base]["buy_price"]
            last = self.last_prices[base]["buy_price"]
            if last > 0:
                delta = cur - last
                pct = delta / last * 100
                if abs(pct) >= 0.1:
                    emoji = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
                    lines.append(f"{emoji} *Biến động:* {'+' if delta>0 else ''}{self.format_price(delta)} VND ({pct:+.2f}%)\n")

        lines.append(f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}")
        lines.append("🌐 *Nguồn:* giabac.phuquygroup.vn")

        await self.send_to_group("\n".join(lines))


# ========= Khởi tạo bot =========
bot = SilverPriceBot()

# ========= Handlers =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🏦 *Chào mừng đến với Bot Giá Bạc!*\n\n"
        "📋 *Lệnh:*\n"
        "• /price - Giá hiện tại\n"
        "• /history - Lịch sử 24h\n"
        "• /subscribe - Đăng ký thông báo\n"
        "• /unsubscribe - Hủy đăng ký\n"
        "• /status - Trạng thái bot\n\n"
        "🔄 *Tự động:*\n"
        "• 08:30, 12:00, 16:00\n"
        "• Cảnh báo thay đổi > 2%"
    )
    keyboard = [
        [InlineKeyboardButton("📈 Giá hiện tại", callback_data="price"),
         InlineKeyboardButton("📊 Chênh lệch", callback_data="spread")],
        [InlineKeyboardButton("🔔 Đăng ký", callback_data="subscribe"),
         InlineKeyboardButton("📚 Lịch sử", callback_data="history")],
    ]
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang lấy giá...")
    prices = await bot.fetch_silver_prices()
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu.")
        return

    lines = ["💰 *GIÁ BẠC HIỆN TẠI*\n"]
    for product, d in prices.items():
        sp, pct = bot.calculate_spread(d["buy_price"], d["sell_price"])
        lines.append(f"🔸 *{product}*")
        lines.append(f"   💵 Mua: {bot.format_price(d['buy_price'])} VND")
        if d["sell_price"]:
            lines.append(f"   💴 Bán: {bot.format_price(d['sell_price'])} VND")
            lines.append(f"   📊 Chênh lệch: {bot.format_price(sp)} VND ({pct:.2f}%)")
        lines.append("")
    lines.append(f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot.price_history:
        await update.message.reply_text("📭 Chưa có lịch sử.")
        return
    last = bot.price_history[-1]
    ts = last["timestamp"].strftime("%H:%M %d/%m/%Y")
    await update.message.reply_text(f"📚 Hiện lưu {len(bot.price_history)} bản ghi. Bản gần nhất: {ts}")

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot.subscribers.add(update.effective_user.id)
    await update.message.reply_text("🔔 Đã đăng ký thông báo!")

async def cmd_unsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot.subscribers.discard(update.effective_user.id)
    await update.message.reply_text("🔕 Đã hủy đăng ký.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        f"🤖 *TRẠNG THÁI*\n\n"
        f"📊 History: {len(bot.price_history)}\n"
        f"👥 Subs: {len(bot.subscribers)}\n"
        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
        f"🌐 Railway (PORT={PORT})"
    )
    await update.message.reply_text(txt, parse_mode="Markdown")

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "price":
        await q.edit_message_text("🔄 Đang lấy giá...")
        prices = await bot.fetch_silver_prices()
        if prices:
            base = "BẠC MIẾNG PHÚ QUÝ 999 1 LƯỢNG"
            if base in prices:
                d = prices[base]
                sp, pct = bot.calculate_spread(d["buy_price"], d["sell_price"])
                msg = (
                    "💰 *GIÁ BẠC HIỆN TẠI*\n\n"
                    f"🔸 *{base}*\n"
                    f"💵 Mua: {bot.format_price(d['buy_price'])} VND\n"
                    + (f"💴 Bán: {bot.format_price(d['sell_price'])} VND\n" if d["sell_price"] else "")
                    + (f"📊 Chênh lệch: {bot.format_price(sp)} VND ({pct:.2f}%)\n" if d["sell_price"] else "")
                    + f"\n🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
                )
                await q.edit_message_text(msg, parse_mode="Markdown")
            else:
                await q.edit_message_text("Không tìm thấy sản phẩm chính.")
        else:
            await q.edit_message_text("❌ Không thể lấy dữ liệu.")
    elif q.data == "subscribe":
        bot.subscribers.add(q.from_user.id)
        await q.edit_message_text("🔔 Đã đăng ký thông báo!")
    elif q.data == "history":
        await q.edit_message_text(f"📚 Số bản ghi: {len(bot.price_history)}")
    elif q.data == "spread":
        prices = await bot.fetch_silver_prices()
        if not prices:
            await q.edit_message_text("❌ Không thể lấy dữ liệu.")
            return
        lines = ["📊 *BẢNG CHÊNH LỆCH (mẫu)*\n"]
        for product, d in list(prices.items())[:5]:
            sp, pct = bot.calculate_spread(d["buy_price"], d["sell_price"])
            lines.append(f"• {product}: {bot.format_price(sp)} VND ({pct:.2f}%)")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown")

# ========= Health server =========
from aiohttp import web

async def _health(request):
    return web.Response(
        text=(
            "🤖 Silver Price Bot is running!\n"
            f"⏰ {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
            f"📊 History: {len(bot.price_history)}\n"
            f"👥 Subs: {len(bot.subscribers)}"
        )
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
    # Luôn bật health server trước để Railway không 503
    await start_health_server()
    logger.info("🌐 Health server started on port %s", PORT)

    # Nếu thiếu BOT_TOKEN, vẫn treo app để /health sống
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Chưa cấu hình BOT_TOKEN! Chỉ chạy /health.")
        try:
            await asyncio.Future()  # run forever
        except asyncio.CancelledError:
            return

    # Tạo Telegram Application
    application = Application.builder().token(BOT_TOKEN).build()
    bot.application = application

    # Handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("subscribe", cmd_sub))
    application.add_handler(CommandHandler("unsubscribe", cmd_unsub))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CallbackQueryHandler(on_button))

    # Monitoring background
    bot.monitoring_task = asyncio.create_task(bot.price_monitoring_loop())

    logger.info("🤖 Bot Giá Bạc khởi động (polling)...")

    # ✅ Trình tự async đúng (không dùng run_polling trong event loop)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Future()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
