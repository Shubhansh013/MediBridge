from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        ContextTypes,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = Any
    InlineKeyboardButton = Any
    InlineKeyboardMarkup = Any
    ApplicationBuilder = CommandHandler = MessageHandler = CallbackQueryHandler = filters = None

    class _ContextTypes:
        DEFAULT_TYPE = Any
    ContextTypes = _ContextTypes

import mock_db
from medibridge_db.models import (
    HistoryChangeType,
    Pharmacy,
    StockHistory,
    StockStatus,
    User,
    UserRole,
)
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge.services.proposal_service import (
    ProposalServiceResult,
    ProposalServiceStatus,
    process_stock_message,
)
from medibridge.services.confirmation_service import (
    ConfirmationResult,
    confirm_and_apply_proposal,
    reject_proposal_workflow,
)
from medibridge.services.history_service import (
    UndoExecutionResult,
    orchestrate_undo,
)
from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError,
    ProposalExpiredError,
    ProposalNotPendingError,
    UserNotLinkedToPharmacyError,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("medibridge.bot")

# Bot token from environment variable or hackathon demo fallback
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8803435363:AAEO79TaBl3Irs4P27aBAD1eKs65m79y5tI")

# In-memory dictionary to track mode per user for demoing
# Modes: "patient" (default) or "chemist"
USER_MODES: Dict[int, str] = {}

# Lazy-loaded backend operations adapter
_adapter: Optional[DBOperationsAdapter] = None


def get_adapter() -> DBOperationsAdapter:
    global _adapter
    if _adapter is None:
        _adapter = DBOperationsAdapter(session_factory=mock_db._SessionFactory)
    return _adapter


def resolve_chemist_context(telegram_user_id: int) -> Tuple[int, int, str]:
    """Resolves a chemist user ID, pharmacy ID, and pharmacy name from Telegram user ID.

    If the telegram user is registered, uses their linked pharmacy.
    Otherwise, defaults to the primary demo chemist (Amit Verma at MedPlus Saket).
    """
    mock_db.init_db()
    with mock_db.get_session() as session:
        # Check if telegram user is a registered chemist
        user = session.query(User).filter_by(telegram_user_id=telegram_user_id).first()
        if user and user.role in (UserRole.chemist, UserRole.pharmacist):
            if user.pharmacy_links:
                for link in user.pharmacy_links:
                    pharm = link.pharmacy
                    if pharm and pharm.is_active:
                        return user.id, pharm.id, pharm.name

        # Fallback to demo chemist: User ID 3 (Amit Verma), Pharmacy ID 1 (MedPlus Saket)
        demo_chemist = session.query(User).filter_by(role=UserRole.chemist).first()
        demo_pharm = session.query(Pharmacy).filter_by(is_active=True).first()
        user_id = demo_chemist.id if demo_chemist else 3
        pharm_id = demo_pharm.id if demo_pharm else 1
        pharm_name = demo_pharm.name if demo_pharm else "MedPlus Saket"
        return user_id, pharm_id, pharm_name


def handle_chemist_stock_text(
    text: str, user_id: int, pharmacy_id: int, source_ref: str = "telegram"
) -> Tuple[Optional[ProposalServiceResult], Optional[str]]:
    """Processes a chemist stock message via proposal_service.process_stock_message."""
    adapter = get_adapter()
    try:
        result = process_stock_message(
            raw_message=text,
            pharmacy_id=pharmacy_id,
            chemist_user_id=user_id,
            repository=adapter,
            source_message_ref=source_ref,
        )
        return result, None
    except Exception as exc:
        logger.error(f"Error processing stock message: {exc}", exc_info=True)
        return None, str(exc)


def handle_proposal_confirmation(
    proposal_id: int, user_id: int, pharmacy_id: int
) -> Tuple[bool, str, Optional[ConfirmationResult]]:
    """Applies a confirmed proposal atomically using confirmation_service."""
    adapter = get_adapter()
    try:
        conf_result = confirm_and_apply_proposal(
            proposal_id=proposal_id,
            user_id=user_id,
            pharmacy_id=pharmacy_id,
            repository=adapter,
        )
        msg = (
            f"? *Database Committed & Audited!*\n\n"
            f"? *Proposal:* `#{conf_result.proposal_id}`\n"
            f"? *Status:* *{conf_result.updated_inventory.stock_status.value}*\n"
            f"? *Current Quantity:* {conf_result.updated_inventory.quantity if conf_result.updated_inventory.quantity is not None else 0}\n\n"
            f"Patients will see this immediately on their next search.\n"
            f"_Tip: Made a mistake? Type `/undo` to revert this change._"
        )
        return True, msg, conf_result
    except ProposalNotPendingError:
        return False, "?? This proposal has already been confirmed or rejected.", None
    except ProposalExpiredError:
        return False, "?? This proposal has expired (exceeded 24h lifetime).", None
    except Exception as exc:
        logger.error(f"Confirmation failed: {exc}", exc_info=True)
        return False, f"?? Confirmation failed: {exc}", None


