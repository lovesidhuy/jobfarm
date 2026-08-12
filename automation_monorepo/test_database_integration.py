#!/usr/bin/env python3
"""
Test script to verify database integration works for both Indeed IT and Indeed General bots
"""
import os
import sys
from pathlib import Path

# Add the root to the path
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

def test_database_integration(bot_name: str):
    """Test database integration for a specific bot"""
    print(f"\n=== Testing {bot_name} Database Integration ===")
    
    # Set the bot name environment variable
    os.environ["BOT_NAME"] = bot_name
    
    try:
        # Inject master directory to satisfy 'modules' imports
        target_dir = _ROOT.parent / "master" / "it_indeed cwgeopy" / "Auto_indeed"
        if str(target_dir) not in sys.path:
            sys.path.insert(0, str(target_dir))
            
        # Import the core portal module
        from core.portals.indeed import _init_ai_client, _ai_answer
        
        # Initialize the AI client (this should set up the database store)
        _init_ai_client()
        
        # Test a simple AI answer (this should trigger database storage)
        answer = _ai_answer(
            question="What is your expected salary?",
            hint="salary expectation",
            job_context="Test job at Test Company",
            run_id="test_run_" + bot_name,
            job_id="test_job_" + bot_name
        )
        
        print(f"✓ Database store initialized for {bot_name}")
        print(f"✓ AI answer test completed: {answer[:50] if answer else 'No answer'}")
        
        # Check if database fallback directory was created
        fallback_dir = Path(f"data/db_fallback/{bot_name}")
        
        if fallback_dir.exists():
            print(f"✓ Database fallback directory exists: {fallback_dir}")
            
            # Check for fallback files
            fallback_files = list(fallback_dir.glob("db_fallback_*.jsonl"))
            if fallback_files:
                print(f"✓ Database fallback files: {fallback_files}")
            else:
                print(f"⚠ No fallback files found yet (MongoDB may be working)")
        else:
            print(f"✗ Database fallback directory not found: {fallback_dir}")
            
        # Check training directory
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
        import traceback
        traceback.print_exc()

def test_mongodb_connection():
    """Test if MongoDB is available"""
    print(f"\n=== Testing MongoDB Connection ===")
    
    try:
        from core.llm_backend.db import MongoStore
        from pathlib import Path
        
        # Test connection to MongoDB
        store = MongoStore(
            bot_id="test_bot",
            uri="mongodb://localhost:27017",
            database="test_db",
            fallback_dir=Path("data/db_fallback/test")
        )
        
        if store.connected:
            print("✓ MongoDB is available and connected")
            
            # Test a simple insert
            doc_id = store.insert("questions", {
                "run_id": "test_run",
                "job_id": "test_job", 
                "question": "Test question",
                "kind": "test",
                "answer": "Test answer",
                "source": "test",
                "provider": "test",
                "accepted": True
            })
            
            print(f"✓ MongoDB insert test successful: {doc_id}")
            
        else:
            print("⚠ MongoDB not available, will use fallback files")
            
    except Exception as e:
        print(f"✗ MongoDB connection test failed: {e}")

def main():
    print("Testing Database Integration for Indeed Bots")
    print("=" * 60)
    
    # Test MongoDB connection first
    test_mongodb_connection()
    
    # Test both bots
    test_database_integration("indeed_it")
    test_database_integration("indeed_general")
    
    print("\n" + "=" * 60)
    print("Database integration test completed!")
    
    # Show directory structure
    print("\nDirectory Structure:")
    print("data/")
    print("├── training/")
    print("│   ├── it_data/")
    print("│   └── general_data/")
    print("└── db_fallback/")
    print("    ├── indeed_it/")
    print("    └── indeed_general/")

if __name__ == "__main__":
    main()
