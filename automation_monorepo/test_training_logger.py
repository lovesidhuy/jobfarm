#!/usr/bin/env python3
"""
Test script to verify training logger works for both Indeed IT and Indeed General bots
"""
import os
import sys
from pathlib import Path

# Add the root to the path
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

def run_training_logger_check(bot_name: str):
    """Test training logger for a specific bot"""
    print(f"\n=== Testing {bot_name} ===")
    
    # Set the bot name environment variable
    os.environ["BOT_NAME"] = bot_name
    
    try:
        # Import the core portal module
        from core.portals.indeed import _init_ai_client, _ai_answer
        
        # Initialize the AI client (this should set up the training logger)
        _init_ai_client()
        
        # Test a simple AI answer
        answer = _ai_answer(
            question="What is your availability?",
            hint="date available",
            job_context="Test job at Test Company",
            run_id="test_run_" + bot_name,
            job_id="test_job_" + bot_name
        )
        
        print(f"✓ Training logger initialized for {bot_name}")
        print(f"✓ AI answer test completed: {answer[:50] if answer else 'No answer'}")
        
        # Check if training directory was created
        bot_type = "it" if "it" in bot_name.lower() else "general"
        training_dir = Path(f"data/training/{bot_type}_data")
        
        if training_dir.exists():
            print(f"✓ Training directory exists: {training_dir}")
            
            # Check for training files
            qa_files = list(training_dir.glob("qa-*.jsonl"))
            if qa_files:
                print(f"✓ Training files created: {qa_files}")
            else:
                print(f"⚠ No training files found yet (may need actual AI interaction)")
        else:
            print(f"✗ Training directory not found: {training_dir}")
            
    except Exception as e:
        print(f"✗ Error testing {bot_name}: {e}")

def main():
    print("Testing Training Logger for Indeed Bots")
    print("=" * 50)
    
    # Test both bots
    run_training_logger_check("indeed_it")
    run_training_logger_check("indeed_general")
    
    print("\n" + "=" * 50)
    print("Test completed!")

if __name__ == "__main__":
    main()
