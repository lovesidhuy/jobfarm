"""
Vendored core library for the job-applier bots.

This package is COPIED VERBATIM into each bot. It must not import from any
bot-specific module. Each bot owns its own copy and runs fully independently:
no shared runtime state, no shared router, no shared cache, no shared queues.

Public API (stable surface used by bots):
    core.config.load_bot_config(path) -> BotConfig
    core.fallback.ProviderChain(...)
    core.rate_limit.TokenBucket(...)
    core.db.MongoStore(...)
    core.training_logger.TrainingLogger(...)
    core.state.Checkpoint(...)
    core.supervisor.Supervisor(...)
    core.ai_client.AIClient(...)
    core.answer_policy.classify(question, options=, control_type=, values=)
    core.answer_controls.apply_radio / apply_select / apply_listbox / match_option
    core.textarea_strategy.classify_textarea / should_block_summary
                            build_behavioral_prompt / build_short_answer_prompt
    core.submit_verify.SubmitResult / wait_for / verify_submit
                          verify_state / retry_action

Versioning: bump CORE_VERSION when the public API changes; the vendor sync
script writes this into each bot's copy so you can detect drift.
"""

CORE_VERSION = "0.4.0"
