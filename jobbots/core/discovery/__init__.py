"""Dual-engine discovery package.

Exports ``run_discovery()`` — the top-level entry point invoked by the
supervisor (``--stage discover``) or the standalone runner script.
"""
from jobbots.core.discovery.planner import run_discovery

__all__ = ["run_discovery"]
