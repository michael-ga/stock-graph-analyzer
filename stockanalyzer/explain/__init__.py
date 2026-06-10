"""Deterministic (no-AI) natural-language layer: turns the engine's structured
signals/verdict/levels into plain-English recommendations, scenarios, checklists,
and narratives for non-expert users.
"""
from .glossary import PlainTerm, explain_signal
from .usecase import UseCase
from .recommend import Recommendation, build_recommendation
from .narrative import timeframe_caption

__all__ = [
    "PlainTerm", "explain_signal",
    "UseCase",
    "Recommendation", "build_recommendation",
    "timeframe_caption",
]
