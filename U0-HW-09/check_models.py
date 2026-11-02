"""
Check available models from the embedding and LLM services.
"""

import requests
from src.config import load_config
from src.utils.logging import setup_logging

logger = setup_logging()


def check_embedding_models() -> None:
    """Check available embedding models."""
    print("\n" + "=" * 80)
    print("EMBEDDING MODELS")
    print("=" * 80)
    
    try:
        config = load_config()
        
        endpoint = f"{config.embedding_base_url}/models"
        headers = {
            "Authorization": f"Bearer {config.embedding_api_key}",
            "Content-Type": "application/json",
        }
        
        response = requests.get(endpoint, headers=headers, timeout=10)
        response.raise_for_status()
        
        models_data = response.json()
        
        if "data" in models_data:
            print(f"\nAvailable embedding models:")
            for model in models_data["data"]:
                model_id = model.get("id", "unknown")
                model_type = model.get("type", "unknown")
                print(f"  - {model_id} (type: {model_type})")
        else:
            print(f"\nResponse: {models_data}")
        
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to embedding service: {e}")
    except Exception as e:
        logger.error(f"Error checking embedding models: {e}")


def check_llm_models() -> None:
    """Check available LLM models."""
    print("\n" + "=" * 80)
    print("LLM MODELS")
    print("=" * 80)
    
    try:
        config = load_config()
        
        endpoint = f"{config.llm_base_url}/models"
        headers = {
            "Authorization": f"Bearer {config.llm_api_key}",
            "Content-Type": "application/json",
        }
        
        response = requests.get(endpoint, headers=headers, timeout=10)
        response.raise_for_status()
        
        models_data = response.json()
        
        if "data" in models_data:
            print(f"\nAvailable LLM models:")
            for model in models_data["data"]:
                model_id = model.get("id", "unknown")
                model_type = model.get("type", "unknown")
                print(f"  - {model_id} (type: {model_type})")
        else:
            print(f"\nResponse: {models_data}")
        
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to LLM service: {e}")
    except Exception as e:
        logger.error(f"Error checking LLM models: {e}")


def main() -> None:
    """Check available models from both services."""
    print("\n" + "=" * 80)
    print("MODEL AVAILABILITY CHECK")
    print("=" * 80)
    
    try:
        config = load_config()
        
        print(f"\nConfiguration loaded:")
        print(f"  Embedding Base URL: {config.embedding_base_url}")
        print(f"  Embedding Model: {config.embedding_model}")
        print(f"  LLM Base URL: {config.llm_base_url}")
        print(f"  LLM Model: {config.llm_model}")
        
        check_embedding_models()
        check_llm_models()
        
        print("\n" + "=" * 80)
        
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()
