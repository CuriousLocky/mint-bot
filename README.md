# Telegram AI Chat Bot - Mint

Mint is a Telegram bot designed for group chats, providing AI-powered conversation. It integrates with OpenAI-compatible backends to generate responses and can remember user information for personalized interactions.

## Features

* General Chat: Responds to primary commands (e.g., `/mint`) and engages in follow-up conversations.
* Chat History Management: 
    * Maintains conversation threads
    * Supports replies to any message within a thread
    * Automatically clears inactive chat histories
* User Recognition & Personalization: Learn and update information about users
* Console Control (Optional): Manually send messages to designated chat


## Prerequisites

*   Python 3.9+
*   An OpenAI-compatible AI backend (e.g., OpenAI API, local LLM server)
*   A Telegram Bot Token

## Setup and Configuration

1.  Clone the Repository:
    ```bash
    git clone https://github.com/yourusername/telegram-ai-bot.git
    cd telegram-ai-bot
    ```

2.  Create a Virtual Environment (Recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  Install Dependencies:
    ```bash
    pip install -r requirements.txt
    ```

4.  Configure the Bot:
    Create and edit `config/config.yaml` and fill in your details:
    *   `telegram_bot_token`: Your Telegram Bot token.
    *   `openai_api_url`: The URL for your OpenAI-compatible backend.
    *   `openai_api_key`: Your API key for the backend.
    *   `model_name`: The name of the AI model to use.
    *   `allowed_chat_ids`: A list of Telegram chat IDs (integer, group IDs are negative) where the bot is allowed to operate. Leave empty to restrict all chats (fail-safe).
    *   `enable_console_interface`: `true` to enable the local console, `false` to disable (recommended for Docker/server deployment).
    *   Other parameters like `temperature`, `context_window_tokens`, etc.

5.  Customize System Prompt:
    Create and edit `config/system_prompt.txt` to define the bot's persona, rules, and instructions for interacting with users and using tools.

6.  (Optional) Pre-populate Known Users:
    Create and edit `data/known_users.yaml` if you want to provide initial information about some users. The format is:
    ```yaml
    # UserID (integer):
    #   description: "A short description of the user."
    #   name: "User's Name" # Optional
    123456789:
      description: "Alice, loves discussing AI ethics."
      name: "Alice"
    ```

## Running the Bot

Locally:
```bash
python main.py

## Running the Bot
```

# Acknoledgements

- [python-telegram-bot](https://python-telegram-bot.org/) library
- OpenAI or other AI backend providers
- Google Gemini 2.5 Pro for implementation (including this readme file)
