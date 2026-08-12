#!/usr/bin/env python3
"""Inspect and recover the shared discovery/application queue."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.job_queue import JobQueue

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="command",required=True)
    sub.add_parser("stats"); sub.add_parser("recover")
    en=sub.add_parser("enqueue-json"); en.add_argument("path"); en.add_argument("--portal",required=True); en.add_argument("--profile",required=True)
    one=sub.add_parser("enqueue-stdin"); one.add_argument("--portal",required=True); one.add_argument("--profile",required=True)
    args=ap.parse_args(); q=JobQueue()
    if args.command=="stats": print(json.dumps(q.counts(),indent=2)); return
    if args.command=="recover": print(json.dumps({"released_expired_leases":q.release_expired(),"counts":q.counts()},indent=2)); return
    records=json.loads(sys.stdin.read()) if args.command=="enqueue-stdin" else json.loads(Path(args.path).read_text())
    if args.command=="enqueue-stdin" and isinstance(records,dict): records=[records]
    if isinstance(records,dict): records=records.get("jobs",records.get("results",[]))
    created=existing=0
    for row in records:
        jid=str(row.get("job_id") or row.get("jobId") or row.get("id") or "").strip()
        if not jid: continue
        _,was_created=q.enqueue(portal=args.portal,profile=args.profile,source_job_id=jid,
          title=row.get("title") or row.get("jobTitle") or "Unknown",company=row.get("company") or "Unknown",
          location=row.get("location") or "",url=row.get("url") or row.get("job_url") or row.get("jobUrl") or "",
          description=row.get("description") or row.get("detailText") or "",gate_reason=row.get("gate_reason") or "imported approved job",
          resume_policy="tailored" if args.profile.lower()=="it" else "default",metadata=row)
        created+=int(was_created); existing+=int(not was_created)
    print(json.dumps({"created":created,"existing":existing,"counts":q.counts()},indent=2))
if __name__=="__main__": main()
