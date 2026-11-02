"""
Simple verification script for vector ingestion pipeline.
Checks that all documents were processed and sampled embeddings are valid.
"""

import random
from pathlib import Path
from src.config import load_config
import chromadb
from chromadb.config import Settings

def verify_ingestion():
    """Verify that ingestion completed successfully."""
    
    print("=" * 60)
    print("INGESTION VERIFICATION")
    print("=" * 60)
    
    # Load config
    try:
        config = load_config()
    except Exception as e:
        print(f"\n❌ Failed to load configuration: {e}")
        return False
    
    # Check for Chroma database
    chroma_dir = Path(config.chroma_persist_directory)
    if not chroma_dir.exists():
        print(f"\n❌ Chroma database not found at: {chroma_dir}")
        return False
    
    print(f"\n✓ Chroma database found at: {chroma_dir}")
    
    # Connect to Chroma
    try:
        settings = Settings(
            is_persistent=True,
            persist_directory=str(chroma_dir.absolute()),
            allow_reset=False,
        )
        client = chromadb.Client(settings)
        collection = client.get_collection(name=config.chroma_collection_name)
    except Exception as e:
        print(f"❌ Failed to connect to Chroma: {e}")
        return False
    
    print(f"✓ Connected to Chroma collection: {config.chroma_collection_name}")
    
    # Check document count
    total_chunks = collection.count()
    print(f"\n✓ Total chunks stored: {total_chunks}")
    
    if total_chunks == 0:
        print("❌ No chunks found in collection!")
        return False
    
    # Get all documents to check which files were processed
    all_data = collection.get(include=["embeddings", "documents", "metadatas"])
    unique_files = set()
    
    for metadata in all_data["metadatas"]:
        unique_files.add(metadata["source_filename"])
    
    print(f"\n✓ Unique documents processed: {len(unique_files)}")
    for filename in sorted(unique_files):
        file_chunks = sum(1 for m in all_data["metadatas"] if m["source_filename"] == filename)
        print(f"  - {filename}: {file_chunks} chunks")
    
    # Sample random embeddings and check validity
    print(f"\n✓ Sampling 5 random embeddings for validation:")
    sample_indices = random.sample(range(len(all_data["ids"])), min(5, len(all_data["ids"])))
    
    all_valid = True
    for idx in sample_indices:
        chunk_id = all_data["ids"][idx]
        embedding = all_data["embeddings"][idx] if all_data["embeddings"] is not None else []
        content = all_data["documents"][idx]
        metadata = all_data["metadatas"][idx]
        
        # Check embedding validity
        is_valid_embedding = (
            embedding is not None and
            len(embedding) > 0 and
            all(isinstance(x, (int, float)) for x in embedding)
        )
        
        # Check metadata validity
        has_required_metadata = (
            "source_filename" in metadata and
            "page_number" in metadata and
            "chunk_sequence" in metadata
        )
        
        status = "✓" if (is_valid_embedding and has_required_metadata) else "❌"
        
        if not (is_valid_embedding and has_required_metadata):
            all_valid = False
        
        print(f"\n  {status} Sample {idx + 1}:")
        print(f"     ID: {chunk_id}")
        print(f"     Embedding dims: {len(embedding)}")
        print(f"     Content preview: {content[:50]}...")
        print(f"     File: {metadata['source_filename']}")
        print(f"     Page: {metadata['page_number']}, Seq: {metadata['chunk_sequence']}")
    
    print("\n" + "=" * 60)
    if all_valid:
        print("✅ VERIFICATION PASSED - Ingestion successful!")
        print(f"   - {len(unique_files)} documents processed")
        print(f"   - {total_chunks} chunks stored")
        print(f"   - Embeddings and metadata are valid")
    else:
        print("❌ VERIFICATION FAILED - Some issues detected")
    
    print("=" * 60)
    
    return all_valid

if __name__ == "__main__":
    success = verify_ingestion()
    exit(0 if success else 1)
