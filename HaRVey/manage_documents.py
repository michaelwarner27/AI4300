#!/usr/bin/env python3
"""
CLI tool for managing ingested documents in the vector database.

Provides commands to list, delete, and update documents without modifying the pipeline code.

Usage:
    python manage_documents.py --list
    python manage_documents.py --delete report.pdf
    python manage_documents.py --update report.pdf
    python manage_documents.py --help
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from src.config import load_config
from src.data_models import IngestionState
from src.ingestion.nodes import list_documents, delete_document, update_document
from src.utils.logging import get_logger

logger = get_logger()


def cmd_list() -> int:
    """
    List all documents currently stored in the vector database.
    
    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        config = load_config()
        state = IngestionState(config=config)
        result = list_documents(state)
        
        documents = result.get("documents_in_store", [])
        
        if not documents:
            print("No documents found in vector database.")
            return 0
        
        print("\n" + "=" * 80)
        print(f"{'Document Name':<40} {'Chunks':<10} {'Ingested At':<30}")
        print("=" * 80)
        
        for doc in documents:
            name = doc.get("name", "unknown")
            chunk_count = doc.get("chunk_count", 0)
            ingested_at = doc.get("ingested_at", "unknown")
            
            # Truncate long names
            if len(name) > 39:
                name = name[:36] + "..."
            
            print(f"{name:<40} {chunk_count:<10} {ingested_at:<30}")
        
        print("=" * 80)
        print(f"\nTotal: {len(documents)} document(s) in vector database")
        
        return 0
    
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        print(f"Error: {e}")
        print("Make sure .env file exists in the project root.")
        return 1
    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        print(f"Error listing documents: {e}")
        return 1


def cmd_delete(document_name: str) -> int:
    """
    Delete a document and all its associated vectors from the database.
    
    Args:
        document_name: Name of the document to delete (e.g., "report.pdf")
    
    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        config = load_config()
        state = IngestionState(config=config)
        
        # First, verify the document exists
        result = list_documents(state)
        documents = result.get("documents_in_store", [])
        doc_exists = any(d["name"] == document_name for d in documents)
        
        if not doc_exists:
            print(f"Warning: Document '{document_name}' not found in database.")
            response = input("Continue with delete anyway? (y/n): ").strip().lower()
            if response != 'y':
                print("Delete cancelled.")
                return 0
        
        # Confirm deletion
        print(f"\nDeleting document: {document_name}")
        response = input("Are you sure? This action cannot be undone. (y/n): ").strip().lower()
        
        if response != 'y':
            print("Delete cancelled.")
            return 0
        
        # Perform deletion
        result = delete_document(state, document_name)
        deleted_count = result.get("deleted_vectors", 0)
        errors = result.get("errors", [])
        
        if errors:
            logger.warning(f"Errors during deletion: {errors}")
        
        print(f"\nSuccess! Deleted {deleted_count} vectors for '{document_name}'")
        return 0
    
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        print(f"Error: {e}")
        print("Make sure .env file exists in the project root.")
        return 1
    except Exception as e:
        logger.error(f"Failed to delete document: {e}")
        print(f"Error deleting document: {e}")
        return 1


def cmd_update(document_path: str) -> int:
    """
    Delete old vectors for a document and prepare it for re-ingestion.
    
    After running this command, modify the PDF file and run:
        python ingestion_main.py
    
    Args:
        document_path: Path to the PDF file
    
    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        file_path = Path(document_path)
        
        # Verify file exists
        if not file_path.exists():
            print(f"Error: File not found: {document_path}")
            return 1
        
        if not file_path.suffix.lower() == '.pdf':
            print(f"Error: File is not a PDF: {document_path}")
            return 1
        
        config = load_config()
        state = IngestionState(config=config)
        
        # First, verify the document exists in database
        result = list_documents(state)
        documents = result.get("documents_in_store", [])
        doc_exists = any(d["name"] == file_path.name for d in documents)
        
        if not doc_exists:
            print(f"Warning: Document '{file_path.name}' not found in database.")
            print("This may be the first time ingesting this document.")
        
        # Confirm update
        print(f"\nPreparing to update document: {file_path.name}")
        if doc_exists:
            response = input("Delete old vectors and prepare for re-ingestion? (y/n): ").strip().lower()
        else:
            response = input("Proceed with preparation? (y/n): ").strip().lower()
        
        if response != 'y':
            print("Update cancelled.")
            return 0
        
        # Perform update
        result = update_document(state, document_path)
        deleted_count = result.get("deleted_vectors", 0)
        errors = result.get("errors", [])
        
        if errors:
            logger.warning(f"Errors during update: {errors}")
        
        if deleted_count > 0:
            print(f"\nSuccess! Deleted {deleted_count} old vectors for '{file_path.name}'")
        else:
            print(f"\nNo old vectors found. Document is ready for initial ingestion.")
        
        print("\nNext steps:")
        print(f"  1. Modify {file_path.name} if needed")
        print(f"  2. Ensure the file is in {file_path.parent}/")
        print(f"  3. Run: python ingestion_main.py")
        print("\nThe document will be re-ingested with its latest version.")
        
        return 0
    
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        print(f"Error: {e}")
        print("Make sure .env file exists in the project root.")
        return 1
    except Exception as e:
        logger.error(f"Failed to update document: {e}")
        print(f"Error updating document: {e}")
        return 1


def main() -> int:
    """
    Main CLI entry point.
    
    Returns:
        Exit code (0 for success, 1 for error)
    """
    parser = argparse.ArgumentParser(
        description="Manage documents in the vector database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python manage_documents.py --list
  python manage_documents.py --delete report.pdf
  python manage_documents.py --update ./documents/report.pdf

Operations:
  --list      List all ingested documents
  --delete    Remove a document and its vectors
  --update    Delete old vectors and prepare for re-ingestion
        """
    )
    
    # Create mutually exclusive group for operations
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="List all documents in the vector database"
    )
    group.add_argument(
        "--delete",
        metavar="DOCUMENT_NAME",
        help="Delete a document by name (e.g., 'report.pdf')"
    )
    group.add_argument(
        "--update",
        metavar="DOCUMENT_PATH",
        help="Prepare a document for re-ingestion (e.g., './documents/report.pdf')"
    )
    
    args = parser.parse_args()
    
    # Execute the appropriate command
    if args.list:
        return cmd_list()
    elif args.delete:
        return cmd_delete(args.delete)
    elif args.update:
        return cmd_update(args.update)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
