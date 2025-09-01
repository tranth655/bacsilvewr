#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silver Price Bot - Notify on ANY change (with persistent subscribers)
- Polls regularly (POLL_SECONDS) and notifies immediately if price changed
- Persists subscribers to /app/subscribers.json (works on Railway)
- Health server at /health
- PTB v20.7, async-friendly
"""

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
POLL_SECONDS = int(str(os.environ.get("POLL_SECONDS", "60")).strip().strip('"').strip("'"))  # robust parsing

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

    # -------- scraping --------
    async def fetch_silver_prices(self) -> Dict[str, Dict]:
        """Fetch & parse prices from website."""
        try:
            loop = asyncio.get_event_loop()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
            resp = await loop.run_in_executor(
                None, lambda: requests.get(PRICE_URL, headers=headers, timeout=15)
            )
            if resp.status_code == 200:
                return self.parse_prices(resp.text)
            logger.error("HTTP %s when fetching prices", resp.status_code)
            return {}
        except Exception as e:
            logger.exception("Fetch error: %s", e)
            return {}

    def parse_prices(self, html: str) -> Dict[str, Dict]:
        """Parse prices from HTML."""
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

                if "BẠC" not in product.upper():
                    continue

                buy = self._parse_price_num(buy_raw)
                sell = self._parse_price_num(sell_raw)

                if buy > 0:
                    prices[product] = {
                        "unit": unit,
                        "buy_price": buy,
                        "sell_price": sell if sell > 0 else None,
                        "timestamp": now,
                    }
            logger.info("Parsed %d products", len(prices))
            return prices
        except Exception as e:
            logger.exception("Parse error: %s", e)
            return {}

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

    # -------- notifications --------
    async def send_to_group(self, text: str):
        if GROUP_CHAT_ID and GROUP_CHAT_ID != "YOUR_GROUP_CHAT_ID" and self.application:
            try:
                await self.application.bot.send_message(GROUP_CHAT_ID, text, parse_mode="Markdown")
            except Exception as e:
                logger.error("Send group error: %s", e)

    async def notify_change(self, product: str, prev: Dict, cur: Dict):
        """Notify immediately when price changes (buy and/or sell)."""
        prev_buy = prev.get("buy_price", 0)
        cur_buy = cur.get("buy_price", 0)
        prev_sell = prev.get("sell_price", None)
        cur_sell = cur.get("sell_price", None)

        delta_buy = cur_buy - prev_buy
        pct = (delta_buy / prev_buy * 100) if prev_buy else 0.0
        emoji = "📈" if delta_buy > 0 else "📉" if delta_buy < 0 else "↔️"

        lines = [
            "🔔 *GIÁ BẠC THAY ĐỔI*",
            f"\n🔸 *{product}*",
            f"💵 Mua: {self.fmt(prev_buy)} ➜ {self.fmt(cur_buy)} VND ({pct:+.2f}%) {emoji}",
        ]
        if prev_sell is not None or cur_sell is not None:
            prev_sell_txt = self.fmt(prev_sell) if prev_sell else "—"
            cur_sell_txt = self.fmt(cur_sell) if cur_sell else "—"
            lines.append(f"💴 Bán: {prev_sell_txt} ➜ {cur_sell_txt} VND")

        sp, spct = self.spread(cur_buy, cur_sell)
        if sp:
            lines.append(f"📊 Chênh lệch hiện tại: {self.fmt(sp)} VND ({spct:.2f}%)")

        lines.append(f"\n🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}")
        msg = "\n".join(lines)

        await self.send_to_group(msg)
        # also notify subscribers
        for uid in self.subscribers.copy():
            try:
                await self.application.bot.send_message(uid, msg, parse_mode="Markdown")
            except Exception:
                self.subscribers.discard(uid)
                save_subscribers(self.subscribers)

    # -------- monitor loop --------
    async def monitor_loop(self):
        logger.info("🔄 Start monitoring every %ss ...", POLL_SECONDS)
        while True:
            try:
                current = await self.fetch_silver_prices()
                if current:
                    # save history
                    self.price_history.append({"timestamp": datetime.now(VN_TZ), "prices": current.copy()})
                    self.price_history = self.price_history[-200:]

                    # compare & notify
                    await self.compare_and_notify(current)

                    # update last
                    self.last_prices = current.copy()
                else:
                    logger.warning("⚠️ No price data fetched")

                await asyncio.sleep(POLL_SECONDS)
            except Exception as e:
                logger.exception("Monitor error: %s", e)
                await asyncio.sleep(30)

    async def compare_and_notify(self, current: Dict[str, Dict]):
        """
        Compare current vs last; if any product buy/sell changed -> notify immediately.
        """
        if not self.last_prices:
            return  # first run: establish baseline

        for product, cur in current.items():
            if product not in self.last_prices:
                # new product appears -> treat as change
                await self.notify_change(product, {"buy_price": 0, "sell_price": None}, cur)
                continue

            prev = self.last_prices[product]
            if (cur["buy_price"] != prev.get("buy_price") or
                (cur["sell_price"] or 0) != (prev.get("sell_price") or 0)):
                await self.notify_change(product, prev, cur)

# ========= Global bot instance =========
bot = SilverPriceBot()

# ========= Telegram handlers =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🏦 *Bot Giá Bạc*\n\n"
        "Bot theo dõi liên tục và *báo ngay khi giá thay đổi*.\n\n"
        "📋 Lệnh:\n"
        "• /price - Giá hiện tại\n"
        "• /subscribe - Đăng ký nhận cảnh báo\n"
        "• /unsubscribe - Hủy đăng ký\n"
        "• /status - Trạng thái bot\n"
    )
    kb = [
        [InlineKeyboardButton("📈 Giá hiện tại", callback_data="price"),
         InlineKeyboardButton("🔔 Đăng ký", callback_data="subscribe")],
        [InlineKeyboardButton("🔕 Hủy đăng ký", callback_data="unsubscribe")],
    ]
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Đang lấy giá...")
    prices = await bot.fetch_silver_prices()
    if not prices:
        await update.message.reply_text("❌ Không thể lấy dữ liệu.")
        return

    lines = ["💰 *GIÁ BẠC HIỆN TẠI*\n"]
    for product, d in prices.items():
        lines.append(f"🔸 *{product}*")
        lines.append(f"   💵 Mua: {bot.fmt(d['buy_price'])} VND")
        if d["sell_price"]:
            sp, pct = bot.spread(d['buy_price'], d['sell_price'])
            lines.append(f"   💴 Bán: {bot.fmt(d['sell_price'])} VND")
            lines.append(f"   📊 Chênh lệch: {bot.fmt(sp)} VND ({pct:.2f}%)")
        lines.append("")
    lines.append(f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot.subscribers.add(uid)
    save_subscribers(bot.subscribers)
    await update.message.reply_text("🔔 Đã đăng ký nhận cảnh báo!")

async def cmd_unsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot.subscribers.discard(uid)
    save_subscribers(bot.subscribers)
    await update.message.reply_text("🔕 Đã hủy đăng ký.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        f"🤖 *TRẠNG THÁI*\n\n"
        f"📊 History: {len(bot.price_history)}\n"
        f"👥 Subscribers: {len(bot.subscribers)}\n"
        f"⏱️ Poll mỗi: {POLL_SECONDS}s\n"
        f"🕐 {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
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
        await q.edit_message_text("🔔 Đã đăng ký nhận cảnh báo!")
    elif q.data == "unsubscribe":
        uid = q.from_user.id
        bot.subscribers.discard(uid)
        save_subscribers(bot.subscribers)
        await q.edit_message_text("🔕 Đã hủy đăng ký.")

# ========= Health server =========
from aiohttp import web

async def _health(request):
    return web.Response(
        text=(
            "🤖 Silver Price Bot is running!\n"
            f"⏰ {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}\n"
            f"📊 History: {len(bot.price_history)}\n"
            f"👥 Subs: {len(bot.subscribers)}\n"
            f"⏱️ Poll: {POLL_SECONDS}s"
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
    # health first
    await start_health_server()
    logger.info("🌐 Health server started on port %s", PORT)

    # if token missing, keep only /health
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN missing. Running health only.")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return

    application = Application.builder().token(BOT_TOKEN).build()
    bot.application = application

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("subscribe", cmd_sub))
    application.add_handler(CommandHandler("unsubscribe", cmd_unsub))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CallbackQueryHandler(on_button))

    # start monitor loop
    bot.monitoring_task = asyncio.create_task(bot.monitor_loop())

    logger.info("🤖 Bot starting (polling)...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
