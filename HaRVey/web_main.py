"""
Entry point for the HaRVey web app.

Usage:
    python web_main.py
    python web_main.py --host 0.0.0.0 --port 8000
"""

import argparse

from web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the HaRVey web app")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
