import logging
import threading
import asyncio
from telegram.ext import Application, JobQueue
from telegram import Bot
from config_loader import config # This also initializes logging
from bot_core import run_bot # The function that sets up and runs the bot

logger = logging.getLogger(__name__)

# --- Console Control State ---
manual_control_chat_id: int = None
telegram_app: Application = None # To send messages via console

def console_set_manual_chat(args: list):
    global manual_control_chat_id
    if not args:
        print("Usage: set_chat <chat_id>")
        return
    try:
        manual_control_chat_id = int(args[0])
        print(f"Manual control chat ID set to: {manual_control_chat_id}")
    except ValueError:
        print("Invalid chat_id. Must be an integer.")

def console_print_state(args: list):
    print("\n--- Bot State ---")
    print(f"Log Level: {config.log_level if config else 'N/A'}")
    print(f"Manual Control Chat ID: {manual_control_chat_id if manual_control_chat_id else 'Not Set'}")
    
    try:
        from bot_core import chat_manager # Assuming chat_manager is globally accessible in bot_core
        if chat_manager:
            active_chats = chat_manager.get_all_active_chats_summary()
            print("\nActive Chats:")
            for summary in active_chats:
                print(f"  - {summary}")
        else:
            print("Chat manager not yet initialized or accessible.")
    except ImportError:
        print("Chat manager module (bot_core.chat_manager) not found or not yet initialized.")
    except Exception as e:
        print(f"Error accessing chat manager state: {e}")
    print("-----------------\n")
        
async def console_send_message_async(context):
    """Asynchronous function to send a message to the manual control chat."""
    global manual_control_chat_id, telegram_app
    if not manual_control_chat_id:
        print("Manual control chat ID not set. Use 'set_chat <chat_id>'.")
        return
    if not telegram_app or not telegram_app.bot:
        print("Telegram application not initialized or bot not available.")
        return
    try:
        await telegram_app.bot.send_message(chat_id=manual_control_chat_id, text=context.job.data)
        print(f"Message sent to chat ID {manual_control_chat_id}: {context.job.data}")
    except Exception as e:
        print(f"Error sending message: {e}")

def console_send_message(args: list):
    if not args:
        print("Usage: send <message text>")
        return
    message_text = " ".join(args)
    # Use telegram_app.asyncio_event_loop
    if telegram_app:
        if isinstance(telegram_app.job_queue, JobQueue):
            telegram_app.job_queue.run_once(
                console_send_message_async,
                when=0,  # Run immediately
                data=message_text,
                name="ConsoleSendMessage"
            )
        else:
            print("Job queue is not properly initialized.")
    else:
        print("Telegram application event loop not available. Is the bot running and loop initialized?")


def console_interface():
    """Runs the interactive console for controlling the bot."""
    print("Console interface started. Type 'help' for commands.")
    commands = {
        "help": lambda args: print("Available commands:\n"
                                   "  set_chat <chat_id> - Set the chat ID for manual messages.\n"
                                   "  state              - Print current bot state.\n"
                                   "  send <message>     - Send a message to the manual control chat.\n"
                                   "  quit               - Exit the application."),
        "set_chat": console_set_manual_chat,
        "state": console_print_state,
        "send": console_send_message,
        "quit": lambda args: exit_application()
    }
    
    while True:
        try:
            user_input = input("Console> ").strip()
            if not user_input:
                continue
            
            command_part = user_input.split(" ", 1)
            command_name = command_part[0].lower()
            command_args = command_part[1].split() if len(command_part) > 1 else []

            if command_name in commands:
                commands[command_name](command_args)
            else:
                print(f"Unknown command: {command_name}. Type 'help'.")
        except KeyboardInterrupt:
            print("\nKeyboard interrupt received in console. Type 'quit' to exit gracefully.")
        except EOFError: # Ctrl+D
            print("\nEOF received. Initiating exit...")
            exit_application() 
            break 
        except Exception as e:
            logger.error(f"Console error: {e}", exc_info=True)

def exit_application():
    global telegram_app
    logger.info("Initiating application shutdown sequence...")

    force_exit = True
    
    if telegram_app.running:
        telegram_app.stop_running()
    
    logger.info("Exiting application process...")
    import os
    os._exit(0)


if __name__ == "__main__":
    if not config:
        logger.critical("Application cannot start due to configuration errors. Please check config.yaml and logs.")
        exit(1)

    logger.info("Starting Telegram AI Bot...")

    # Initialize Telegram Application
    # The asyncio_event_loop is set up when run_polling (or similar) is called.
    telegram_app = Application.builder().token(config.telegram_bot_token).build()

    # --- Conditionally Start Console Thread ---
    console_thread = None # Initialize to None
    if config.enable_console_interface:
        logger.info("Console interface is ENABLED in configuration. Starting console thread.")
        console_thread = threading.Thread(target=console_interface, daemon=True)
        console_thread.start()
        logger.info("Console interface thread started.")
    else:
        logger.info("Console interface is DISABLED in configuration.")
    # --- End Conditional Start ---

    try:
        run_bot(telegram_app) 
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received in main thread. PTB should handle shutdown.")
        # PTB's run_polling handles KeyboardInterrupt to stop its loop.
        # If further cleanup is needed, it would be after run_bot returns.
    except Exception as e:
        logger.critical(f"Critical error in bot execution: {e}", exc_info=True)
    finally:
        logger.info("Bot application main execution block finished or interrupted.")
        # If exit_application() wasn't called (e.g. run_bot exited cleanly or via Ctrl+C),
        # we might not need to do anything more here, as os._exit() in exit_application
        # would be the forceful way. If run_bot returns, the main thread ends,
        # and daemon threads are terminated.