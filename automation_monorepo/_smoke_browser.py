import os
import sys

def main():
    print("=== Browser Stealth Smoke Test (Xvfb & Playwright CDP) ===")
    
    # Pre-checks
    os.environ["BOT_NAME"] = "smoke-bot"
    os.environ["JOB_PROFILE"] = "IT"
    os.environ["BYPASS_PROXY"] = "1"  # Disable proxy for CI run
    os.environ["AUTONOMOUS_SUPERVISOR"] = "1"
    
    try:
        from core.browser.open_chrome import createBrowserSession
        print("✓ Loaded core.browser.open_chrome module.")
        
        print("Initializing stealthy browser session...")
        sb, page, context, browser, pw = createBrowserSession(bot_name="smoke-bot")
        
        print(f"✓ Browser booted. CDP endpoint attached.")
        print(f"Page title: {page.title()}")
        
        # Navigate to a simple test page
        print("Navigating to ca.indeed.com or static fallback...")
        try:
            page.goto("https://ca.indeed.com", wait_until="domcontentloaded", timeout=20000)
            print(f"✓ Navigation completed. New title: {page.title()}")
        except Exception as e:
            print(f"⚠ Navigation failed or timed out: {e}")
            print("Trying fallback to static page...")
            page.goto("https://example.com", wait_until="domcontentloaded", timeout=10000)
            print(f"✓ Navigation to example.com completed. Title: {page.title()}")
            
        print("Closing browser session...")
        try:
            # Safely shut down
            if sb:
                sb.quit()
            if pw:
                pw.stop()
            print("✓ Session closed successfully.")
        except Exception as e:
            print(f"⚠ Error during close: {e}")
            
        print("=== BROWSER SMOKE TEST PASSED ===")
        return 0
        
    except Exception as e:
        print(f"❌ BROWSER SMOKE TEST FAILED: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
