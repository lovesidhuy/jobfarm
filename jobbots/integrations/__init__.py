"""jobbots.integrations — portal adapters, browser backends, AI, comms, storage.

Phase 1 contains interface contracts only. Concrete adapters wrap the existing
production modules (master/* bots, core/ats adapters, core/discovery providers,
NSTbrowser/Playwright helpers, Telegram, email, Mongo) without moving them.
"""