def handle_proposal_rejection(
    proposal_id: int, user_id: int, pharmacy_id: int, reason: str = "Cancelled by chemist"
) -> Tuple[bool, str]:
    """Rejects a proposal without modifying inventory."""
    adapter = get_adapter()
    try:
        reject_proposal_workflow(
            proposal_id=proposal_id,
            user_id=user_id,
            pharmacy_id=pharmacy_id,
            repository=adapter,
            reason=reason,
        )
        return True, f"? Proposal `#{proposal_id}` cancelled. No changes were made to the database."
    except Exception as exc:
        logger.error(f"Rejection failed: {exc}", exc_info=True)
        return False, f"?? Cancellation failed: {exc}"


def handle_undo_action(pharmacy_id: int, user_id: int) -> Tuple[bool, str]:
    """Undoes the latest stock change for the pharmacy."""
    adapter = get_adapter()
    try:
        histories = adapter.get_pharmacy_history(pharmacy_id=pharmacy_id, limit=50)
        if not histories:
            return False, "?? No stock update history found for this pharmacy to undo."

        # Find the latest stock_update that has not been undone
        target_history = None
        for h in reversed(histories):
            if h.change_type == HistoryChangeType.stock_update and not adapter.has_undo_for_history(h.id):
                target_history = h
                break

        if target_history is None:
            return False, "?? All previous stock changes have already been undone."

        undo_result = orchestrate_undo(
            history_id=target_history.id,
            user_id=user_id,
            pharmacy_id=pharmacy_id,
            repository=adapter,
        )
        msg = (
            f"?? *Stock Update Reversed!*\n\n"
            f"? *Reversed Change:* `#{undo_result.undone_history_id}`\n"
            f"? *Restored Status:* *{undo_result.restored_inventory.stock_status.value}*\n"
            f"? *Restored Quantity:* {undo_result.restored_inventory.quantity if undo_result.restored_inventory.quantity is not None else 0}\n"
            f"? *Audit Row:* Recorded immutable `UNDO` history row."
        )
        return True, msg
    except HistoryAlreadyUndoneError:
        return False, "?? That update has already been undone."
    except NewerChangeExistsError:
        return False, "?? A newer change exists for this medicine. Newer updates must be undone first."
    except Exception as exc:
        logger.error(f"Undo failed: {exc}", exc_info=True)
        return False, f"?? Undo failed: {exc}"


