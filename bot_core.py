# bot_core.py
from functools import wraps
import logging
from typing import Optional
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue

# Make sure to import load_known_users if you want to reload it periodically or on command
from config_loader import config, load_system_prompt, load_known_users
from ai_handler import get_ai_response
from chat_manager import DEFAULT_CHAT_HISTORY_FILE, ChatManager

logger = logging.getLogger(__name__)

base_system_prompt: str # Renamed from system_prompt_content
chat_manager: ChatManager

# --- Whitelist Check Decorator and Helper ---
def restricted_to_allowed_chats(func):
    """
    Decorator that restricts execution of a handler to chats specified in config.allowed_chat_ids.
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat_id = update.effective_chat.id if update.effective_chat else None
        
        if chat_id is None: # Should not happen for typical message/command updates
            logger.warning(f"Could not determine chat ID for update, cannot apply whitelist to {func.__name__}.")
            return # Or handle as disallowed

        if not config.allowed_chat_ids:
            logger.warning(
                f"Command '{func.__name__}' used in chat {chat_id}, but allowed_chat_ids is empty. "
                "Bot is globally restricted. Informing user."
            )
            if update.message:
                 await update.message.reply_text(
                    "I am not configured to operate in any chat at the moment. Please contact the administrator."
                )
            return

        if chat_id not in config.allowed_chat_ids:
            logger.info(
                f"Command '{func.__name__}' used in non-whitelisted chat {chat_id} by user {update.effective_user.id}. "
                "Informing user and ignoring."
            )
            if update.message:
                await update.message.reply_text(
                    "Sorry, I am not authorized to operate in this chat. Please contact the administrator."
                )
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def is_chat_allowed(chat_id: int) -> bool:
    """Helper to check if a specific chat_id is in the whitelist."""
    if not config.allowed_chat_ids: # If list is empty, no chat is allowed
        return False
    return chat_id in config.allowed_chat_ids
# --- End Whitelist Check ---

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"This chat's ID is: {update.message.chat_id}")
# Add to application: application.add_handler(CommandHandler("id", id_command))


@restricted_to_allowed_chats # Apply decorator
async def mint_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_query = ' '.join(context.args) if context.args else None
    if not user_query:
        user_query = "" # Default message if none provided

    chat_id = update.message.chat_id
    user_message_id = update.message.message_id # This is the ID of the "/mint" command message
    from_user_id = update.message.from_user.id

    logger.info(f"Received /mint (msg_id: {user_message_id}) from user {from_user_id} in chat {chat_id}: '{user_query}'")
    await update.message.reply_chat_action(constants.ChatAction.TYPING)
    
    # For /mint, there's no prior message in *this specific thread* being replied to by the user.
    # The AI history will just be this user message.
    initial_history_for_ai = [{"role": "user", "content": user_query}]
    
    ai_response_text = await get_ai_response(
        messages_history=initial_history_for_ai,
        base_system_prompt=base_system_prompt,
        user_id_for_current_message=from_user_id,
        # No reply_to_message_content for the initial /mint command
    )

    if ai_response_text:
        bot_reply_message = await update.message.reply_text(
            ai_response_text,
            reply_to_message_id=user_message_id # Bot replies to the /mint command
        )
        # Start new chat thread, keyed by the user's /mint command ID.
        # Store the /mint command and the bot's first reply.
        chat_manager.start_new_chat(
            initial_user_message_content=user_query,
            initial_user_telegram_message_id=user_message_id,
            chat_id=chat_id,
            bot_first_reply_telegram_message_id=bot_reply_message.message_id,
            bot_first_reply_content=ai_response_text
        )
    else:
        await update.message.reply_text(
            "Sorry, I couldn't get a response from the AI.",
            reply_to_message_id=user_message_id
        )

async def handle_reply_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_chat_allowed(update.message.chat_id): # Whitelist check
        logger.info(f"Reply in non-whitelisted chat {update.message.chat_id}. Ignoring.")
        return

    if not update.message or not update.message.text or not update.message.reply_to_message:
        return 

    # User's current message
    current_user_message_content = update.message.text
    current_user_message_id = update.message.message_id
    chat_id = update.message.chat_id
    from_user_id = update.message.from_user.id

    # The message the user is replying to (could be bot's or another user's if we extend later)
    replied_to_telegram_message_obj = update.message.reply_to_message
    replied_to_telegram_message_id = replied_to_telegram_message_obj.message_id

    logger.info(
        f"User {from_user_id} (msg_id:{current_user_message_id}) replied in chat {chat_id} "
        f"to Telegram message ID {replied_to_telegram_message_id}: '{current_user_message_content}'"
    )

    # Find the chat thread this reply belongs to
    # The key is the ID of the *original* /mint command that started this whole conversation.
    thread_key = chat_manager._find_thread_key_for_reply(replied_to_telegram_message_id, chat_id)

    if thread_key is None:
        logger.info(
            f"No active chat thread found containing replied-to message {replied_to_telegram_message_id} "
            f"in chat {chat_id}. User might be replying to an old/cleared message or a message not part of a bot thread. Ignoring."
        )
        # Optionally, inform the user:
        # await update.message.reply_text("Sorry, I can't find the context for that message. Please start a new conversation with /mint if needed.",
        #                                 reply_to_message_id=current_user_message_id)
        return

    # Retrieve the content of the message being replied to from our history
    message_being_replied_to_in_history = chat_manager.get_message_by_telegram_id(thread_key, replied_to_telegram_message_id)
    
    reply_to_content_for_ai: Optional[str] = None
    reply_to_role_for_ai: Optional[str] = None

    if message_being_replied_to_in_history:
        reply_to_content_for_ai = message_being_replied_to_in_history.get("content")
        reply_to_role_for_ai = message_being_replied_to_in_history.get("role")
        logger.debug(f"User is replying to: Role='{reply_to_role_for_ai}', Content='{reply_to_content_for_ai[:50]}...'")
    else:
        # This case should ideally be caught by thread_key being None,
        # but as a fallback, if the message isn't in *our* history but the thread was found.
        logger.warning(f"Thread {thread_key} found, but specific replied-to message {replied_to_telegram_message_id} not in its history. Proceeding without reply context for AI.")


    # Add user's current reply to the identified chat thread
    chat_manager.add_message_to_chat(
        thread_key=thread_key,
        role="user",
        content=current_user_message_content,
        telegram_message_id=current_user_message_id
    )

    # Get the full history for AI (it will be trimmed by ChatManager)
    # This history is a list of {"role": ..., "content": ...}
    current_full_history_for_ai = chat_manager.get_history_for_ai(thread_key)
    if not current_full_history_for_ai:
        logger.error(f"Could not retrieve history for an active chat thread: {thread_key}")
        return

    await update.message.reply_chat_action(constants.ChatAction.TYPING)
    
    ai_response_text = await get_ai_response(
        messages_history=current_full_history_for_ai, # Pass the history (last message is the current user's)
        base_system_prompt=base_system_prompt,
        user_id_for_current_message=from_user_id,
        reply_to_message_content=reply_to_content_for_ai, # Pass context of what's being replied to
        reply_to_message_role=reply_to_role_for_ai
    )

    if ai_response_text:
        new_bot_reply_message = await update.message.reply_text(
            ai_response_text,
            reply_to_message_id=current_user_message_id # Bot replies to the user's latest message
        )
        # Add AI's response to the same chat thread
        chat_manager.add_message_to_chat(
            thread_key=thread_key,
            role="assistant",
            content=ai_response_text,
            telegram_message_id=new_bot_reply_message.message_id
        )
    else:
        await update.message.reply_text(
            "Sorry, I couldn't get a response from the AI for your reply.",
            reply_to_message_id=current_user_message_id
        )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


async def cleanup_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """Periodically cleans up expired chat histories."""
    logger.info("Running periodic cleanup job for chat histories.")
    chat_manager.cleanup_expired_chats()


# Optional: Add a command to reload known users if you modify the file manually
@restricted_to_allowed_chats # Apply decorator
async def reload_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # You might want to restrict this command to bot admins
    # For simplicity, not adding admin check here
    logger.info("Attempting to reload known_users.yaml")
    load_known_users() # Reloads into the global _known_users_data
    await update.message.reply_text("Known users data reloaded from file.")


def run_bot(application: Application):
    global base_system_prompt, chat_manager # chat_manager is now global
    if not config:
        logger.critical("Configuration not loaded. Bot cannot start.")
        return

    if not config.allowed_chat_ids:
        logger.warning("IMPORTANT: `allowed_chat_ids` is empty...") # Existing warning

    base_system_prompt = load_system_prompt()
    
    # Initialize ChatManager (it will load histories internally)
    # If you made history_file_path configurable:
    # history_file = config.chat_history_file_path 
    history_file = DEFAULT_CHAT_HISTORY_FILE # Using default from chat_manager
    chat_manager = ChatManager(system_prompt=base_system_prompt, history_file_path=history_file)
    
    # ... (add handlers as before) ...
    application.add_handler(CommandHandler("mint", mint_command))
    application.add_handler(CommandHandler("reloadusers", reload_users_command))
    application.add_handler(MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND,
        handle_reply_to_bot
    ))

    application.add_error_handler(error_handler)
    job_queue: JobQueue = application.job_queue
    job_queue.run_repeating(cleanup_job_callback, interval=3600, first=60) # cleanup_job_callback uses global chat_manager

    logger.info("Bot starting to poll...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        # This block will execute when run_polling stops (e.g., Ctrl+C, or app.stop())
        logger.info("Bot polling stopped. Saving chat histories before shutdown...")
        if chat_manager: # Ensure chat_manager was initialized
            chat_manager.save_chat_histories()
        logger.info("Chat histories save attempt complete. Exiting run_bot.")