"""
Entry point for the vector query pipeline.
Manually test the query pipeline with custom queries and configuration.

Usage:
    python query_main.py "Your question here"
    python query_main.py "Your question here" --verbose
    python query_main.py --interactive
"""

import argparse
import sys
from pathlib import Path

from src.config import load_config
from src.query.graph import create_query_graph, execute_query_pipeline
from src.utils.logging import setup_logging
from logging import CRITICAL, INFO
logger = setup_logging(level=CRITICAL)


def print_query_result(result) -> None:
    """
    Print a formatted summary of the query pipeline execution.
    
    Args:
        result: Final QueryState after pipeline completion
    """
    print("\n" + "=" * 80)
    print("QUERY PIPELINE RESULT")
    print("=" * 80)
    
    # Status and basic info
    print(f"\nStatus: {result.status}")
    print(f"Original Query: {result.original_query}")
    print(f"Query Validated: {result.query_validated}")
    
    # Embedding info
    if result.query_embedding:
        print(f"Query Embedding: Generated ({len(result.query_embedding)} dimensions)")
    else:
        print("Query Embedding: Not generated")
    
    # # Search results
    # print(f"\nSearch Results: {len(result.search_results)} documents retrieved")
    # if result.search_results:
    #     print("-" * 80)
    #     for i, doc in enumerate(result.search_results, 1):
    #         print(f"\n  Document {i}:")
    #         print(f"    Source: {doc.metadata.source_filename}")
    #         print(f"    Page: {doc.metadata.page_number}")
    #         print(f"    Chunk Sequence: {doc.metadata.chunk_sequence}")
    #         print(f"    Similarity Score: {doc.similarity_score:.4f}")
    #         print(f"    Content Preview: {doc.content[:200]}...")
    
    # Final response
    if result.final_response:
        print("\n" + "=" * 80)
        print("LLM RESPONSE")
        print("=" * 80)
        print(result.final_response)
    
    # Errors
    if result.errors:
        print("\n" + "=" * 80)
        print(f"ERRORS ({len(result.errors)})")
        print("=" * 80)
        for i, error in enumerate(result.errors, 1):
            print(f"{i}. {error}")
    else:
        print("\n" + "=" * 80)
        print("NO ERRORS ENCOUNTERED")
        print("=" * 80)
    
    print("\n" + "=" * 80)


def print_detailed_query_result(result) -> None:
    """
    Print a very detailed version of query results with all state information.
    
    Args:
        result: Final QueryState after pipeline completion
    """
    print("\n" + "=" * 80)
    print("DETAILED QUERY PIPELINE RESULT")
    print("=" * 80)
    
    # State metadata
    print("\n[STATE METADATA]")
    print(f"  Status: {result.status}")
    print(f"  Query Validated: {result.query_validated}")
    print(f"  Number of Errors: {len(result.errors)}")
    print(f"  Number of Search Results: {len(result.search_results)}")
    
    # Query details
    print("\n[QUERY DETAILS]")
    print(f"  Original Query: {result.original_query}")
    if result.query_embedding:
        print(f"  Embedding Dimension: {len(result.query_embedding)}")
        print(f"  Embedding Sample (first 5): {result.query_embedding[:5]}")
    else:
        print(f"  Embedding: Not generated")
    
    # Search results details
    if result.search_results:
        print("\n[SEARCH RESULTS DETAILS]")
        for i, doc in enumerate(result.search_results, 1):
            print(f"\n  Result {i}:")
            print(f"    Filename: {doc.metadata.source_filename}")
            print(f"    Page Number: {doc.metadata.page_number}")
            print(f"    Chunk Sequence: {doc.metadata.chunk_sequence}")
            print(f"    Similarity Score: {doc.similarity_score:.6f}")
            print(f"    Char Range: {doc.metadata.start_char_index}-{doc.metadata.end_char_index}")
            print(f"    Content Length: {len(doc.content)} characters")
            print(f"    Content:\n{doc.content}\n")
    
    # LLM response
    if result.final_response:
        print("\n[LLM RESPONSE]")
        print(f"  Length: {len(result.final_response)} characters")
        print(f"  Response:\n{result.final_response}\n")
    
    # Errors
    if result.errors:
        print("\n[ERRORS]")
        for i, error in enumerate(result.errors, 1):
            print(f"  {i}. {error}")
    
    print("\n" + "=" * 80)


