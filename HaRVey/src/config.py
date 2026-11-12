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
    
    # Retry logic configuration
    embedding_max_retries: int = 3
    embedding_retry_backoff_base: float = 2.0
    
    # LLM service configuration (for future use)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    
    # Chunking configuration
    chunk_size: int = 512
    chunk_overlap: int = 50
    
    # Table extraction configuration
    enable_table_extraction: bool = True
    table_min_confidence: float = 0.5
    
    # Change detection configuration
    enable_change_detection: bool = True
    change_detection_log_level: str = "critical"
    
    # Progress tracking configuration
    show_progress_bar: bool = True
    
    # Vector database configuration
    chroma_persist_directory: str = "./chroma_db"
    chroma_collection_name: str = "vector_ingestion_collection"
    
    # Document processing configuration
    document_input_dir: str = "./documents"


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
    }
    
    # Optional environment variables with defaults
    optional_vars = {
        "LLM_BASE_URL": ("LLM_BASE_URL", ""),
        "LLM_MODEL": ("LLM_MODEL", ""),
        "LLM_API_KEY": ("LLM_API_KEY", ""),
        "CHUNK_SIZE": ("CHUNK_SIZE", "512"),
        "CHUNK_OVERLAP": ("CHUNK_OVERLAP", "50"),
        "EMBEDDING_MAX_RETRIES": ("EMBEDDING_MAX_RETRIES", "3"),
        "EMBEDDING_RETRY_BACKOFF_BASE": ("EMBEDDING_RETRY_BACKOFF_BASE", "2.0"),
        "ENABLE_TABLE_EXTRACTION": ("ENABLE_TABLE_EXTRACTION", "true"),
        "TABLE_MIN_CONFIDENCE": ("TABLE_MIN_CONFIDENCE", "0.5"),
        "ENABLE_CHANGE_DETECTION": ("ENABLE_CHANGE_DETECTION", "true"),
        "CHANGE_DETECTION_LOG_LEVEL": ("CHANGE_DETECTION_LOG_LEVEL", "critical"),
        "SHOW_PROGRESS_BAR": ("SHOW_PROGRESS_BAR", "true"),
        "CHROMA_PERSIST_DIRECTORY": ("CHROMA_PERSIST_DIRECTORY", "./chroma_db"),
        "CHROMA_COLLECTION_NAME": ("CHROMA_COLLECTION_NAME", "vector_ingestion_collection"),
        "DOCUMENT_INPUT_DIR": ("DOCUMENT_INPUT_DIR", "./documents"),
    }
    
    # Retrieve required values
    missing_vars = []
    config_values: dict[str, str | int | float | bool] = {}
    
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
    
    # Retrieve optional values
    for key, (env_var, default) in optional_vars.items():
        value = os.getenv(env_var, default)
        config_values[key] = value
    
    # Convert numeric values
    config_values["CHUNK_SIZE"] = int(config_values["CHUNK_SIZE"])
    config_values["CHUNK_OVERLAP"] = int(config_values["CHUNK_OVERLAP"])
    config_values["EMBEDDING_MAX_RETRIES"] = int(config_values["EMBEDDING_MAX_RETRIES"])
    config_values["EMBEDDING_RETRY_BACKOFF_BASE"] = float(config_values["EMBEDDING_RETRY_BACKOFF_BASE"])
    config_values["TABLE_MIN_CONFIDENCE"] = float(config_values["TABLE_MIN_CONFIDENCE"])
    
    # Convert boolean values
    config_values["ENABLE_TABLE_EXTRACTION"] = config_values["ENABLE_TABLE_EXTRACTION"].lower() == "true"
    config_values["ENABLE_CHANGE_DETECTION"] = config_values["ENABLE_CHANGE_DETECTION"].lower() == "true"
    config_values["SHOW_PROGRESS_BAR"] = config_values["SHOW_PROGRESS_BAR"].lower() == "true"
    
    # Create and return config object
    return IngestionConfig(
        embedding_base_url=config_values["EMBEDDING_BASE_URL"],
        embedding_model=config_values["EMBEDDING_MODEL"],
        embedding_api_key=config_values["EMBEDDING_API_KEY"],
        embedding_max_retries=config_values["EMBEDDING_MAX_RETRIES"],
        embedding_retry_backoff_base=config_values["EMBEDDING_RETRY_BACKOFF_BASE"],
        llm_base_url=config_values["LLM_BASE_URL"],
        llm_model=config_values["LLM_MODEL"],
        llm_api_key=config_values["LLM_API_KEY"],
        chunk_size=config_values["CHUNK_SIZE"],
        chunk_overlap=config_values["CHUNK_OVERLAP"],
        enable_table_extraction=config_values["ENABLE_TABLE_EXTRACTION"],
        table_min_confidence=config_values["TABLE_MIN_CONFIDENCE"],
        enable_change_detection=config_values["ENABLE_CHANGE_DETECTION"],
        change_detection_log_level=config_values["CHANGE_DETECTION_LOG_LEVEL"],
        show_progress_bar=config_values["SHOW_PROGRESS_BAR"],
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
