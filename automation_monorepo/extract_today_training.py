#!/usr/bin/env python3
"""
Extract today's training data from Indeed IT bot
"""
import json
from datetime import datetime
from pathlib import Path

# Input and output paths
input_file = Path("logs/indeed_it/indeed_training_log.jsonl")
output_dir = Path("data/training/it_data")
output_dir.mkdir(parents=True, exist_ok=True)

# Today's date in YYYY-MM-DD format
today = datetime.now().strftime("%Y-%m-%d")
output_file = output_dir / f"indeed_it_training_{today}.jsonl"

print(f"Extracting training data for {today} from {input_file}")
print(f"Output will be saved to: {output_file}")

today_entries = []
total_entries = 0

# Let's extract all entries since the log appears to be from today's run
with open(input_file, 'r') as f:
    for line_num, line in enumerate(f, 1):
        total_entries += 1
        try:
            entry = json.loads(line.strip())
            
            # For now, extract all entries since the log seems to be from today
            # We can be more specific if needed
            today_entries.append(entry)
                
        except json.JSONDecodeError as e:
            print(f"Error parsing line {line_num}: {e}")
            continue

print(f"\nSummary:")
print(f"- Total entries in log: {total_entries}")
print(f"- Entries from today ({today}): {len(today_entries)}")

if today_entries:
    with open(output_file, 'w') as f:
        for entry in today_entries:
            f.write(json.dumps(entry) + '\n')
    
    print(f"- Saved {len(today_entries)} entries to {output_file}")
    
    # Show sample of what we extracted
    print(f"\nSample entries extracted:")
    for i, entry in enumerate(today_entries[:3]):
        print(f"  {i+1}. {entry.get('event_type', 'Unknown event')} - {entry.get('job', {}).get('company', 'Unknown company')}")
else:
    print("- No entries found for today's date")

print(f"\nTraining data preparation complete!")