def interactive_mode() -> None:
    """
    Run the query pipeline in interactive mode.
    Allows the user to enter multiple queries and see results.
    """
    print("\n" + "=" * 80)
    print("QUERY PIPELINE - INTERACTIVE MODE")
    print("=" * 80)
    print("\nEnter 'quit' to exit, 'clear' to clear screen, 'verbose' to toggle verbose mode")
    print("")
    
    try:
        # Load configuration
        logger.info("Loading configuration from .env")
        config = load_config()
        logger.info(f"Configuration loaded: embedding_model={config.embedding_model}, llm_model={config.llm_model}")
        
        # Create graph
        logger.info("Creating query pipeline graph")
        graph = create_query_graph(config)
        logger.info("Graph created successfully")
        
        verbose = False
        
        while True:
            # Get user input
            user_input = input("\nEnter query (or command): ").strip()
            
            # Handle commands
            if user_input.lower() == "quit":
                print("Exiting interactive mode...")
                break
            elif user_input.lower() == "clear":
                print("\033[2J\033[H")  # Clear screen
                continue
            elif user_input.lower() == "verbose":
                verbose = not verbose
                print(f"Verbose mode: {'ON' if verbose else 'OFF'}")
                continue
            elif not user_input:
                print("Please enter a query")
                continue
            
            # Execute query
            logger.info(f"Executing query: {user_input}")
            result = execute_query_pipeline(user_input, config, graph)
            
            # Print results
            if verbose:
                print_detailed_query_result(result)
            else:
                print_query_result(result)
    
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        raise
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        logger.error(f"Error in interactive mode: {e}")
        raise


def single_query_mode(query: str, verbose: bool = False) -> None:
    """
    Run the query pipeline for a single query.
    
    Args:
        query: The query string to execute
        verbose: Whether to print detailed output
    """
    print("\n" + "=" * 80)
    print("QUERY PIPELINE - SINGLE QUERY MODE")
    print("=" * 80)
    
    try:
        # Load configuration
        logger.info("Loading configuration from .env")
        config = load_config()
        logger.info(f"Configuration loaded: embedding_model={config.embedding_model}, llm_model={config.llm_model}")
        
        # Create graph
        logger.info("Creating query pipeline graph")
        graph = create_query_graph(config)
        logger.info("Graph created successfully")
        
        # Execute query
        logger.info(f"Executing query: {query}")
        result = execute_query_pipeline(query, config, graph)
        
        # Print results
        if verbose:
            print_detailed_query_result(result)
        else:
            print_query_result(result)
        
        logger.info("Query pipeline execution complete")
        
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"Query pipeline execution failed: {e}")
        raise


def main() -> None:
    """
    Main entry point for the query pipeline.
    Handles both interactive and single query modes via command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Query pipeline for semantic memory tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single query
  python query_main.py "What is the main topic?"
  
  # Single query with verbose output
  python query_main.py "What is the main topic?" --verbose
  
  # Interactive mode
  python query_main.py --interactive
  
  # Interactive mode with verbose output
  python query_main.py -i -v
        """
    )
    
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Query string to execute (if not provided, starts in interactive mode)"
    )
    
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Run in interactive mode (allow multiple queries)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed output with all state information"
    )
    
    args = parser.parse_args()
    
    # Determine mode based on arguments
    if args.interactive or args.query is None:
        # Interactive mode
        if args.verbose:
            print("Note: Verbose mode is controlled interactively (type 'verbose' to toggle)")
        interactive_mode()
    else:
        # Single query mode
        single_query_mode(args.query, args.verbose)


if __name__ == "__main__":
    main()
