"""
Configuration loader for the vector ingestion pipeline.
Reads environment variables from .env file and provides type-safe access.
"""

from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os


@dataclass
class IngestionConfig:
    """Configuration for the vector ingestion pipeline."""
    
    # Embedding service configuration
    embedding_base_url: str
    embedding_model: str
    embedding_api_key: str
    
    # LLM service configuration (for future use)
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    
    # Chunking configuration
    chunk_size: int
    chunk_overlap: int
    
    # Vector database configuration
    chroma_persist_directory: str
    chroma_collection_name: str
    
    # Document processing configuration
    document_input_dir: str


def load_config(env_file: str | None = None) -> IngestionConfig:
    """
    Load configuration from environment variables.
    
    Args:
        env_file: Path to .env file. If None, looks for .env in current directory.
    
    Returns:
        IngestionConfig object with all configuration values.
    
    Raises:
        FileNotFoundError: If .env file not found.
        ValueError: If required environment variables are missing.
    """
    # Determine .env file path
    if env_file is None:
        env_path = Path(".env")
    else:
        env_path = Path(env_file)
    
    if not env_path.exists():
        raise FileNotFoundError(f".env file not found at {env_path.absolute()}")
    
    # Load environment variables
    load_dotenv(env_path)
    
    # Required environment variables
    required_vars = {
        "EMBEDDING_BASE_URL": "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL": "EMBEDDING_MODEL",
        "EMBEDDING_API_KEY": "EMBEDDING_API_KEY",
        "LLM_BASE_URL": "LLM_BASE_URL",
        "LLM_MODEL": "LLM_MODEL",
        "LLM_API_KEY": "LLM_API_KEY",
        "CHUNK_SIZE": "CHUNK_SIZE",
        "CHUNK_OVERLAP": "CHUNK_OVERLAP",
        "CHROMA_PERSIST_DIRECTORY": "CHROMA_PERSIST_DIRECTORY",
        "CHROMA_COLLECTION_NAME": "CHROMA_COLLECTION_NAME",
        "DOCUMENT_INPUT_DIR": "DOCUMENT_INPUT_DIR",
    }
    
    # Retrieve values with type conversion
    missing_vars = []
    config_values: dict[str, str | int] = {}
    
    for key, env_var in required_vars.items():
        value = os.getenv(env_var)
        if value is None:
            missing_vars.append(env_var)
        else:
            config_values[key] = value
    
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )
    
    # Convert numeric values
    config_values["CHUNK_SIZE"] = int(config_values["CHUNK_SIZE"])
    config_values["CHUNK_OVERLAP"] = int(config_values["CHUNK_OVERLAP"])
    
    # Create and return config object
    return IngestionConfig(
        embedding_base_url=config_values["EMBEDDING_BASE_URL"],
        embedding_model=config_values["EMBEDDING_MODEL"],
        embedding_api_key=config_values["EMBEDDING_API_KEY"],
        llm_base_url=config_values["LLM_BASE_URL"],
        llm_model=config_values["LLM_MODEL"],
        llm_api_key=config_values["LLM_API_KEY"],
        chunk_size=config_values["CHUNK_SIZE"],
        chunk_overlap=config_values["CHUNK_OVERLAP"],
        chroma_persist_directory=config_values["CHROMA_PERSIST_DIRECTORY"],
        chroma_collection_name=config_values["CHROMA_COLLECTION_NAME"],
        document_input_dir=config_values["DOCUMENT_INPUT_DIR"],
    )


if __name__ == "__main__":
    # Quick test
    config = load_config()
    print(f"Config loaded successfully!")
    print(f"Embedding Model: {config.embedding_model}")
    print(f"Chunk Size: {config.chunk_size}")
    print(f"Document Input Dir: {config.document_input_dir}")
