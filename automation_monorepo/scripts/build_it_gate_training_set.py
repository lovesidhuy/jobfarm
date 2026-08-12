#!/usr/bin/env python3
"""Build / re-evaluate the local IT title gate from user review spreadsheets.

Default sources (Downloads, after user review passes):
  1. ``indeed_it_queued_jobs_review (1).xlsx`` — full queued universe
  2. ``indeed_it_queued_jobs_review_filtered (2).xlsx`` — IT keep set
  3. ``indeed_it_queued_jobs_review_filtered (3).xlsx`` — apply set
     (currently identical to (2); both treated as apply-positive)

Outputs under ``data/training/``:
  * ``it_title_gate_labels.jsonl``
  * ``it_title_gate_evaluation.json``
  * ``it_title_gate_overrides.json``  (apply positives + skip residual FPs)
  * ``user_reviews/*.csv`` copies for reproducibility

Env:
  ``IT_GATE_REVIEW_ALL`` / ``IT_GATE_REVIEW_APPLY`` optional absolute paths.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery._gate_adapter import (  # noqa: E402
    _review_key,
    hard_screen_job,
    is_ambiguous_title_reason,
)
import core.discovery._gate_adapter as ga  # noqa: E402

OUT_DIR = ROOT / "data" / "training"
LABELS = OUT_DIR / "it_title_gate_labels.jsonl"
REPORT = OUT_DIR / "it_title_gate_evaluation.json"
OVERRIDES = OUT_DIR / "it_title_gate_overrides.json"
REV_DIR = OUT_DIR / "user_reviews"

DEFAULT_ALL = Path.home() / "Downloads" / "indeed_it_queued_jobs_review (1).xlsx"
DEFAULT_APPLY = Path.home() / "Downloads" / "indeed_it_queued_jobs_review_filtered (2).xlsx"
# (3) is apply-narrow; fall back to (2) when missing/identical
DEFAULT_APPLY3 = Path.home() / "Downloads" / "indeed_it_queued_jobs_review_filtered (3).xlsx"


def _load_frames():
    import pandas as pd

    all_path = Path(os.getenv("IT_GATE_REVIEW_ALL", str(DEFAULT_ALL)))
    apply_path = Path(os.getenv("IT_GATE_REVIEW_APPLY", str(DEFAULT_APPLY)))
    apply3 = Path(os.getenv("IT_GATE_REVIEW_APPLY3", str(DEFAULT_APPLY3)))
    if not all_path.is_file():
        raise SystemExit(f"Missing full review workbook: {all_path}")
    if not apply_path.is_file():
        raise SystemExit(f"Missing filtered IT/apply workbook: {apply_path}")

    all_df = pd.read_excel(all_path)
    apply_df = pd.read_excel(apply_path)
    if apply3.is_file():
        apply3_df = pd.read_excel(apply3)
        # Prefer narrower apply set when distinct; else keep (2).
        if len(apply3_df) and set(apply3_df["url"].dropna().astype(str)) != set(
            apply_df["url"].dropna().astype(str)
        ):
            apply_df = apply3_df

    REV_DIR.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(REV_DIR / "indeed_it_queued_jobs_review_all.csv", index=False)
    apply_df.to_csv(REV_DIR / "indeed_it_queued_jobs_review_apply.csv", index=False)
    return all_df, apply_df


def main() -> None:
    all_df, apply_df = _load_frames()
    pos_urls = set(apply_df["url"].dropna().astype(str).str.strip())

    # Pure local gate (no overrides)
    ga._review_overrides = {}
    pure_rows = []
    for _, row in all_df.iterrows():
        title = str(row.get("title") or "")
        company = str(row.get("company") or "")
        location = str(row.get("location") or "")
        url = str(row.get("url") or "").strip()
        passed, _, reason = hard_screen_job(
            title=title,
            company=company,
            description="",
            location=location,
            easy_apply=True,
        )
        amb = (not passed) and is_ambiguous_title_reason(reason)
        pure_rows.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "passed": passed,
                "reason": reason,
                "ambiguous": amb,
                "is_pos": url in pos_urls,
                "key": _review_key(title, company),
            }
        )

    overrides: dict[str, str] = {}
    for r in pure_rows:
        if r["is_pos"]:
            overrides[r["key"]] = "apply"
        elif r["passed"]:
            overrides[r["key"]] = "skip"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(
        json.dumps(dict(sorted(overrides.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # With overrides
    ga._review_overrides = None
    labels = []
    counts: Counter[str] = Counter()
    bucket: Counter[str] = Counter()
    for r in pure_rows:
        passed, _, reason = hard_screen_job(
            title=r["title"],
            company=r["company"],
            description="",
            location=r["location"],
            easy_apply=True,
        )
        amb = (not passed) and is_ambiguous_title_reason(reason)
        if passed:
            pred = "allow"
            bucket["allow"] += 1
        elif amb:
            pred = "ambiguous"
            bucket["ambiguous"] += 1
        else:
            pred = "reject"
            bucket["reject_hard"] += 1

        is_pos = r["is_pos"]
        if is_pos:
            if pred == "allow":
                outcome = "true_positive"
            elif pred == "ambiguous":
                outcome = "false_negative_deferred"
            else:
                outcome = "false_negative"
        else:
            if pred == "allow":
                outcome = "false_positive"
            elif pred == "ambiguous":
                outcome = "true_negative_deferred"
            else:
                outcome = "true_negative"
        counts[outcome] += 1
        labels.append(
            {
                "label": "positive" if is_pos else "negative",
                "label_source": "user_review_xlsx",
                "prediction": pred,
                "outcome": outcome,
                "gate_reason": reason,
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "url": r["url"],
            }
        )

    with LABELS.open("w", encoding="utf-8") as fh:
        for rec in labels:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    tp, fp = counts["true_positive"], counts["false_positive"]
    fn, fn_def = counts["false_negative"], counts["false_negative_deferred"]
    n_pos = sum(1 for r in pure_rows if r["is_pos"])
    pure_tp = sum(1 for r in pure_rows if r["is_pos"] and r["passed"])
    pure_fn_h = sum(1 for r in pure_rows if r["is_pos"] and not r["passed"] and not r["ambiguous"])
    pure_fn_d = sum(1 for r in pure_rows if r["is_pos"] and r["ambiguous"])
    pure_fp = sum(1 for r in pure_rows if not r["is_pos"] and r["passed"])

    report = {
        "source_rows": len(labels),
        "user_selected_positive": n_pos,
        "overrides": {
            "total": len(overrides),
            "apply": sum(1 for v in overrides.values() if v == "apply"),
            "skip": sum(1 for v in overrides.values() if v == "skip"),
        },
        "prediction_buckets": dict(bucket),
        "counts": dict(counts),
        "metrics_with_overrides": {
            "precision_allow": tp / (tp + fp) if tp + fp else 0,
            "recall_allow_only": tp / (tp + fn + fn_def) if tp + fn + fn_def else 0,
            "recall_if_batch_recovers_deferred": (tp + fn_def) / (tp + fn + fn_def)
            if tp + fn + fn_def
            else 0,
            "hard_false_negatives": fn,
            "deferred_positives": fn_def,
            "false_positives": fp,
        },
        "metrics_pure_local_no_override": {
            "true_positive": pure_tp,
            "false_negative_hard": pure_fn_h,
            "false_negative_deferred": pure_fn_d,
            "false_positive": pure_fp,
            "recall_allow": pure_tp / n_pos if n_pos else 0,
            "recall_with_batch": (pure_tp + pure_fn_d) / n_pos if n_pos else 0,
        },
        "false_negatives_hard": [r for r in labels if r["outcome"] == "false_negative"],
        "false_negatives_deferred": [r for r in labels if r["outcome"] == "false_negative_deferred"],
        "false_positives": [r for r in labels if r["outcome"] == "false_positive"],
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "labels": str(LABELS),
                "report": str(REPORT),
                "overrides": str(OVERRIDES),
                **report["metrics_with_overrides"],
                "pure": report["metrics_pure_local_no_override"],
            },
            indent=2,
        )
    )
    if report["metrics_with_overrides"]["hard_false_negatives"]:
        print("HARD misses still remaining:", file=sys.stderr)
        for r in report["false_negatives_hard"]:
            print(f"  - {r['title']} @ {r['company']}: {r['gate_reason']}", file=sys.stderr)


if __name__ == "__main__":
    main()
