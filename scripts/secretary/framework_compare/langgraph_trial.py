"""LangGraph-backed supervisor with two specialist subagents (read-only PoC).

Requires: pip install langchain langgraph langchain-ollama
Both manager and specialists use the same installed Ollama model.
This is not the existing production Secretary Core.
"""
from __future__ import annotations

import argparse
import json
import time

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

from shared import Evidence, MODEL, QUESTION, RULES, TARGET, DOMAIN


def run(model: str, mode: str, question: str):
    evidence = Evidence(mode)
    chat = ChatOllama(model=model, temperature=0)

    @tool
    def read_personal_memory(target: str = TARGET, domain: str = DOMAIN) -> str:
        """Read bounded personal claims and previous actions, preserving entity linkage and simulated status."""
        return evidence.recall(target, domain)

    @tool
    def read_local_research(query: str) -> str:
        """Search fictional local notes for context; never claim this is real vendor information."""
        return evidence.research(query)

    memory_agent = create_agent(
        model=chat, tools=[read_personal_memory],
        system_prompt=("You are the personal-memory specialist. For relevant previous "
                       "experience, call read_personal_memory with the named target. "
                       "Summarize only what the evidence supports, including missing linkage "
                       "and simulated status. No system changes."),
    )
    research_agent = create_agent(
        model=chat, tools=[read_local_research],
        system_prompt=("You are the local-document specialist. Call read_local_research "
                       "when documents are needed; preserve fictional provenance and "
                       "never invent real-web facts. No system changes."),
    )
    delegations = []

    @tool
    def consult_memory_specialist(question: str) -> str:
        """Delegate a personal history or prior-remediation question to the memory specialist."""
        delegations.append({"to": "memory_specialist", "request": question})
        result = memory_agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": 10},
        )
        return str(result["messages"][-1].content)

    @tool
    def consult_research_specialist(question: str) -> str:
        """Delegate a source-research or troubleshooting-document question to the research specialist."""
        delegations.append({"to": "research_specialist", "request": question})
        result = research_agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": 10},
        )
        return str(result["messages"][-1].content)

    manager = create_agent(
        model=chat,
        tools=[consult_memory_specialist, consult_research_specialist],
        system_prompt=RULES + (
            " When previous experiences matter, delegate to memory first or when needed; "
            "when current general information is needed, delegate to research. "
            "You own the final answer, and may call a specialist more than once if new "
            "evidence requires it. Never assert an uncalled tool returned evidence."),
    )
    started = time.monotonic()
    result = manager.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": 22},
    )
    return {
        "framework": "LangGraph-backed LangChain supervisor",
        "model": model, "source_mode": mode, "question": question,
        "answer": str(result["messages"][-1].content),
        "delegations": delegations, "evidence_calls": evidence.calls,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limitations": "No real-PC operations; no durable DB task/checkpoint tested",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    print(json.dumps(run(args.model, args.mode, args.question),
                     ensure_ascii=False, indent=2))
