"""
Entry point for the vector ingestion pipeline.
Initializes configuration, creates the pipeline, and executes it.
"""

from src.config import load_config
from src.data_models import IngestionState
from src.ingestion.graph import create_ingestion_graph
from src.utils.logging import setup_logging

logger = setup_logging()


def print_summary(state: IngestionState) -> None:
    """
    Print a summary of the pipeline execution results.
    
    Args:
        state: Final IngestionState after pipeline completion
    """
    print("\n" + "=" * 60)
    print("INGESTION PIPELINE SUMMARY")
    print("=" * 60)
    print(f"Status: {state.status}")
    print(f"Documents Loaded: {state.documents_loaded}")
    print(f"Documents Parsed: {state.documents_parsed}")
    print(f"Chunks Created: {state.chunks_created}")
    print(f"Embeddings Generated: {state.embeddings_generated}")
    print(f"Vectors Stored: {state.vectors_stored}")
    
    if state.errors:
        print(f"\nErrors ({len(state.errors)}):")
        for error in state.errors:
            print(f"  - {error}")
    else:
        print("\nNo errors encountered")
    
    print("=" * 60)


def main() -> None:
    """
    Main entry point for the ingestion pipeline.
    
    1. Loads configuration from .env
    2. Creates initial pipeline state
    3. Creates and compiles the LangGraph pipeline
    4. Executes the pipeline
    5. Prints results summary
    """
    logger.info("Starting vector ingestion pipeline")
    
    try:
        # Load configuration
        logger.info("Loading configuration from .env")
        config = load_config()
        logger.info(f"Configuration loaded: embedding_model={config.embedding_model}")
        
        # Create initial state
        initial_state = IngestionState(
            documents=[],
            chunks=[],
            errors=[],
            config=config,
            status="initialized",
        )
        logger.info("Initial pipeline state created")
        
        # Create and compile graph
        logger.info("Creating LangGraph pipeline")
        graph = create_ingestion_graph(config)
        
        # Execute pipeline
        logger.info("Executing pipeline...")
        result_dict = graph.invoke(initial_state)
        
        # Reconstruct state from result dict
        # The invoke returns a dict with updated fields
        final_state = IngestionState(
            documents=result_dict.get("documents", []),
            chunks=result_dict.get("chunks", []),
            errors=result_dict.get("errors", []),
            config=config,
            status=result_dict.get("status", "unknown"),
            documents_loaded=result_dict.get("documents_loaded", 0),
            documents_parsed=result_dict.get("documents_parsed", 0),
            chunks_created=result_dict.get("chunks_created", 0),
            embeddings_generated=result_dict.get("embeddings_generated", 0),
            vectors_stored=result_dict.get("vectors_stored", 0),
        )
        
        # Print summary
        print_summary(final_state)
        
        logger.info("Pipeline execution complete")
        
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        raise


if __name__ == "__main__":
    main()
