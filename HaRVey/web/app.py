"""
Minimal Flask chat front end for the HaRVey query pipeline.

Routes:
    GET  /                    - chat page (re-renders this session's conversation)
    POST /api/chat            - JSON {"message": "..."} -> {kind, answer (markdown), steps,
                                sources, images, options, errors, retrieval_query, status},
                                built from the pipeline's structured QueryState.response
    POST /api/reset           - start a new conversation
    GET  /diagrams/<filename> - serve an image from extracted_diagrams/
    GET  /manuals/<filename>  - serve a source PDF from the documents folder (sources link here)

Conversations live in server memory, keyed by an id in the Flask session
cookie. They are lost when the server restarts.
"""

import os
import threading
import uuid
from collections import OrderedDict
from logging import CRITICAL
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, session, url_for

from src.config import load_config
from src.ingestion.tools import media_kind
from src.query.graph import chat_turn_from_result, create_query_graph, execute_query_pipeline
from src.query.nodes import DIAGRAM_DIR_NAME, ERROR_MESSAGE, PROJECT_ROOT
from src.utils.logging import setup_logging

DIAGRAM_DIR = PROJECT_ROOT / DIAGRAM_DIR_NAME
MAX_DISPLAY_TURNS = 50  # turns kept per conversation (the pipeline itself uses only the last few)
MAX_CONVERSATIONS = 200  # least recently used conversations are dropped beyond this
NO_RESULTS_MESSAGE = "No relevant documents found."


class ConversationStore:
    """Thread-safe in-memory conversations: chat id -> list of turn records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chats: OrderedDict[str, list[dict]] = OrderedDict()

    def get(self, chat_id: str) -> list[dict]:
        with self._lock:
            if chat_id in self._chats:
                self._chats.move_to_end(chat_id)
                return list(self._chats[chat_id])
            return []

    def append(self, chat_id: str, record: dict) -> None:
        with self._lock:
            turns = self._chats.setdefault(chat_id, [])
            turns.append(record)
            del turns[:-MAX_DISPLAY_TURNS]
            self._chats.move_to_end(chat_id)
            while len(self._chats) > MAX_CONVERSATIONS:
                self._chats.popitem(last=False)

    def clear(self, chat_id: str) -> None:
        with self._lock:
            self._chats.pop(chat_id, None)


def _diagram_caption(diagram: dict) -> str:
    """Caption under a diagram image, e.g. "manual.pdf, page 10 · INSTALLATION > B. Divider"."""
    caption = f"{diagram['filename']}, page {diagram['page']}"
    return f"{caption} · {diagram['section']}" if diagram.get("section") else caption


def _record_from_result(message: str, result) -> dict:
    """
    One displayed turn, built from the pipeline's structured response.

    Without a response (no documents found, or the pipeline failed) the turn
    shows NO_RESULTS_MESSAGE or ERROR_MESSAGE. `turn` is the history entry, or
    None for turns that should not be remembered.
    """
    response = result.response or {
        "kind": "error" if result.errors else "empty",
        "answer": ERROR_MESSAGE if result.errors else NO_RESULTS_MESSAGE,
        "steps": [], "sources": [], "diagrams": [], "options": [],
    }
    return {
        "question": message,
        "kind": response["kind"],
        "answer": response["answer"],
        "steps": response["steps"],
        "sources": response["sources"],
        "diagrams": [
            {"file": Path(d["path"]).name, "caption": _diagram_caption(d)} for d in response["diagrams"]
        ],
        "options": response["options"],
        "errors": list(result.errors),
        "retrieval_query": result.retrieval_query if result.retrieval_query != message else None,
        "turn": chat_turn_from_result(message, result),
    }


def _source_with_url(source: dict, doc_dir: Path) -> dict:
    """
    A source plus its "media" type ("pdf", "image" or "video") and a "url" to
    the file: a PDF opens at the cited page, an image or video opens as is.

    No url is added when the file is not in the documents folder, so the browser
    only gets links the server can serve.
    """
    name = source.get("filename") or ""  # may include subfolders: "folder/a.pdf"
    kind = media_kind(name)
    if kind is None:
        return source
    source = {**source, "media": kind}
    path = (doc_dir / name).resolve()
    if not path.is_relative_to(doc_dir.resolve()) or not path.is_file():
        return source
    url = url_for("serve_manual", filename=name)
    if kind == "pdf" and source.get("page"):
        url += f"#page={source['page']}"
    return {**source, "url": url}


def _public(record: dict, doc_dir: Path) -> dict:
    """The parts of a turn record the browser needs (everything but the history entry)."""
    return {
        "question": record["question"],
        "kind": record["kind"],
        "answer": record["answer"],
        "steps": record["steps"],
        "sources": [_source_with_url(s, doc_dir) for s in record["sources"]],
        "images": [
            {"src": url_for("serve_diagram", filename=d["file"]), "caption": d["caption"]}
            for d in record["diagrams"]
        ],
        "options": record["options"],
        "errors": record["errors"],
        "retrieval_query": record["retrieval_query"],
    }


def create_app() -> Flask:
    """Build the Flask app, loading config and compiling the query graph once."""
    # Quiet console; agent details (incl. replies without a tool call) go to the log file
    setup_logging(level=CRITICAL, log_file=PROJECT_ROOT / "logs" / "harvey.log")

    app = Flask(__name__)
    # A random key means sessions (and so conversations) reset on restart, like the store
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)

    config = load_config()
    graph = create_query_graph(config)
    store = ConversationStore()
    doc_dir = Path(config.document_input_dir)
    if not doc_dir.is_absolute():
        doc_dir = PROJECT_ROOT / doc_dir

    def chat_id() -> str:
        if "chat_id" not in session:
            session["chat_id"] = uuid.uuid4().hex
        return session["chat_id"]

    @app.get("/")
    def index():
        history = [_public(r, doc_dir) for r in store.get(chat_id())]
        return render_template("index.html", history=history)

    @app.post("/api/chat")
    def chat():
        data = request.get_json(silent=True) or {}
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return jsonify({"error": "Message cannot be empty."}), 400
        message = message.strip()

        cid = chat_id()
        records = store.get(cid)
        history = [r["turn"] for r in records if r["turn"] is not None]

        result = execute_query_pipeline(message, config, graph, chat_history=history)

        # Failed or empty turns are shown but not remembered as context (record["turn"] is None)
        record = _record_from_result(message, result)
        store.append(cid, record)
        return jsonify({**_public(record, doc_dir), "status": result.status})

    @app.post("/api/reset")
    def reset():
        store.clear(chat_id())
        session.pop("chat_id", None)
        return jsonify({"ok": True})

    @app.get("/diagrams/<path:filename>")
    def serve_diagram(filename: str):
        # send_from_directory rejects paths that escape DIAGRAM_DIR
        return send_from_directory(DIAGRAM_DIR, filename)

    @app.get("/manuals/<path:filename>")
    def serve_manual(filename: str):
        # Ingested file types only, shown inline: the PDF viewer honors #page=N,
        # and range requests (handled by send_from_directory) let videos seek
        if media_kind(filename) is None:
            abort(404)
        return send_from_directory(doc_dir, filename, as_attachment=False)

    return app
