import json
import logging
import os
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================= CONFIGURATION =================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = -1001234567890  # Private group/channel ID where payment requests go
ADMIN_IDS = [123456789]  # Telegram user ID(s) of admins who can approve & set prices
PAID_CHANNEL_LINK = "https://t.me/+YourHardcodedPrivateChannelInviteLink"
UPI_QR_IMAGE_PATH = "upi_qr.jpg"  # Local image file of your UPI QR code
UPI_ID = "yourupi@okaxis"
PRICES_FILE = "prices.json"

# Default prices in INR (60d = 560, 30d = 390; others chosen sensibly)
DEFAULT_PRICES = {
    "1hr": 50,
    "6hr": 110,
    "1day": 160,
    "7day": 250,
    "14day": 320,
    "30day": 390,
    "60day": 560,
}

DURATION_LABELS = {
    "1hr": "1 Hour",
    "6hr": "6 Hours",
    "1day": "1 Day",
    "7day": "7 Days",
    "14day": "14 Days",
    "30day": "30 Days",
    "60day": "60 Days",
}
# =================================================


def load_prices() -> dict[str, int]:
    if os.path.exists(PRICES_FILE):
        try:
            with open(PRICES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            prices = DEFAULT_PRICES.copy()
            for k, v in data.items():
                if k in prices and isinstance(v, (int, float)) and v > 0:
                    prices[k] = int(v)
            return prices
        except Exception as e:
            logger.error("Failed to load prices.json: %s", e)
    save_prices(DEFAULT_PRICES)
    return DEFAULT_PRICES.copy()


def save_prices(prices: dict[str, int]) -> None:
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, indent=2)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def plans_keyboard() -> InlineKeyboardMarkup:
    prices = load_prices()
    order = ["1hr", "6hr", "1day", "7day", "14day", "30day", "60day"]
    rows = []
    for i in range(0, len(order), 2):
        row = []
        for dur in order[i : i + 2]:
            row.append(
                InlineKeyboardButton(
                    f"{DURATION_LABELS[dur]} — ₹{prices[dur]}",
                    callback_data=f"select:{dur}",
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🎮 *BGMI Key Store*\n\n"
        "Select a key duration and price below to purchase:\n"
        "• Pay via UPI QR code\n"
        "• Wait for admin verification\n"
        "• Receive your key + private channel access"
    )
    await update.message.reply_text(
        text, reply_markup=plans_keyboard(), parse_mode="Markdown"
    )


async def prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prices = load_prices()
    lines = ["💰 *Current Key Prices:*\n"]
    for dur, label in DURATION_LABELS.items():
        lines.append(f"• *{label}* (`{dur}`): ₹{prices[dur]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def setprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /setprice <duration> <amount>  e.g. /setprice 30day 420"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to change prices.")
        return

    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text(
            "⚠️ Usage: `/setprice <1hr|6hr|1day|7day|14day|30day|60day> <price_in_rs>`\n"
            "Example: `/setprice 30day 420`",
            parse_mode="Markdown",
        )
        return

    dur = args[0].lower()
    if dur not in DEFAULT_PRICES:
        await update.message.reply_text(
            f"❌ Invalid duration `{dur}`. Valid keys: {', '.join(DEFAULT_PRICES.keys())}",
            parse_mode="Markdown",
        )
        return

    try:
        amount = int(args[1])
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Price must be a positive whole number (in ₹).")
        return

    prices = load_prices()
    old_price = prices[dur]
    prices[dur] = amount
    save_prices(prices)

    await update.message.reply_text(
        f"✅ Updated *{DURATION_LABELS[dur]}* (`{dur}`): ₹{old_price} → *₹{amount}*",
        parse_mode="Markdown",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("select:"):
        dur = data.split(":", 1)[1]
        if dur not in DEFAULT_PRICES:
            await query.message.reply_text("❌ Invalid plan selected.")
            return

        prices = load_prices()
        price = prices[dur]
        label = DURATION_LABELS[dur]
        order_id = f"ORD{int(time.time())}{query.from_user.id % 1000}"

        orders = context.bot_data.setdefault("orders", {})
        orders[order_id] = {
            "order_id": order_id,
            "user_id": query.from_user.id,
            "username": query.from_user.username or "N/A",
            "full_name": query.from_user.full_name,
            "duration": dur,
            "label": label,
            "price": price,
            "status": "pending",
        }

        pay_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ I Have Paid", callback_data=f"paid:{order_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Plans", callback_data="back_plans"
                    )
                ],
            ]
        )

        caption = (
            f"🧾 *Order Created:* `{order_id}`\n"
            f"⏳ *Plan:* {label}\n"
            f"💵 *Amount:* ₹{price}\n"
            f"🏦 *UPI ID:* `{UPI_ID}`\n\n"
            f"Scan the QR code above, pay *₹{price}*, then tap *I Have Paid*."
        )

        if os.path.exists(UPI_QR_IMAGE_PATH):
            with open(UPI_QR_IMAGE_PATH, "rb") as qr_file:
                await query.message.reply_photo(
                    photo=qr_file, caption=caption, reply_markup=pay_kb, parse_mode="Markdown"
                )
        else:
            await query.message.reply_text(
                f"⚠️ QR image (`{UPI_QR_IMAGE_PATH}`) not found on server.\n\n" + caption,
                reply_markup=pay_kb,
                parse_mode="Markdown",
            )

    elif data == "back_plans":
        await query.message.reply_text(
            "Select a plan:", reply_markup=plans_keyboard()
        )

    elif data.startswith("paid:"):
        order_id = data.split(":", 1)[1]
        orders = context.bot_data.get("orders", {})
        order = orders.get(order_id)

        if not order:
            await query.message.reply_text("⚠️ Order not found or expired.")
            return
        if order["status"] != "pending":
            await query.message.reply_text(f"ℹ️ Order `{order_id}` is already *{order['status']}*.", parse_mode="Markdown")
            return

        order["status"] = "awaiting_admin"
        user_handle = f"@{order['username']}" if order["username"] != "N/A" else order["full_name"]

        admin_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Approve & Enter Key", callback_data=f"approve:{order_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject", callback_data=f"reject:{order_id}"
                    ),
                ]
            ]
        )

        admin_msg = (
            f"🔔 *New Payment Claim*\n\n"
            f"• *Order ID:* `{order_id}`\n"
            f"• *Customer:* {user_handle} (`{order['user_id']}`)\n"
            f"• *Plan:* {order['label']} (`{order['duration']}`)\n"
            f"• *Amount:* ₹{order['price']}\n\n"
            f"Check your UPI app to confirm receipt before approving."
        )

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_msg,
            reply_markup=admin_kb,
            parse_mode="Markdown",
        )
        await query.message.reply_text(
            f"📩 Payment claim for `{order_id}` sent to admin for verification.\n"
            f"Once approved, your key and channel link will be sent here.",
            parse_mode="Markdown",
        )

    elif data.startswith("approve:"):
        if not is_admin(query.from_user.id):
            await query.answer("Only authorized admins can approve orders.", show_alert=True)
            return

        order_id = data.split(":", 1)[1]
        orders = context.bot_data.get("orders", {})
        order = orders.get(order_id)

        if not order or order["status"] == "completed":
            await query.answer("Order already processed or missing.", show_alert=True)
            return

        pending_keys = context.bot_data.setdefault("pending_key_input", {})
        pending_keys[query.from_user.id] = {
            "order_id": order_id,
            "admin_chat_id": query.message.chat_id,
            "admin_message_id": query.message.message_id,
        }

        await query.message.reply_text(
            f"🔑 Please reply in this chat with the *key* for order `{order_id}` "
            f"({order['label']} — ₹{order['price']}).",
            parse_mode="Markdown",
        )

    elif data.startswith("reject:"):
        if not is_admin(query.from_user.id):
            await query.answer("Only authorized admins can reject orders.", show_alert=True)
            return

        order_id = data.split(":", 1)[1]
        orders = context.bot_data.get("orders", {})
        order = orders.get(order_id)

        if not order:
            await query.answer("Order not found.", show_alert=True)
            return

        order["status"] = "rejected"
        await query.edit_message_text(
            query.message.text + f"\n\n❌ *REJECTED* by {query.from_user.full_name}",
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=(
                    f"❌ *Payment Not Verified*\n\n"
                    f"Your payment for Order `{order_id}` ({order['label']} — ₹{order['price']}) "
                    f"could not be verified. If you paid, contact support with your UTR/screenshot."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to notify buyer of rejection: %s", e)


async def handle_admin_key_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Captures the key typed by an admin after clicking 'Approve & Enter Key'."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not msg.text:
        return

    if not is_admin(user.id):
        return

    # Ignore commands like /setprice
    if msg.text.startswith("/"):
        return

    pending_keys = context.bot_data.get("pending_key_input", {})
    req = pending_keys.get(user.id)
    if not req:
        return

    key_value = msg.text.strip()
    order_id = req["order_id"]
    orders = context.bot_data.get("orders", {})
    order = orders.get(order_id)

    del pending_keys[user.id]

    if not order:
        await msg.reply_text(f"⚠️ Order `{order_id}` no longer exists.", parse_mode="Markdown")
        return

    order["status"] = "completed"
    order["key"] = key_value

    customer_msg = (
        f"🎉 *Payment Approved!*\n\n"
        f"• *Order ID:* `{order_id}`\n"
        f"• *Duration:* {order['label']}\n"
        f"• *Key:* `{key_value}`\n\n"
        f"📢 *Paid Private Channel:*\n{PAID_CHANNEL_LINK}"
    )

    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=customer_msg,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        await msg.reply_text(
            f"✅ Key delivered to user `{order['user_id']}` for order `{order_id}`.",
            parse_mode="Markdown",
        )
        try:
            await context.bot.edit_message_text(
                chat_id=req["admin_chat_id"],
                message_id=req["admin_message_id"],
                text=(
                    f"✅ *APPROVED & DELIVERED*\n"
                    f"Order `{order_id}` | Key: `{key_value}` | By: {user.full_name}"
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error("Failed to deliver key to user %s: %s", order["user_id"], e)
        await msg.reply_text(
            f"⚠️ Order marked completed, but failed to DM user `{order['user_id']}` "
            f"(they may have blocked the bot): `{e}`",
            parse_mode="Markdown",
        )


def main() -> None:
    load_prices()  # Ensure prices.json exists on startup
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("prices", prices_cmd))
    app.add_handler(CommandHandler("setprice", setprice_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_key_message)
    )

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
