"""
Update the LLM model in .env file.
Helps switch between available models.
"""

import os
from pathlib import Path


def update_llm_model(model_name: str) -> bool:
    """
    Update the LLM_MODEL in .env file.
    
    Args:
        model_name: The new model name to use
    
    Returns:
        True if successful, False otherwise
    """
    env_path = Path(".env")
    
    if not env_path.exists():
        print(f"Error: .env file not found at {env_path.absolute()}")
        return False
    
    try:
        # Read current .env file
        with open(env_path, "r") as f:
            lines = f.readlines()
        
        # Find and update LLM_MODEL line
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith("LLM_MODEL="):
                new_lines.append(f"LLM_MODEL={model_name}\n")
                updated = True
                print(f"Updated: LLM_MODEL={model_name}")
            else:
                new_lines.append(line)
        
        if not updated:
            # If LLM_MODEL not found, add it
            new_lines.append(f"LLM_MODEL={model_name}\n")
            print(f"Added: LLM_MODEL={model_name}")
        
        # Write back to .env file
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        
        print(f"Successfully updated .env")
        return True
        
    except Exception as e:
        print(f"Error updating .env: {e}")
        return False


def show_available_models() -> None:
    """Display available LLM models."""
    print("\n" + "=" * 80)
    print("AVAILABLE LLM MODELS")
    print("=" * 80)
    print("""
Available models for LLM:
  1. Qwen3.8-Flash-Next    (RECOMMENDED - Fast Qwen model)
  2. Gemma-4-26B-A4B       (Alternative - 26B parameter model)
  3. Clef                  (Smaller model)
  4. Clef-Flash            (Smaller fast model)
""")


def main() -> None:
    """Main entry point."""
    print("\n" + "=" * 80)
    print("LLM MODEL UPDATE UTILITY")
    print("=" * 80)
    
    show_available_models()
    
    print("Current configuration:")
    try:
        from src.config import load_config
        config = load_config()
        print(f"  LLM_MODEL={config.llm_model}")
    except Exception as e:
        print(f"  Could not load current config: {e}")
    
    print("\nSelect a model to update:")
    print("  1. Qwen3.8-Flash-Next")
    print("  2. Gemma-4-26B-A4B")
    print("  3. Clef")
    print("  4. Clef-Flash")
    print("  0. Cancel")
    
    choice = input("\nEnter choice (0-4): ").strip()
    
    models = {
        "1": "Qwen3.8-Flash-Next",
        "2": "Gemma-4-26B-A4B",
        "3": "Clef",
        "4": "Clef-Flash",
    }
    
    if choice == "0":
        print("Cancelled")
        return
    
    if choice not in models:
        print("Invalid choice")
        return
    
    model_name = models[choice]
    
    print(f"\nUpdating to: {model_name}")
    if update_llm_model(model_name):
        print("\n✓ Update successful!")
        print(f"You can now run queries with the new model.")
        print(f"Try: python query_main.py \"Your question here\"")
    else:
        print("\n✗ Update failed")


if __name__ == "__main__":
    main()
