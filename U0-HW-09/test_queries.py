"""
Test suite for querying the agent with all required queries.

This script:
1. Parses queries from U0-HW-09-required-queries.md
2. Executes each query through the query pipeline
3. Captures results to results/query_results.txt
"""

import re
from pathlib import Path
from datetime import datetime

from src.config import load_config
from src.query.graph import create_query_graph, execute_query_pipeline
from src.utils.logging import setup_logging
from logging import CRITICAL

logger = setup_logging(level=CRITICAL)


def parse_queries_from_markdown(markdown_file: str) -> dict:
    """
    Parse queries from the markdown file.
    
    Args:
        markdown_file: Path to the markdown file containing queries
        
    Returns:
        Dictionary mapping query IDs to query text
    """
    queries = {}
    
    with open(markdown_file, 'r') as f:
        content = f.read()
    
    # Split by ## to find sections
    sections = re.split(r'## ', content)
    
    for section in sections[1:]:  # Skip the header section
        lines = section.strip().split('\n')
        if not lines:
            continue
        
        # First line is the header (e.g., "Q1: Food Code Section Lookup")
        header = lines[0]
        
        # Extract query ID and title
        match = re.match(r'(Q\d+):\s*(.*)', header)
        if not match:
            continue
        
        query_id = match.group(1)
        query_title = match.group(2)
        
        # Join remaining lines as the query text
        query_text = '\n'.join(lines[1:]).strip()
        
        queries[query_id] = {
            'title': query_title,
            'text': query_text
        }
    
    return queries


def execute_all_queries(queries: dict, config, graph) -> dict:
    """
    Execute all queries and capture results.
    
    Args:
        queries: Dictionary of query IDs to query info
        config: Configuration object
        graph: Query pipeline graph
        
    Returns:
        Dictionary mapping query IDs to results
    """
    results = {}
    
    for query_id in sorted(queries.keys()):
        query_info = queries[query_id]
        query_text = query_info['text']
        
        print(f"Executing {query_id}...", end=" ", flush=True)
        
        try:
            result = execute_query_pipeline(query_text, config, graph)
            results[query_id] = {
                'title': query_info['title'],
                'query': query_text,
                'status': result.status,
                'response': result.final_response,
                'errors': result.errors,
                'success': True
            }
            print("✓")
        except Exception as e:
            results[query_id] = {
                'title': query_info['title'],
                'query': query_text,
                'status': 'failed',
                'response': None,
                'errors': [str(e)],
                'success': False
            }
            print(f"✗ ({str(e)[:50]}...)")
    
    return results


def format_results_to_file(results: dict, output_file: str) -> None:
    """
    Format and write results to a single consolidated file.
    
    Args:
        results: Dictionary mapping query IDs to results
        output_file: Path to output file
    """
    with open(output_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("QUERY TEST RESULTS\n")
        f.write("=" * 80 + "\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        
        # Summary statistics
        total_queries = len(results)
        successful_queries = sum(1 for r in results.values() if r['success'])
        failed_queries = total_queries - successful_queries
        
        f.write(f"Summary: {successful_queries}/{total_queries} queries executed successfully\n")
        f.write(f"Failed: {failed_queries}\n")
        f.write("=" * 80 + "\n\n")
        
        # Results for each query
        for query_id in sorted(results.keys()):
            result = results[query_id]
            
            f.write(f"\n{'=' * 80}\n")
            f.write(f"{query_id}: {result['title']}\n")
            f.write(f"{'=' * 80}\n\n")
            
            f.write(f"Query Text:\n{'-' * 80}\n")
            f.write(f"{result['query']}\n\n")
            
            f.write(f"Status: {result['status']}\n")
            
            if result['success']:
                if result['response']:
                    f.write(f"\nResponse:\n{'-' * 80}\n")
                    f.write(f"{result['response']}\n")
                else:
                    f.write("\nResponse: (empty)\n")
                
                if result['errors']:
                    f.write(f"\nWarnings/Errors:\n{'-' * 80}\n")
                    for error in result['errors']:
                        f.write(f"  • {error}\n")
            else:
                f.write(f"\nExecution Failed:\n{'-' * 80}\n")
                for error in result['errors']:
                    f.write(f"  • {error}\n")
            
            f.write("\n")
        
        # Footer
        f.write("=" * 80 + "\n")
        f.write("END OF RESULTS\n")
        f.write("=" * 80 + "\n")


def main() -> None:
    """
    Main entry point for the test suite.
    """
    # Get paths
    script_dir = Path(__file__).parent
    queries_file = script_dir / "U0-HW-09-required-queries.md"
    output_file = script_dir / "results" / "query_results.txt"
    
    print("\n" + "=" * 80)
    print("QUERY TEST SUITE")
    print("=" * 80)
    
    # Parse queries
    print("\n1. Parsing queries from markdown...", end=" ", flush=True)
    queries = parse_queries_from_markdown(str(queries_file))
    print(f"✓ Found {len(queries)} queries")
    
    # Load configuration
    print("2. Loading configuration...", end=" ", flush=True)
    config = load_config()
    print("✓")
    
    # Create graph
    print("3. Creating query pipeline graph...", end=" ", flush=True)
    graph = create_query_graph(config)
    print("✓")
    
    # Execute queries
    print(f"4. Executing {len(queries)} queries...\n")
    results = execute_all_queries(queries, config, graph)
    
    # Write results
    print(f"\n5. Writing results to {output_file}...", end=" ", flush=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    format_results_to_file(results, str(output_file))
    print("✓")
    
    # Print summary
    successful = sum(1 for r in results.values() if r['success'])
    failed = len(results) - successful
    
    print("\n" + "=" * 80)
    print(f"RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total Queries: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Output File: {output_file}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
