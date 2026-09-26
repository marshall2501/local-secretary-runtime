"""Fairer bounded supervisor comparison, same evidence and specialist contracts.

Use separate existing virtual environments:
    ...\\.venv-compare\\Scripts\\python.exe supervisor_trials.py --framework langgraph
    ...\\.venv-microsoft\\Scripts\\python.exe supervisor_trials.py --framework microsoft

This is a limited test of the manager's delegation, NOT proof of broad secretary
capability. Fictional evidence, read-only adapters, no database writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from shared import Evidence, MODEL, QUESTION, RULES

MEMORY_JOB = ("You are a personal-memory specialist. Summarize the supplied JSON "
              "accurately; keep entity-linkage and simulated/unverified flags. "
              "Do not invent other data or recommend performing PC changes.")
RESEARCH_JOB = ("You are a local-document specialist. Summarize the supplied "
                "fictional notes accurately, keeping their fictional provenance. "
                "Do not claim to have researched a live vendor website.")
MANAGER_JOB = RULES + (
    " You have two specialists available as tools. If the user asks about previous "
    "experience, VERIFY it by calling consult_memory_specialist before claiming "
    "there are no records. Consult research only if it could materially change "
    "the answer. Do not repeat specialist calls without a specific new question. "
    "After the available evidence, answer even when the cause remains unknown. "
    "Never invent a filesystem path, current version, real diagnosis or a tool result."
)


class BudgetedSpecialists:
    def __init__(self, mode: str):
        self.evidence = Evidence(mode)
        self.delegations = []
        self.used = set()
        self.max_distinct = 2

    def authorize(self, name: str, question: str) -> bool:
        key = name  # one call per specialist in this first bounded gate
        self.delegations.append({"to": name, "request": question,
                                 "first_call": key not in self.used})
        if key in self.used or len(self.used) >= self.max_distinct:
            return False
        self.used.add(key)
        return True

    def metrics(self):
        return {
            "delegations": self.delegations,
            "evidence_calls": self.evidence.calls,
            "invalid_memory_queries": self.evidence.invalid_queries,
            "distinct_specialists_used": sorted(self.used),
        }


def langgraph_run(model: str, mode: str, question: str) -> dict:
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langchain_ollama import ChatOllama

    budget = BudgetedSpecialists(mode)
    chat = ChatOllama(model=model, temperature=0)
    memory = create_agent(chat, [], system_prompt=MEMORY_JOB)
    research = create_agent(chat, [], system_prompt=RESEARCH_JOB)

    @tool
    def consult_memory_specialist(question: str) -> str:
        """Delegate a question about named personal history and prior attempts to the memory specialist."""
        if not budget.authorize("memory", question):
            return "Already consulted memory. Reuse prior findings and answer the user."
        data = budget.evidence.recall()
        response = memory.invoke({"messages": [
            {"role": "user", "content": "Summarize only this JSON: " + data}
        ]}, config={"recursion_limit": 5})
        return str(response["messages"][-1].content)

    @tool
    def consult_research_specialist(question: str) -> str:
        """Delegate source-backed background research to the local-document specialist."""
        if not budget.authorize("research", question):
            return "Already consulted research. Reuse prior findings and answer the user."
        data = budget.evidence.research(question)
        response = research.invoke({"messages": [
            {"role": "user", "content": "Summarize only these fictional source notes: " + data}
        ]}, config={"recursion_limit": 5})
        return str(response["messages"][-1].content)

    manager = create_agent(chat, [consult_memory_specialist,
                                  consult_research_specialist],
                           system_prompt=MANAGER_JOB)
    started = time.monotonic()
    try:
        result = manager.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": 12})
        answer = str(result["messages"][-1].content)
        error = None
    except Exception as exc:
        answer, error = None, repr(exc)
    return {"framework": "langgraph_supervisor", "model": model,
            "mode": mode, "question": question, "answer": answer,
            "error": error, **budget.metrics(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "note": "No real-PC diagnosis; no autonomous action; no persistent-task test."}


async def microsoft_run(model: str, mode: str, question: str) -> dict:
    from agent_framework import Agent, tool
    from agent_framework.ollama import OllamaChatClient

    # The installed Ollama provider beta uses this setting.
    os.environ["OLLAMA_MODEL_ID"] = model
    budget = BudgetedSpecialists(mode)
    client = OllamaChatClient()
    memory = Agent(name="MemorySpecialist", description="Personal history reviewer",
                   client=client, instructions=MEMORY_JOB)
    research = Agent(name="ResearchSpecialist", description="Fictional document reviewer",
                     client=client, instructions=RESEARCH_JOB)

    @tool(approval_mode="never_require")
    async def consult_memory_specialist(question: str) -> str:
        """Delegate a question about named personal history and prior attempts to the memory specialist."""
        if not budget.authorize("memory", question):
            return "Already consulted memory. Reuse prior findings and answer the user."
        data = budget.evidence.recall()
        response = await memory.run("Summarize only this JSON: " + data)
        return response.text or "Specialist returned no text."

    @tool(approval_mode="never_require")
    async def consult_research_specialist(question: str) -> str:
        """Delegate source-backed background research to the local-document specialist."""
        if not budget.authorize("research", question):
            return "Already consulted research. Reuse prior findings and answer the user."
        data = budget.evidence.research(question)
        response = await research.run("Summarize only these fictional source notes: " + data)
        return response.text or "Specialist returned no text."

    manager = Agent(name="SecretaryManager", client=client,
                    description="Delegates to memory and research specialists",
                    instructions=MANAGER_JOB,
                    tools=[consult_memory_specialist, consult_research_specialist])
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(manager.run(question), timeout=180)
        answer, error = response.text, None
    except Exception as exc:
        answer, error = None, repr(exc)
    return {"framework": "microsoft_supervisor", "model": model,
            "mode": mode, "question": question, "answer": answer,
            "error": error, **budget.metrics(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "note": "No real-PC diagnosis; no autonomous action; no persistent-task test."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--framework", choices=["langgraph", "microsoft"], required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    result = (langgraph_run(args.model, args.mode, args.question)
              if args.framework == "langgraph" else
              asyncio.run(microsoft_run(args.model, args.mode, args.question)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
