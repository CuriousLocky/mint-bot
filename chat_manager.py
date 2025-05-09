# chat_manager.py
import json
import os
import time
import logging
from collections import deque
from typing import Dict, List, Tuple, Optional, Any # Added Any

from config_loader import config
from ai_handler import count_tokens, count_message_tokens

DEFAULT_CHAT_HISTORY_FILE = "chat_histories.json"

logger = logging.getLogger(__name__)

# New structure for active_chats
# Key: message_id of the user's initial /mint command that started the conversation.
# Value: {"messages": deque[{"role": str, "content": str, "telegram_message_id": int}], 
#         "last_interaction": float, "chat_id": int}
ChatHistoriesType = Dict[int, Dict[str, Any]]

class ChatManager:
    def __init__(self, system_prompt: str, history_file_path: str = DEFAULT_CHAT_HISTORY_FILE): # Added history_file_path
        self.active_chats: ChatHistoriesType = {}
        self.system_prompt = system_prompt
        self.system_prompt_tokens = count_tokens(self.system_prompt)
        self.history_file_path = history_file_path # Store the path

        if not config:
            logger.error("ChatManager initialized without global config.")
            self.context_window_tokens = 4000
            self.chat_history_expiry_seconds = 3 * 24 * 60 * 60
        else:
            self.context_window_tokens = config.context_window_tokens
            self.chat_history_expiry_seconds = config.chat_history_expiry_days * 24 * 60 * 60
        
        self.load_chat_histories() # Load histories on initialization
        
    def _serialize_active_chats(self) -> Dict[str, Any]:
        """Converts active_chats (with deques) to a JSON-serializable dictionary."""
        serializable_chats = {}
        for thread_key, chat_data in self.active_chats.items():
            # Convert deque to list for JSON serialization
            serializable_messages = list(chat_data["messages"])
            serializable_chats[str(thread_key)] = { # Convert int key to str for JSON
                "messages": serializable_messages,
                "last_interaction": chat_data["last_interaction"],
                "chat_id": chat_data["chat_id"]
            }
        return serializable_chats

    def _deserialize_active_chats(self, data: Dict[str, Any]) -> ChatHistoriesType:
        """Converts a loaded dictionary back to active_chats format (with deques)."""
        deserialized_chats = {}
        for thread_key_str, chat_data in data.items():
            try:
                thread_key_int = int(thread_key_str) # Convert str key back to int
                # Convert list of messages back to deque
                messages_deque = deque(chat_data["messages"])
                deserialized_chats[thread_key_int] = {
                    "messages": messages_deque,
                    "last_interaction": chat_data["last_interaction"],
                    "chat_id": chat_data["chat_id"]
                }
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Error deserializing chat data for key '{thread_key_str}': {e}. Skipping this entry.")
        return deserialized_chats

    def save_chat_histories(self) -> bool:
        logger.info(f"Attempting to save chat histories to {self.history_file_path}...")
        if not self.active_chats:
            logger.info("No active chat histories to save.")
            # Optionally, remove the old file if it exists and no active chats
            # if os.path.exists(self.history_file_path):
            #     try:
            #         os.remove(self.history_file_path)
            #         logger.info(f"Removed empty chat history file: {self.history_file_path}")
            #     except OSError as e:
            #         logger.error(f"Error removing empty chat history file {self.history_file_path}: {e}")
            return True # Nothing to save is a success in this context

        serializable_data = self._serialize_active_chats()
        try:
            with open(self.history_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_data, f, indent=2) # indent for readability
            logger.info(f"Successfully saved {len(self.active_chats)} chat histories to {self.history_file_path}")
            return True
        except IOError as e:
            logger.error(f"Error saving chat histories to {self.history_file_path}: {e}")
            return False
        except TypeError as e: # Should be caught by _serialize_active_chats but as a safeguard
            logger.error(f"TypeError during chat history serialization: {e}")
            return False


    def load_chat_histories(self) -> bool:
        logger.info(f"Attempting to load chat histories from {self.history_file_path}...")
        if not os.path.exists(self.history_file_path):
            logger.info(f"Chat history file not found at {self.history_file_path}. Starting with empty histories.")
            self.active_chats = {}
            return True # No file to load is not an error

        try:
            with open(self.history_file_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
            
            self.active_chats = self._deserialize_active_chats(loaded_data)
            logger.info(f"Successfully loaded {len(self.active_chats)} chat histories from {self.history_file_path}")
            # Optionally, run cleanup_expired_chats after loading to remove any stale ones
            self.cleanup_expired_chats()
            return True
        except IOError as e:
            logger.error(f"Error loading chat histories from {self.history_file_path}: {e}")
            self.active_chats = {} # Reset to empty on error
            return False
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from chat history file {self.history_file_path}: {e}")
            self.active_chats = {}
            return False
        except Exception as e: # Catch-all for other unexpected errors during deserialization
            logger.error(f"Unexpected error loading or processing chat histories: {e}", exc_info=True)
            self.active_chats = {}
            return False

    def _find_thread_key_for_reply(self, replied_to_telegram_message_id: int, chat_id: int) -> Optional[int]:
        """
        Finds the thread key (initial_user_mint_command_message_id) for a given replied-to message ID.
        Iterates through all active chats and their messages.
        """
        for thread_key, chat_data in self.active_chats.items():
            if chat_data["chat_id"] == chat_id: # Ensure reply is in the same chat
                for msg_in_history in chat_data["messages"]:
                    if msg_in_history.get("telegram_message_id") == replied_to_telegram_message_id:
                        return thread_key
        return None

    def start_new_chat(self, initial_user_message_content: str, initial_user_telegram_message_id: int,
                       chat_id: int, bot_first_reply_telegram_message_id: int, bot_first_reply_content: str):
        """
        Starts a new chat history, keyed by the initial user's /mint command message ID.
        """
        history = deque()
        history.append({
            "role": "user",
            "content": initial_user_message_content,
            "telegram_message_id": initial_user_telegram_message_id
        })
        history.append({
            "role": "assistant",
            "content": bot_first_reply_content,
            "telegram_message_id": bot_first_reply_telegram_message_id
        })
        
        self.active_chats[initial_user_telegram_message_id] = {
            "messages": history,
            "last_interaction": time.time(),
            "chat_id": chat_id
        }
        logger.info(f"Started new chat thread keyed by user_msg_id {initial_user_telegram_message_id} in chat {chat_id}")


    def add_message_to_chat(self, thread_key: int, role: str, content: str,
                            telegram_message_id: int) -> bool:
        """
        Adds a message to an existing chat identified by thread_key.
        """
        if thread_key not in self.active_chats:
            logger.warning(f"Attempted to add message to non-existent or expired chat thread (key: {thread_key})")
            return False

        chat_data = self.active_chats[thread_key]
        chat_data["messages"].append({
            "role": role,
            "content": content,
            "telegram_message_id": telegram_message_id
        })
        chat_data["last_interaction"] = time.time()
        
        logger.debug(f"Added {role} message (ID: {telegram_message_id}) to chat thread {thread_key}. History length: {len(chat_data['messages'])}")
        return True

    def get_message_by_telegram_id(self, thread_key: int, telegram_message_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a specific message from a thread by its Telegram message ID."""
        if thread_key not in self.active_chats:
            return None
        for msg in self.active_chats[thread_key]["messages"]:
            if msg.get("telegram_message_id") == telegram_message_id:
                return msg
        return None

    def get_history_for_ai(self, thread_key: int) -> Optional[List[Dict[str, str]]]:
        """
        Gets (and potentially trims) history for AI.
        Returns messages in the format AI expects (role, content).
        """
        if thread_key not in self.active_chats:
            logger.warning(f"Attempted to get history for non-existent chat thread (key: {thread_key})")
            return None

        chat_data = self.active_chats[thread_key]
        original_messages = chat_data["messages"] # This is a deque of dicts with telegram_message_id
        
        # Trim history to fit context window
        current_tokens = self.system_prompt_tokens # Consider base system prompt
        # Note: The AI handler will add user info to system prompt, so this count is an approximation.
        # For more precise trimming, AI handler would need to inform ChatManager of the full system prompt length.

        trimmed_messages_for_ai = deque() # This will store {"role": ..., "content": ...}

        # Iterate from newest to oldest from original_messages
        for msg_obj in reversed(original_messages):
            # For token counting and AI, we only need role and content
            ai_message_format = {"role": msg_obj["role"], "content": msg_obj["content"]}
            msg_tokens = count_message_tokens(ai_message_format)
            
            if current_tokens + msg_tokens <= self.context_window_tokens:
                trimmed_messages_for_ai.appendleft(ai_message_format)
                current_tokens += msg_tokens
            else:
                logger.info(f"Trimming chat thread {thread_key}. Max context: {self.context_window_tokens}, Current (incl. base sys prompt): {current_tokens}")
                break 
        
        # If trimming occurred, we should update the stored messages deque.
        # This is complex because we store telegram_message_id.
        # For now, let's not trim the *stored* messages, only what's *sent* to AI.
        # This means self.active_chats[thread_key]["messages"] can grow larger than context window,
        # but what's sent to AI is always trimmed.
        # A more advanced approach would be to trim self.active_chats[thread_key]["messages"] as well.
        # This would involve deciding which messages (with their telegram_message_ids) to discard.

        if len(trimmed_messages_for_ai) < len(original_messages):
            logger.info(f"Chat thread {thread_key} history for AI trimmed to {len(trimmed_messages_for_ai)} messages, {current_tokens} tokens (excluding dynamic parts of system prompt).")
            
        return list(trimmed_messages_for_ai)


    def cleanup_expired_chats(self):
        now = time.time()
        expired_keys = [
            key for key, data in self.active_chats.items()
            if now - data["last_interaction"] > self.chat_history_expiry_seconds
        ]
        for key in expired_keys:
            del self.active_chats[key]
            logger.info(f"Cleared expired chat thread history for initial_user_msg_id: {key}")
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired chat threads.")

    def get_chat_info(self, thread_key: int) -> Optional[Dict]: # thread_key is initial user msg id
        return self.active_chats.get(thread_key)

    def get_all_active_chats_summary(self) -> List[str]:
        summary = []
        if not self.active_chats:
            return ["No active chats."]
        for key, data in self.active_chats.items():
            summary.append(
                f"ThreadKey (InitialUserMsgID): {key}, ChatID: {data['chat_id']}, "
                f"Msgs: {len(data['messages'])}, "
                f"LastSeen: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['last_interaction']))}"
            )
        return summary