# ===========================================================================
# Telegram Handlers
# ===========================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_MODES[user_id] = "patient"

    welcome_text = (
        "?? *Welcome to MediBridge Delhi NCR!*\n\n"
        "Eliminating the frantic medicine hunt during emergencies.\n\n"
        "?? *Current Mode:* ?? *Patient*\n"
        "Type any medicine name (e.g., `Dolo 650` or `Calpol 650`) to find verified stock nearby.\n\n"
        "?? *Tip for Hackathon Demo:*\n"
        "Use `/mode` to switch between *Patient Mode* and *Chemist Mode* on the fly!\n"
        "Chemist updates require confirmation (AI Proposes, Human Confirms) and support `/undo`."
    )

    keyboard = [
        [
            InlineKeyboardButton("?? Patient Mode", callback_data="set_mode_patient"),
            InlineKeyboardButton("????? Chemist Mode", callback_data="set_mode_chemist"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("?? Patient Mode", callback_data="set_mode_patient"),
            InlineKeyboardButton("????? Chemist Mode", callback_data="set_mode_chemist"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("?? *Switch Role:*", reply_markup=reply_markup, parse_mode="Markdown")


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chemist_id, pharm_id, pharm_name = resolve_chemist_context(user_id)
    success, msg = handle_undo_action(pharm_id, chemist_id)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    current_mode = USER_MODES.get(user_id, "patient")

    is_chemist_message = any(word in text.lower() for word in ["khatam", "out of stock", "aaya", "available", "stock", "strips", "patte", "hai"])

    if current_mode == "chemist" or is_chemist_message:
        # === CHEMIST FLOW ===
        chemist_id, pharm_id, pharm_name = resolve_chemist_context(user_id)
        source_ref = f"tg_{update.message.message_id}"
        result, error = handle_chemist_stock_text(text, chemist_id, pharm_id, source_ref)

        if error or result is None:
            await update.message.reply_text(f"?? Error parsing message: {error or 'Unknown error'}")
            return

        if result.status == ProposalServiceStatus.UNAUTHORIZED:
            await update.message.reply_text(f"? User is not authorized to update inventory for *{pharm_name}*.", parse_mode="Markdown")
            return

        if result.created_proposals:
            for prop in result.created_proposals:
                intent_label = prop.parsed_intent.value if hasattr(prop.parsed_intent, 'value') else str(prop.parsed_intent)
                status_emoji = "??" if "IN" in intent_label else "??"
                flagged_msg = "\n?? *Flagged for pharmacist review:* Schedule H or low confidence." if prop.flagged_for_review else ""

                keyboard = [
                    [
                        InlineKeyboardButton(f"? Confirm: Mark {intent_label}", callback_data=f"confirm_prop_{prop.id}"),
                        InlineKeyboardButton("? Cancel", callback_data=f"cancel_prop_{prop.id}"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"?? *Proposed Stock Update:*\n\n"
                    f"? *Pharmacy:* {pharm_name}\n"
                    f"? *Medicine:* `{prop.brand_name}` ({prop.parsed_salt or 'Generic'})\n"
                    f"? *Target Status:* {status_emoji} *{intent_label}*\n"
                    f"? *Quantity:* {prop.parsed_quantity if prop.parsed_quantity is not None else 'N/A'}\n"
                    f"? *Proposal ID:* `#{prop.id}` (Expires in 24h){flagged_msg}\n\n"
                    f"_Please confirm to commit this update to the living database._",
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
        else:
            reason = result.reason or "Could not parse or catalogue match medicine."
            await update.message.reply_text(
                f"?? *Could not create proposal:*\n_{reason}_\n\n"
                f"?? *Tip:* Try sending: `Dolo 650 20 strips available` or `Dolo 650 khatam`",
                parse_mode="Markdown",
            )

    else:
        # === PATIENT FLOW ===
        results = mock_db.search_medicine(text)

        if not results:
            await update.message.reply_text(
                f"? *No pharmacy found with '{text}' in stock nearby.*\n\n"
                f"Try searching for `Dolo 650`, `Calpol 650`, `Crocin 650`, or `Augmentin 625`.",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text(f"?? Found *{len(results)}* availability records for `{text}`:")

        for res in results:
            status_str = "?? IN STOCK" if res["is_available"] else "?? OUT OF STOCK"
            maps_url = f"https://www.google.com/maps/dir/?api=1&destination={res['lat']},{res['lng']}"

            card = (
                f"?? *{res['pharmacy_name']}*\n"
                f"?? {res['locality']}\n"
                f"?? *Item:* {res['medicine_name']} ({res['salt']})\n"
                f"?? *Status:* {status_str}\n"
                f"?? *Freshness:* Verified at {res['last_updated']}\n"
            )

            if res["is_substitute"]:
                card += f"\n?? *Salt Equivalent Alternative:*\n_{res['substitute_note']}_\n"

            keyboard = [[InlineKeyboardButton("??? Open Google Maps Directions", url=maps_url)]]
            await update.message.reply_text(card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    data = query.data

    # Mode switching
    if data == "set_mode_patient":
        USER_MODES[user_id] = "patient"
        await query.edit_message_text("? Switched to *Patient Mode*! Send a medicine name (e.g. `Dolo 650`).", parse_mode="Markdown")
        return
    elif data == "set_mode_chemist":
        USER_MODES[user_id] = "chemist"
        _, _, pharm_name = resolve_chemist_context(user_id)
        await query.edit_message_text(
            f"????? Switched to *Chemist Mode ({pharm_name})*!\n\n"
            f"Send a quick stock update:\n"
            f"?? `Dolo 650 khatam`\n"
            f"?? `Calpol 650 20 strips available`\n"
            f"?? Send `/undo` to revert last change.",
            parse_mode="Markdown",
        )
        return

    # Proposal confirmation
    if data.startswith("confirm_prop_"):
        prop_id = int(data.split("_")[2])
        chemist_id, pharm_id, _ = resolve_chemist_context(user_id)
        success, msg, _ = handle_proposal_confirmation(prop_id, chemist_id, pharm_id)
        await query.edit_message_text(msg, parse_mode="Markdown")
        return

    # Proposal rejection
    if data.startswith("cancel_prop_"):
        prop_id = int(data.split("_")[2])
        chemist_id, pharm_id, _ = resolve_chemist_context(user_id)
        success, msg = handle_proposal_rejection(prop_id, chemist_id, pharm_id)
        await query.edit_message_text(msg, parse_mode="Markdown")
        return

    # Legacy confirmation callback
    if data.startswith("confirm_"):
        _, med_name, status_code = data.split("_", 2)
        is_available = status_code == "1"
        chemist_id, pharm_id, pharm_name = resolve_chemist_context(user_id)
        mock_db.update_stock(pharm_id, med_name, is_available)
        status_label = "?? IN STOCK" if is_available else "?? OUT OF STOCK"
        await query.edit_message_text(
            f"? *Database Committed!* \n\n"
            f"`{med_name}` at *{pharm_name}* is now marked as *{status_label}*.\n"
            f"Patients will see this immediately on their next search.\n"
            f"_Tip: Type `/undo` to revert._",
            parse_mode="Markdown",
        )
    elif data == "cancel_update":
        await query.edit_message_text("? Update cancelled. No changes were made to the database.")


def main():
    if not TELEGRAM_AVAILABLE:
        print("python-telegram-bot is not installed. Run: pip install python-telegram-bot")
        return

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n" + "="*60)
        print("??  ATTENTION: TELEGRAM BOT TOKEN MISSING!")
        print("Please paste your token into bot.py or run:")
        print("set TELEGRAM_BOT_TOKEN=your_token_from_botfather")
        print("="*60 + "\n")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("?? MediBridge Telegram Bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
