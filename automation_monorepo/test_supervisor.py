"""Manual Indeed-IT supervisor launcher (not an import-time pytest test)."""
import supervisor

def main():
    supervisor.BOT_CONFIGS = [c for c in supervisor.BOT_CONFIGS if c["bot_name"] == "indeed_it"]
    print("Running only indeed_it via supervisor wrapper.")
    supervisor.main()

if __name__ == "__main__":
    main()
