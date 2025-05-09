# ai_handler.py
from typing import List, Dict, Optional
import openai
import logging
import tiktoken # For token counting
import json

from config_loader import config, get_known_user_info_for_prompt, update_user_in_memory, save_known_users # New imports

logger = logging.getLogger(__name__)

# Initialize ASYNCHRONOUS OpenAI client
# Note the change from OpenAI to AsyncOpenAI
if config and config.openai_api_key: # Ensure config and key are present before initializing
    async_client = openai.AsyncOpenAI(
        api_key=config.openai_api_key,
        base_url=config.openai_api_url
    )
else:
    async_client = None # Or handle this case more gracefully, e.g., by not starting the bot
    logger.error("OpenAI API key or URL not configured. AI handler will not function.")


# --- Token Counting (using tiktoken) ---
_tokenizer = None

def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        try:
            model_to_tokenize = "gpt-3.5-turbo" # Default if config not loaded or model_name missing
            if config and config.model_name:
                model_to_tokenize = config.model_name
            _tokenizer = tiktoken.encoding_for_model(model_to_tokenize)
        except Exception:
            logger.warning(f"Could not get tokenizer for model {config.model_name if config else 'N/A'}. Falling back to cl100k_base.")
            _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer

def count_tokens(text):
    if not text:
        return 0
    tokenizer = get_tokenizer()
    return len(tokenizer.encode(text))

def count_message_tokens(message):
    """Counts tokens for a single message object {'role': ..., 'content': ...}"""
    num_tokens = 4 
    for key, value in message.items():
        num_tokens += count_tokens(value)
        if key == "name":
            num_tokens -= 1
    return num_tokens
# --- End Token Counting ---

# --- Tool Definition for AI ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": "Update or add information about a known user. Use this to remember details about users based on the conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "The Telegram User ID of the user whose profile is to be updated."
                    },
                    "description": {
                        "type": "string",
                        "description": "A new or updated concise description of the user, summarizing key characteristics or preferences learned. Should be a complete replacement of any old description."
                    },
                    "name": {
                        "type": "string",
                        "description": "The user's preferred name or nickname, if learned or confirmed."
                    }
                },
                "required": ["user_id", "description"] # Make description mandatory for an update
            }
        }
    }
]

available_tools = {
    "update_user_profile": update_user_in_memory # Map tool name to our Python function
}
# --- End Tool Definition ---


