import logging
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import mock_db

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Put your Telegram Bot Token here or set it in your environment
BOT_TOKEN = "8803435363:AAEO79TaBl3Irs4P27aBAD1eKs65m79y5tI"

# In-memory dictionary to track mode per user for easy hackathon demoing
# Modes: "patient" (default) or "chemist"
USER_MODES = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_MODES[user_id] = "patient"  # Default to patient

    welcome_text = (
        "🏥 *Welcome to MediBridge Delhi NCR!*\n\n"
        "Eliminating the frantic medicine hunt during emergencies.\n\n"
        "👉 *Current Mode:* 🤒 *Patient*\n"
        "Type any medicine name (e.g., `Dolo 650` or `Calpol 650`) to find verified stock nearby.\n\n"
        "💡 *Tip for Hackathon Demo:*\n"
        "Use `/mode` to switch between *Patient Mode* and *Chemist Mode* on the fly!"
    )

    keyboard = [
        [
            InlineKeyboardButton("🤒 Patient Mode", callback_data="set_mode_patient"),
            InlineKeyboardButton("👨‍⚕️ Chemist Mode (Sharma Medicos)", callback_data="set_mode_chemist"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🤒 Patient Mode", callback_data="set_mode_patient"),
            InlineKeyboardButton("👨‍⚕️ Chemist Mode", callback_data="set_mode_chemist"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔄 *Switch Role:*", reply_markup=reply_markup, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    current_mode = USER_MODES.get(user_id, "patient")

    # Detect if user wrote a chemist-like message even in patient mode
    is_chemist_message = any(word in text.lower() for word in ["khatam", "out of stock", "aaya", "available", "stock"])

    if current_mode == "chemist" or is_chemist_message:
        # === CHEMIST FLOW ===
        text_lower = text.lower()
        is_out = any(w in text_lower for w in ["khatam", "out", "nahi", "nil", "finish"])
        status_action = "OUT OF STOCK" if is_out else "IN STOCK"

        # Simple extraction: strip out the status words to find the medicine name
        med_name = re.sub(r"(khatam|out of stock|out|aaya|stock|available|nahi hai)", "", text, flags=re.IGNORECASE).strip()
        if not med_name:
            med_name = "Dolo 650"

        # Ask for confirmation via Inline Buttons (AI Proposes, Human Confirms)
        keyboard = [
            [
                InlineKeyboardButton(f"✅ Confirm: Mark {status_action}", callback_data=f"confirm_{med_name}_{1 if not is_out else 0}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_update")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"⚠️ *Proposed Stock Update:*\n\n"
            f"• *Pharmacy:* Sharma Medicos (Rohini Sec 14)\n"
            f"• *Medicine:* `{med_name}`\n"
            f"• *Target Status:* *{status_action}*\n\n"
            f"_Please confirm to commit this update to the living database._",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    else:
        # === PATIENT FLOW ===
        results = mock_db.search_medicine(text)

        if not results:
            await update.message.reply_text(
                f"❌ *No pharmacy found with '{text}' in stock nearby.*\n\n"
                f"Try searching for `Dolo 650`, `Calpol 650`, or `Azithral 500`.",
                parse_mode="Markdown"
            )
            return

        await update.message.reply_text(f"🔍 Found *{len(results)}* availability records for `{text}`:")

        for res in results:
            status_str = "🟢 IN STOCK" if res["is_available"] else "🔴 OUT OF STOCK"
            maps_url = f"https://www.google.com/maps/dir/?api=1&destination={res['lat']},{res['lng']}"

            card = (
                f"🏥 *{res['pharmacy_name']}*\n"
                f"📍 {res['locality']}\n"
                f"💊 *Item:* {res['medicine_name']} ({res['salt']})\n"
                f"📦 *Status:* {status_str}\n"
                f"🕒 *Freshness:* Verified at {res['last_updated']}\n"
            )

            if res["is_substitute"]:
                card += f"\n💡 *Salt Equivalent Alternative:*\n_{res['substitute_note']}_\n"

            keyboard = [[InlineKeyboardButton("🗺️ Open Google Maps Directions", url=maps_url)]]
            await update.message.reply_text(card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    data = query.data

    # Mode switching
    if data == "set_mode_patient":
        USER_MODES[user_id] = "patient"
        await query.edit_message_text("✅ Switched to *Patient Mode*! Send a medicine name (e.g. `Dolo 650`).", parse_mode="Markdown")
        return
    elif data == "set_mode_chemist":
        USER_MODES[user_id] = "chemist"
        await query.edit_message_text(
            "👨‍⚕️ Switched to *Chemist Mode (Sharma Medicos)*!\n\nSend a quick update:\n👉 `Dolo 650 khatam`\n👉 `Calpol 650 aaya`",
            parse_mode="Markdown"
        )
        return

    # Stock update confirmation
    if data.startswith("confirm_"):
        _, med_name, status_code = data.split("_", 2)
        is_available = status_code == "1"

        # Update in SQLite DB for Pharmacy 1 (Sharma Medicos)
        mock_db.update_stock(1, med_name, is_available)
        status_label = "🟢 IN STOCK" if is_available else "🔴 OUT OF STOCK"

        await query.edit_message_text(
            f"✅ *Database Committed!* \n\n"
            f"`{med_name}` at *Sharma Medicos* is now marked as *{status_label}*.\n"
            f"Patients will see this immediately on their next search.",
            parse_mode="Markdown"
        )
    elif data == "cancel_update":
        await query.edit_message_text("❌ Update cancelled. No changes were made to the database.")

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n" + "="*60)
        print("⚠️  ATTENTION: TELEGRAM BOT TOKEN MISSING!")
        print("Please paste your token into bot.py or run:")
        print("set TELEGRAM_BOT_TOKEN=your_token_from_botfather")
        print("="*60 + "\n")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🚀 MediBridge Telegram Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
