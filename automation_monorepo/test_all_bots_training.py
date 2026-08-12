#!/usr/bin/env python3
"""
Test script to verify training data and database integration for all bots:
- Indeed IT
- Indeed General  
- Glassdoor IT
- Glassdoor General
"""
import os
import sys
from pathlib import Path

# Add the root to the path
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

def test_bot_training_data(bot_name: str):
    """Test training data for a specific bot"""
    print(f"\n=== Testing {bot_name} ===")
    
    # Set the bot name environment variable
    os.environ["BOT_NAME"] = bot_name
    
    try:
        # Import the core portal module
        if "glassdoor" in bot_name.lower():
            # Glassdoor bots use glassdoor portal which imports indeed as smartapply
            from core.portals.glassdoor import smartapply
            # Initialize AI client through smartapply
            smartapply._init_ai_client()
            print(f"✓ Glassdoor AI client initialized for {bot_name}")
        else:
            # Indeed bots use indeed portal directly
            from core.portals.indeed import _init_ai_client, _ai_answer
            _init_ai_client()
            print(f"✓ Indeed AI client initialized for {bot_name}")
        
        # Test a simple AI answer
        if "glassdoor" in bot_name.lower():
            # Use smartapply's AI answer function
            answer = smartapply._ai_answer(
                question="What is your availability?",
                hint="date available",
                job_context="Test job at Test Company",
                run_id="test_run_" + bot_name,
                job_id="test_job_" + bot_name
            )
        else:
            # Use indeed's AI answer function
            answer = _ai_answer(
                question="What is your availability?",
                hint="date available",
                job_context="Test job at Test Company",
                run_id="test_run_" + bot_name,
                job_id="test_job_" + bot_name
            )
        
        print(f"✓ AI answer test completed: {answer[:50] if answer else 'No answer'}")
        
        # Check training directory
        if "glassdoor" in bot_name.lower():
            bot_type = "it" if "it" in bot_name.lower() else "general"
            training_dir = Path(f"data/training/glassdoor_{bot_type}_data")
        else:
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
            
        # Check database fallback directory
        fallback_dir = Path(f"data/db_fallback/{bot_name}")
        
        if fallback_dir.exists():
            print(f"✓ Database fallback directory exists: {fallback_dir}")
        else:
            print(f"⚠ Database fallback directory not found: {fallback_dir}")
            
    except Exception as e:
        print(f"✗ Error testing {bot_name}: {e}")
        import traceback
        traceback.print_exc()

def main():
    print("Testing Training Data Integration for All Bots")
    print("=" * 70)
    
    # Test all four bots
    bots_to_test = [
        "indeed_it",
        "indeed_general", 
        "glassdoor_it",
        "glassdoor_general"
    ]
    
    for bot in bots_to_test:
        test_bot_training_data(bot)
    
    print("\n" + "=" * 70)
    print("All bots training data test completed!")
    
    # Show complete directory structure
    print("\nComplete Directory Structure:")
    print("data/")
    print("├── training/")
    print("│   ├── it_data/                    # Indeed IT training files")
    print("│   ├── general_data/               # Indeed General training files")
    print("│   ├── glassdoor_it_data/          # Glassdoor IT training files")
    print("│   └── glassdoor_general_data/     # Glassdoor General training files")
    print("└── db_fallback/")
    print("    ├── indeed_it/                  # Indeed IT database fallback")
    print("    ├── indeed_general/             # Indeed General database fallback")
    print("    ├── glassdoor_it/               # Glassdoor IT database fallback")
    print("    └── glassdoor_general/          # Glassdoor General database fallback")
    
    print("\nDatabase Names:")
    print("- indeed_it_db")
    print("- indeed_general_db")
    print("- glassdoor_it_db")
    print("- glassdoor_general_db")

if __name__ == "__main__":
    main()