async def get_ai_response(
    messages_history: List[Dict[str, str]], # This is already role/content only
    base_system_prompt: str,
    user_id_for_current_message: Optional[int] = None,
    reply_to_message_content: Optional[str] = None, # NEW: Content of the message being replied to
    reply_to_message_role: Optional[str] = None    # NEW: Role of the message being replied to
) -> str:
    if not async_client:
        logger.error("AsyncOpenAI client not initialized. Cannot get AI response.")
        return "Error: AI backend client not configured."

    # Augment system prompt with known user info
    known_users_prompt_section = get_known_user_info_for_prompt()
    full_system_prompt = base_system_prompt
    full_system_prompt += f"\n\n<known_users>{known_users_prompt_section}\n</known_users>"
    full_system_prompt += "\n\nWhen a user messages, their ID might be prepended to their message like '[User ID: 12345]'. Use this to identify them."
    full_system_prompt += "\nYou can use tools to update user profiles."
    full_system_prompt += "\n\nWhen a user messages, their ID might be prepended. If they are replying to a specific previous message, that context will also be provided."

    processed_messages_for_ai = []
    for i, msg in enumerate(messages_history):
        current_msg_content = msg['content']
        # If this is the last user message AND they are replying to something specific
        if msg["role"] == "user" and i == len(messages_history) - 1:
            user_prefix = f"[User ID: {user_id_for_current_message}]" if user_id_for_current_message else "[User]"
            
            if reply_to_message_content and reply_to_message_role:
                # Truncate reply_to_message_content if it's too long to avoid excessive token usage
                max_reply_context_len = 150 # Characters
                truncated_reply_to_content = (reply_to_message_content[:max_reply_context_len] + '...') \
                                             if len(reply_to_message_content) > max_reply_context_len \
                                             else reply_to_message_content
                
                reply_context = f"[Replying to {reply_to_message_role}'s message: \"{truncated_reply_to_content}\"]"
                current_msg_content = f"{user_prefix} {reply_context} {current_msg_content}"
            else:
                current_msg_content = f"{user_prefix} {current_msg_content}"
            
            processed_messages_for_ai.append({
                "role": "user",
                "content": current_msg_content
            })
        else:
            # For past messages in history, just pass them as they are (role/content)
            processed_messages_for_ai.append(msg)


    api_messages = [{"role": "system", "content": full_system_prompt}] + processed_messages_for_ai
    
    logger.debug(f"Sending to AI (with user info): {api_messages}")
    
    try:
        completion = await async_client.chat.completions.create(
            model=config.model_name,
            messages=api_messages,
            temperature=config.model_params.get("temperature", 0.7),
            reasoning_effort=config.model_params.get("reasoning_effort", None),
            max_tokens=config.max_ai_response_length,
            tools=tools, # Pass the defined tools
            tool_choice="auto" # Let the model decide when to use tools
        )

        response_message = completion.choices[0].message
        
        # --- Handle Tool Calls ---
        if response_message.tool_calls:
            logger.info(f"AI requested tool call(s): {response_message.tool_calls}")
            # Extend the history with the assistant's tool call request
            api_messages.append(response_message) 

            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_to_call = available_tools.get(function_name)
                
                if not function_to_call:
                    logger.error(f"AI called unknown function: {function_name}")
                    # Append an error message for the tool call
                    api_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": f'{{"error": "Function {function_name} not found."}}'
                    })
                    continue

                try:
                    function_args = json.loads(tool_call.function.arguments)
                    logger.info(f"Calling function {function_name} with args: {function_args}")
                    
                    # Special handling for our update_user_profile
                    if function_name == "update_user_profile":
                        uid = function_args.get("user_id")
                        desc = function_args.get("description")
                        name = function_args.get("name") 

                        if uid is None or desc is None:
                             raise ValueError("user_id and description are required for update_user_profile.")

                        # Call our synchronous function to update in memory
                        update_in_memory_successful = update_user_in_memory(user_id=uid, description=desc, name=name)
                        
                        save_to_disk_successful = False
                        if update_in_memory_successful:
                            # --- THIS IS THE CRITICAL FIX ---
                            # Save to disk ONLY if memory update was meaningful
                            save_to_disk_successful = save_known_users() 
                            # --- END CRITICAL FIX ---
                            function_response_content = f'{{"success": true, "user_id": {uid}, "message": "User profile updated.", "save_status": {str(save_to_disk_successful).lower()}}}'
                        else:
                            # This case means update_user_in_memory returned False (e.g., no actual change was made)
                            function_response_content = f'{{"success": false, "user_id": {uid}, "message": "User profile update in memory did not result in changes."}}'
                        # Generic handling for other potential future tools (if any were sync)
                        # For async tools, you'd await function_to_call(**function_args)
                        # This example assumes update_user_in_memory is synchronous
                    else:
                        function_response_content = "Tool executed (generic - no specific return defined)"


                    # Append the tool's response to the history
                    api_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response_content,
                    })

                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON arguments for {function_name}: {tool_call.function.arguments} - {e}")
                    api_messages.append({ "tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": f'{{"error": "Invalid JSON arguments: {e}"}}'})
                except ValueError as e: # Catch our custom validation error
                    logger.error(f"ValueError for {function_name}: {e}")
                    api_messages.append({ "tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": f'{{"error": "{e}"}}'})
                except Exception as e:
                    logger.error(f"Error executing function {function_name}: {e}", exc_info=True)
                    api_messages.append({ "tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": f'{{"error": "Failed to execute tool: {e}"}}'})
            
            if not response_message.content:
                # Now that we've processed all tool calls and appended their results,
                # make a second call to the AI to get a natural language response
                logger.info("Sending updated history back to AI after tool execution.")
                logger.debug(f"Messages for final AI response: {api_messages}")
                
                second_completion = await async_client.chat.completions.create(
                    model=config.model_name,
                    messages=api_messages, # Send the history including tool calls and responses
                    temperature=config.model_params.get("temperature", 0.7),
                    max_tokens=config.max_ai_response_length,
                    # DO NOT pass tools again here, unless you want to allow chained tool calls in one turn
                )
                response_message = second_completion.choices[0].message
            final_response_content = response_message.content
            logger.debug(f"Received final AI response after tool use: {final_response_content}")
            return final_response_content.strip()

        # If no tool calls, just return the content
        response_content = response_message.content
        logger.debug(f"Received from AI (no tool call): {response_content}")
        return response_content.strip() if response_content else "AI returned an empty response."

    # ... (existing exception handling) ...
    except Exception as e:
        logger.error(f"An unexpected error occurred with AI backend: {e}", exc_info=True)
        return "Error: An unexpected error occurred while contacting AI."