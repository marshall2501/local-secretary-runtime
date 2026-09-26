"""Microsoft Agent Framework Magentic manager with two specialists (read-only PoC).

Requires: pip install agent-framework-ollama agent-framework-orchestrations
Uses local Ollama only; never contacts Azure or modifies the PC.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from shared import Evidence, MODEL, QUESTION, RULES, TARGET, DOMAIN


async def run(model: str, mode: str, question: str):
    # Native Ollama provider uses OLLAMA_MODEL and OLLAMA_HOST.
    os.environ["OLLAMA_MODEL"] = model
    from agent_framework import Agent, tool
    from agent_framework.ollama import OllamaChatClient
    from agent_framework.orchestrations import MagenticBuilder

    evidence = Evidence(mode)

    @tool(approval_mode="never_require")
    def read_personal_memory(target: str = TARGET, domain: str = DOMAIN) -> str:
        """Read bounded claims and previous actions; preserve target linkage and simulation flags."""
        return evidence.recall(target, domain)

    @tool(approval_mode="never_require")
    def read_local_research(query: str) -> str:
        """Read fictional local reference notes only; not real-web information."""
        return evidence.research(query)

    client = OllamaChatClient()
    memory_agent = Agent(
        name="MemorySpecialist",
        description="Retrieves personal history, entities, claims, previous actions and results.",
        instructions=("Read personal records with read_personal_memory when asked about history. "
                      "Unlinked same-domain mock actions are NOT confirmed for the named PC. "
                      "Preserve uncertainty and provenance; never alter records."),
        client=client,
        tools=[read_personal_memory],
    )
    research_agent = Agent(
        name="ResearchSpecialist",
        description="Looks up available local reference documents and clarifies their limits.",
        instructions=("For source-backed background, call read_local_research. These sources "
                      "are fictional local fixtures and cannot prove real equipment failure. "
                      "Do not perform external operations."),
        client=client,
        tools=[read_local_research],
    )
    manager = Agent(
        name="SecretaryManager",
        description="Coordinates specialists to answer user requests within their requested scope.",
        instructions=RULES + (
            " Manage the specialists dynamically according to what evidence is missing. "
            "Do not assume that every specialist must always run. "
            "Respond to the user's question rather than proposing unauthorized repair."),
        client=client,
    )
    workflow = MagenticBuilder(
        participants=[memory_agent, research_agent],
        intermediate_output_from=[memory_agent, research_agent],
        manager_agent=manager,
        max_round_count=8,
        max_stall_count=2,
        max_reset_count=1,
    ).build()
    started = time.monotonic()
    events = []
    answer = None
    async for event in workflow.run(question, stream=True):
        if event.type == "group_chat":
            data = event.data
            if hasattr(data, "participant_name"):
                events.append({"event": "delegated",
                               "participant": str(data.participant_name)})
        elif event.type == "output":
            answer = getattr(event.data, "text", None) or str(event.data)
        elif event.type == "request_info":
            events.append({"event": "waiting_for_user"})
            break
    return {
        "framework": "Microsoft Agent Framework Magentic",
        "model": model, "source_mode": mode, "question": question,
        "answer": answer,
        "workflow_events": events,
        "evidence_calls": evidence.calls,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limitations": "No real-PC operations; no durable DB task/checkpoint tested",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.model, args.mode, args.question)),
                     ensure_ascii=False, indent=2))
