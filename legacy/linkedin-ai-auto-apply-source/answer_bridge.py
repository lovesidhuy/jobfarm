#!/usr/bin/env python3
"""Answer Bridge — Node.js subprocess caller for LinkedIn bot.

Usage: python3 answer_bridge.py "question text" '["opt1","opt2"]' "hint" "jobContext"
Output: JSON {"value":"...","source":"...","score":0.9,"matched_question":""}
Or: {} on failure.
"""
import sys, json, os, traceback
from pathlib import Path

# CRITICAL: add monorepo to path BEFORE any imports
_MONOREPO = Path(__file__).resolve().parent / ".." / ".." / "automation_monorepo"
if _MONOREPO.exists():
    sys.path.insert(0, str(_MONOREPO))

os.environ.setdefault("JOB_PROFILE", "IT")

def main():
    question = sys.argv[1] if len(sys.argv) > 1 else ""
    options = []
    if len(sys.argv) > 2:
        try:
            options = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            options = [sys.argv[2]]
    
    hint = sys.argv[3] if len(sys.argv) > 3 else ""
    job_context = sys.argv[4] if len(sys.argv) > 4 else ""
    
    try:
        from core.shared_modules.form_answers import resolve_answer
        
        result = resolve_answer(
            question,
            hint=hint,
            options=options if options else None,
            job_context=job_context,
            allow_ai=True,
        )
        
        if result:
            output = {
                "value": result.value,
                "source": result.source,
                "score": round(result.score, 3),
                "matched_question": result.matched_question or "",
            }
            print(json.dumps(output))
        else:
            print("{}")
            
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print("ERROR:" + str(exc))

if __name__ == "__main__":
    main()
