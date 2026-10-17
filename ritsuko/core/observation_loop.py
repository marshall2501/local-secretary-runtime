# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Legacy import facade for the canonical RITSUKO Task execution Core.

The historical module is retained only for existing integrations and tests;
it has no Task runner, Source executor or independent state writer.
New composition roots import ritsuko.core.task_execution directly.
"""
from ritsuko.core.task_execution import (
    review_proposal,
    resume_user_answer,
    run_observation_loop,
    run_pkb_observation_loop,
)

__all__ = [
    "run_observation_loop",
    "run_pkb_observation_loop",
    "resume_user_answer",
    "review_proposal",
]
