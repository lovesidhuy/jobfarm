"""jobbots — unified CLI and shared core for the job-application automation system.

Phase 1 of the refactor: this package is an *additive* layer. It wraps the
existing, production-proven entry points in ``automation_monorepo`` and
``master/`` without moving or modifying them.

Frozen components (do not change behavior):
  - Q&A chain: core.llm_backend.answer_policy -> modules.qa_answer_bank ->
    core.shared_modules.form_answers -> core.llm_backend.ai fallback
  - Profiles: automation_monorepo/config/{general,it}/*
  - Portal bots: master/{gen_indeed,it_indeed cwgeopy}/Auto_indeed/*
"""

__version__ = "0.1.0"
