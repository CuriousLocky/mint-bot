import time
import yaml
import os
import logging
from typing import Dict, Any, Optional, List

DEFAULT_CONFIG_PATH = "config.yaml"
CONFIG_ENV_VAR = "APP_CONFIG_PATH"

logger = logging.getLogger(__name__)

class Config:
    def __init__(self, config_path=None):
        path = os.getenv(CONFIG_ENV_VAR, config_path or DEFAULT_CONFIG_PATH)
        logger.info(f"Loading configuration from: {path}")
        try:
            with open(path, 'r', encoding="UTF-8") as f:
                self.data = yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Configuration file not found at {path}. Please create it or set {CONFIG_ENV_VAR}.")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration from {path}: {e}")
            raise
        
        # Basic validation or default setting can be done here
        self.telegram_bot_token = self.data.get("telegram_bot_token")
        self.openai_api_url = self.data.get("openai_api_url")
        self.openai_api_key = self.data.get("openai_api_key")
        self.model_name = self.data.get("model_name", "gpt-3.5-turbo")
        self.model_params = self.data.get("model_params", {
            "temperature": 0.7, 
            "reasoning_effort": None})
        self.context_window_tokens = self.data.get("context_window_tokens", 8192)
        self.max_ai_response_length = self.data.get("max_ai_response_length", 1000)
        self.chat_history_expiry_days = self.data.get("chat_history_expiry_days", 3)
        self.log_level = self.data.get("log_level", "INFO").upper()
        
        # --- Load Allowed Chat IDs ---
        self.allowed_chat_ids: List[int] = self.data.get("allowed_chat_ids", [])
        if not isinstance(self.allowed_chat_ids, list):
            logger.warning(
                f"'allowed_chat_ids' in {path} is not a list. Defaulting to an empty list (no chats allowed for restricted commands)."
            )
            self.allowed_chat_ids = []
        else:
            # Ensure all elements are integers
            try:
                self.allowed_chat_ids = [int(cid) for cid in self.allowed_chat_ids]
            except ValueError:
                logger.error(
                    f"'allowed_chat_ids' in {path} contains non-integer values. "
                    "Defaulting to an empty list. Please ensure all chat IDs are integers."
                )
                self.allowed_chat_ids = []
        
        if not self.allowed_chat_ids:
            logger.warning(
                "The 'allowed_chat_ids' list is empty. The bot will not respond to restricted commands in any chat."
            )
        else:
            logger.info(f"Bot operations will be restricted to chat IDs: {self.allowed_chat_ids}")
        # --- End Load Allowed Chat IDs ---
        
                # --- Load Console Interface Setting ---
        self.enable_console_interface: bool = self.data.get("enable_console_interface", False) # Default to False
        if not isinstance(self.enable_console_interface, bool):
            logger.warning(
                f"'enable_console_interface' in {path} is not a boolean (true/false). Defaulting to False."
            )
            self.enable_console_interface = False
        logger.info(f"Console interface enabled: {self.enable_console_interface}")
        # --- End Load Console Interface Setting ---

        if not self.telegram_bot_token:
            raise ValueError("telegram_bot_token is missing in config.")
        if not self.openai_api_key: # API key might be optional for some local backends
            logger.warning("openai_api_key is missing in config. This might be intended for some backends.")

# config_loader.py
import yaml
import os
import logging
from typing import Dict, Any

DEFAULT_CONFIG_PATH = "config.yaml"
CONFIG_ENV_VAR = "APP_CONFIG_PATH"
DEFAULT_KNOWN_USERS_PATH = "known_users.yaml" # New

logger = logging.getLogger(__name__)

# ... (Config class remains the same) ...
# Global config instance
try:
    config = Config()
except Exception as e:
    logger.critical(f"Failed to initialize configuration: {e}")
    config = None

def load_system_prompt(file_path="system_prompt.txt") -> str: # Added return type hint
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning(f"System prompt file '{file_path}' not found. Using a default generic prompt.")
        return "You are a helpful AI assistant."

# --- New User Info Management ---
_known_users_data: Dict[int, Dict[str, Any]] = {}
_known_users_file_path: str = DEFAULT_KNOWN_USERS_PATH

def load_known_users(file_path: str = DEFAULT_KNOWN_USERS_PATH) -> Dict[int, Dict[str, Any]]:
    global _known_users_data, _known_users_file_path
    _known_users_file_path = file_path
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            users = yaml.safe_load(f)
            if users is None: # Empty file
                _known_users_data = {}
                return {}
            # Ensure keys are integers (user IDs)
            _known_users_data = {int(k): v for k, v in users.items()}
            logger.info(f"Loaded {len(_known_users_data)} known users from {file_path}")
            return _known_users_data
    except FileNotFoundError:
        logger.info(f"Known users file '{file_path}' not found. Starting with an empty user list.")
        _known_users_data = {}
        # Optionally create an empty file here if it doesn't exist
        # with open(file_path, 'w', encoding='utf-8') as f:
        #     yaml.dump({}, f)
        return {}
    except (yaml.YAMLError, ValueError) as e: # ValueError for int conversion
        logger.error(f"Error parsing known users YAML from {file_path}: {e}. Using empty list.")
        _known_users_data = {}
        return {}

def save_known_users() -> bool:
    global _known_users_data, _known_users_file_path
    try:
        with open(_known_users_file_path, 'w', encoding='utf-8') as f:
            yaml.dump(_known_users_data, f, allow_unicode=True, sort_keys=True) # sort_keys for consistent output
        logger.info(f"Saved {len(_known_users_data)} known users to {_known_users_file_path}")
        return True
    except IOError as e:
        logger.error(f"Error saving known users to {_known_users_file_path}: {e}")
        return False

def get_known_user_info_for_prompt() -> str:
    if not _known_users_data:
        return "No specific user information is currently known."

    prompt_section = "Known users in this chat:\n"
    for user_id, info in _known_users_data.items():
        name_part = f" ({info.get('name')})" if info.get('name') else ""
        description = info.get('description', 'No description available.')
        prompt_section += f"- User ID {user_id}{name_part}: {description}\n"
    return prompt_section

def update_user_in_memory(user_id: int, description: Optional[str] = None, name: Optional[str] = None) -> bool:
    """Updates user info in memory. Does not save to disk automatically."""
    global _known_users_data
    user_id = int(user_id) # Ensure it's an int
    if user_id not in _known_users_data:
        _known_users_data[user_id] = {}
        logger.info(f"Adding new user {user_id} to known users (in memory).")

    updated = False
    if description is not None:
        _known_users_data[user_id]['description'] = description
        updated = True
    if name is not None:
        _known_users_data[user_id]['name'] = name
        updated = True
    
    if updated:
        _known_users_data[user_id]['last_updated_by_ai'] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.info(f"User {user_id} info updated in memory: name='{name}', desc='{description}'.")
    return updated

# --- End User Info Management ---


if config:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    load_known_users() # Load users when config is loaded
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger.error("Config not loaded, logging with default INFO level.")
