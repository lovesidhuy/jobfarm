#!/usr/bin/env python3
"""Live test: does JobSpy expose direct apply URLs for LinkedIn?

Verifies the claim: linkedin_fetch_description=True -> job_url_direct populated
with the off-LinkedIn apply URL (e.g. Greenhouse/Lever), per-job extra request.
"""
from __future__ import annotations

import json
import sys

from jobspy import scrape_jobs

TERM = "Software Engineer"
LOCATION = "Vancouver, BC"
N = 15


def summarize(df, label):
    total = len(df)
    cols = list(df.columns) if total else []
    has_direct_col = "job_url_direct" in cols
    has_apply_col = "apply_url" in cols
    direct_filled = 0
    gh_lever = []
    if total and has_direct_col:
        direct_filled = df["job_url_direct"].notna().sum()
        mask = df["job_url_direct"].str.contains(
            r"greenhouse|lever\.co|grnh\.se", case=False, na=False
        )
        gh_lever = df[mask][["title", "company", "job_url_direct"]].to_dict("records")
    print(f"\n=== {label} ===")
    print(f"rows={total} has_job_url_direct_col={has_direct_col} has_apply_url_col={has_apply_col}")
    print(f"job_url_direct populated: {direct_filled}/{total}")
    print(f"greenhouse/lever direct URLs: {len(gh_lever)}")
    for r in gh_lever[:10]:
        print(f"  GH/L ever: {r['title'][:40]} @ {r['company'][:25]} -> {r['job_url_direct']}")
    return {
        "label": label,
        "rows": total,
        "columns": cols,
        "direct_filled": int(direct_filled),
        "gh_lever": gh_lever,
    }


def main():
    results = []

    # TEST 1: without linkedin_fetch_description (baseline)
    try:
        df1 = scrape_jobs(
            site_name=["linkedin"],
            search_term=TERM,
            location=LOCATION,
            results_wanted=N,
            linkedin_fetch_description=False,
        )
        results.append(summarize(df1, "linkedin_fetch_description=False"))
    except Exception as e:
        print(f"\n=== baseline scrape FAILED: {type(e).__name__}: {e} ===")
        results.append({"label": "baseline", "error": str(e)})

    # TEST 2: with linkedin_fetch_description=True (the claim)
    try:
        df2 = scrape_jobs(
            site_name=["linkedin"],
            search_term=TERM,
            location=LOCATION,
            results_wanted=N,
            linkedin_fetch_description=True,
        )
        results.append(summarize(df2, "linkedin_fetch_description=True"))
        # sample of what direct URLs look like
        if len(df2) and "job_url_direct" in df2.columns:
            sample = df2[df2["job_url_direct"].notna()][
                ["title", "company", "job_url", "job_url_direct"]
            ].head(8)
            print("\n--- sample direct URLs ---")
            for _, r in sample.iterrows():
                print(f"  {r['title'][:35]:35} | {r['job_url_direct']}")
    except Exception as e:
        print(f"\n=== fetch_description scrape FAILED: {type(e).__name__}: {e} ===")
        results.append({"label": "fetch_description", "error": str(e)})

    out = "/tmp/jobspy_linkedin_test.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    sys.exit(main())
