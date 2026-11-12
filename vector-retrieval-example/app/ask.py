"""Answer one CLI question with a one-retrieval ReAct graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.config import DEFAULT_DB_PATH, require_api_key, setting
from app.store import open_store


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    retrieval_calls: int
    retrieval_status: str
    terminal_reason: str


class RetrievalInput(BaseModel):
    query: str = Field(description="A focused semantic search query")
    filters: dict[str, str] = Field(
        default_factory=dict,
        description="Optional exact metadata filters, such as a source filename",
    )
    k: int = Field(default=4, ge=1, le=5, description="Number of chunks to return")


def build_graph(db_path: Path):
    """Create a graph with exactly one possible retrieval execution."""
    store = open_store(db_path)

    @tool(args_schema=RetrievalInput)
    def retrieve(
        query: str, filters: dict[str, str] | None = None, k: int = 4
    ) -> str:
        """Search the ingested documents for passages relevant to a question."""
        results = store.similarity_search_with_score(
            query, k=k, filter=filters or None
        )
        return json.dumps(
            [
                {
                    "rank": rank,
                    "distance": round(float(distance), 4),
                    "source": document.metadata.get("source"),
                    "page": document.metadata.get("page"),
                    "chunk_id": document.metadata.get("chunk_id"),
                    "text": document.page_content,
                }
                for rank, (document, distance) in enumerate(results, start=1)
            ]
        )

    model = ChatOpenAI(
        model=setting("LLM_MODEL", "Qwen3.8-Flash-Next"),
        base_url=setting("LLM_BASE_URL", "http://golem:4000/v1"),
        api_key=require_api_key("LLM_API_KEY"),
        temperature=0,
    )
    acting_model = model.bind_tools(
        [retrieve], tool_choice="required", parallel_tool_calls=False
    )

    def act(state: AgentState) -> dict[str, object]:
        prompt = SystemMessage(
            content=(
                "You are a document question-answering agent. Use retrieve exactly once. "
                "Choose a focused search query based on the user's question. Use an empty "
                "filters object unless the question identifies a source filename."
            )
        )
        response = acting_model.invoke([prompt, *state["messages"]])
        update: dict[str, object] = {"messages": [response]}
        if not response.tool_calls:
            update["terminal_reason"] = "model_did_not_request_retrieval"
        return update

    def route_after_action(state: AgentState) -> str:
        request = state["messages"][-1]
        if isinstance(request, AIMessage) and request.tool_calls:
            return "retrieve_once"
        return END

    def retrieve_once(state: AgentState) -> dict[str, object]:
        request = state["messages"][-1]
        if not isinstance(request, AIMessage) or not request.tool_calls:
            return {"terminal_reason": "model_did_not_request_retrieval"}

        tool_call = request.tool_calls[0]
        retrieval_status = "ok"
        try:
            if tool_call["name"] != retrieve.name:
                raise ValueError(f"unknown tool: {tool_call['name']}")
            observation = retrieve.invoke(tool_call["args"])
            if json.loads(observation) == []:
                retrieval_status = "empty"
        except Exception as error:
            retrieval_status = "error"
            observation = json.dumps(
                {"error": f"{type(error).__name__}: {error}"}
            )
        messages: list[ToolMessage] = [
            ToolMessage(content=observation, tool_call_id=tool_call["id"])
        ]
        for extra_call in request.tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content="Rejected: this agent permits one retrieval call.",
                    tool_call_id=extra_call["id"],
                )
            )
        return {
            "messages": messages,
            "retrieval_calls": 1,
            "retrieval_status": retrieval_status,
        }

    def synthesize(state: AgentState) -> dict[str, object]:
        prompt = SystemMessage(
            content=(
                "Answer the user's question using only the retrieval observation. "
                "Cite supporting passages as [source, page N]. If the passages do not "
                "support an answer, say what information is missing. Do not call tools."
            )
        )
        response = model.invoke([prompt, *state["messages"]])
        terminal_reason = {
            "ok": "answered_after_one_retrieval",
            "empty": "answered_with_no_retrieval_results",
            "error": "answered_after_retrieval_error",
        }.get(state["retrieval_status"], "answered_after_unknown_retrieval_status")
        return {
            "messages": [response],
            "terminal_reason": terminal_reason,
        }

    graph = StateGraph(AgentState)
    graph.add_node("act", act)
    graph.add_node("retrieve_once", retrieve_once)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "act")
    graph.add_conditional_edges("act", route_after_action)
    graph.add_edge("retrieve_once", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


def print_trace(result: AgentState) -> None:
    """Expose the action, observation, answer, and controller-owned bound."""
    for number, message in enumerate(result["messages"], start=1):
        if isinstance(message, HumanMessage):
            record_type = "question"
            value: object = message.content
        elif isinstance(message, ToolMessage):
            record_type = "retrieval_observation"
            value = json.loads(str(message.content)) if str(message.content).startswith("[") else message.content
        elif isinstance(message, AIMessage) and message.tool_calls:
            record_type = "retrieval_request"
            value = message.tool_calls
        else:
            record_type = "answer"
            value = message.content
        print(json.dumps({"record": number, "type": record_type, "value": value}, indent=2))
    print(
        json.dumps(
            {
                "type": "terminal",
                "terminal_reason": result["terminal_reason"],
                "retrieval_calls": result["retrieval_calls"],
                "retrieval_status": result["retrieval_status"],
            },
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="Question to answer from the document store")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit("No Chroma database found. Run app.ingest first.")
    graph = build_graph(args.db)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=args.question)],
            "retrieval_calls": 0,
            "retrieval_status": "not_run",
            "terminal_reason": "",
        }
    )
    print_trace(result)


if __name__ == "__main__":
    main()
