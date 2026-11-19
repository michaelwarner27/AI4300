"""
Tool implementations for the vector query pipeline.
Re-exports and wraps tools from ingestion pipeline for query operations.
"""

import json
import os
import requests
from src.ingestion.tools import EmbeddingService, ChromaVectorStore
from src.utils.logging import get_logger

logger = get_logger()


class LLMService:
    """
    Interface to LLM service (local or remote).
    
    Configured to use the LLM service specified in .env.
    Makes HTTP calls to LLM API endpoint for generating responses.
    """
    
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 2048,
    ) -> None:
        """
        Initialize LLM service client.
        
        Args:
            base_url: Base URL of LLM service (e.g., http://localhost:4000/v1)
            model: Model identifier (e.g., Qwen3.8-27B)
            api_key: API key for authentication
            max_tokens: Maximum tokens in response (default: 2048)
        """
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        # Set LLM_REASONING_EFFORT in .env (e.g. low / medium / high); .env is loaded by load_config()
        self.reasoning = os.getenv("LLM_REASONING_EFFORT", "").strip().lower() or "low"
        logger.debug(f"LLMService initialized: {base_url}, model={model}, max_tokens={max_tokens}")
    

    def generate_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        temperature: float = 0.7,
    ) -> dict:
        """
        Generate response with tool calling.
        
        Args:
            system_prompt: System prompt for LLM behavior
            user_message: User message
            tools: List of tool definitions
            temperature: LLM temperature
        
        Returns:
            Dict with tool_name and parameters
            Format: {"tool_name": "...", "parameters": {...}}
        
        Raises:
            ConnectionError: Connection failed
            ValueError: Invalid response
        """
        logger.debug(f"LLMService.generate_with_tools() called")
        result = self.chat_with_tools(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=tools,
            temperature=temperature,
        )
        return {"tool_name": result["tool_name"], "parameters": result["parameters"]}

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
    ) -> dict:
        """
        Multi-turn tool calling over an explicit message list.

        Args:
            messages: Full conversation (system/user/assistant/tool messages)
            tools: List of tool definitions
            temperature: LLM temperature

        Returns:
            Dict with tool_name, parameters, tool_call_id and assistant_message.
            assistant_message is the assistant turn (restricted to the first tool
            call) to append to the conversation before the matching tool result.

        Raises:
            ConnectionError: Connection failed
            ValueError: Invalid response
        """
        response_data = self._call_llm_api(temperature=temperature, tools=tools, messages=messages)

        message = response_data["choices"][0]["message"]

        # Check for tool calls in response
        if "tool_calls" not in message or not message["tool_calls"]:
            # Log the full message content when tool calls are missing
            logger.warning(f"No tool calls in LLM response. Message content: {json.dumps(message, indent=2)}")
            raise ValueError("No tool calls in LLM response")

        tool_call = message["tool_calls"][0]
        try:
            parameters = json.loads(tool_call["function"]["arguments"])
            tool_name = tool_call["function"]["name"]
        except (KeyError, json.JSONDecodeError) as e:
            # Log the tool call that failed to parse
            logger.error(f"Failed to parse tool call: {json.dumps(tool_call, indent=2)}. Error: {str(e)}")
            raise ValueError(f"Failed to parse tool call: {str(e)}") from e

        tool_call_id = tool_call.get("id") or "call_0"
        # Keep only the first tool call so every call in the history gets a tool result
        assistant_message = {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": tool_call["function"]["arguments"],
                },
            }],
        }
        return {
            "tool_name": tool_name,
            "parameters": parameters,
            "tool_call_id": tool_call_id,
            "assistant_message": assistant_message,
        }

    def _call_llm_api(
        self,
        system_prompt: str | None = None,
        user_message: str | None = None,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
        messages: list[dict] | None = None,
    ) -> dict:
        """
        Internal method to call LLM API with optional tools.
        
        Returns:
            Raw response dict
        
        Raises:
            ConnectionError: Connection failed
            ValueError: Invalid response
        """
        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "reasoning_effort": self.reasoning,
        }
        
        # Add tools if provided
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        
        try:
            logger.debug(f"Making request to LLM endpoint: {endpoint}")
            response = requests.post(endpoint, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            response_data = response.json()
            logger.debug(f"LLM response received")
            
            if "choices" not in response_data or not response_data["choices"]:
                raise ValueError("Invalid LLM response: missing choices")
            
            if "message" not in response_data["choices"][0]:
                raise ValueError("Invalid LLM response: missing message")
            
            return response_data
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to LLM service at {endpoint}: {str(e)}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e
        except requests.exceptions.Timeout as e:
            error_msg = f"LLM service request timed out: {str(e)}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e
        except requests.exceptions.HTTPError as e:
            error_msg = f"LLM service returned HTTP {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e
        except ValueError as e:
            # Re-raise ValueError for invalid response format
            logger.error(f"Invalid LLM service response: {str(e)}")
            raise
        except Exception as e:
            error_msg = f"Unexpected error calling LLM service: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e


# Re-export tools from ingestion pipeline
# Query pipeline reuses these services
__all__ = ["EmbeddingService", "ChromaVectorStore", "LLMService"]
