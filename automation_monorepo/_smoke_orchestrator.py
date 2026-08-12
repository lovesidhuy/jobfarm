"""Smoke tests for the new autonomous control plane modules.

Validates imports and basic method contracts for:
- core/session_check.py
- core/google_sheets_reporter.py
- core/vm_lifecycle.py
- core/error_recovery.py
- orchestrator.py
- scripts/cron_scheduler.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_imports():
    print("[SmokeTest] Verifying imports...")
    import core.session_check as sc
    import core.google_sheets_reporter as gsr
    import core.vm_lifecycle as vl
    import core.error_recovery as er
    import orchestrator as orch
    import scripts.cron_scheduler as sched
    
    print("[SmokeTest] ✓ Imports succeeded!")


def test_session_check_mocked():
    print("[SmokeTest] Verifying core.session_check with mock data...")
    import core.session_check as sc
    
    # Mock is_mongodb_available and urllib.request
    with patch("core.session_check.is_mongodb_available", return_value=True):
        assert sc.check_mongodb() is True
        
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 200, "data": []}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # Enable key config
        with patch("core.session_check.get_secret", side_effect=lambda k, d="": "key-xyz" if k == "NSTBROWSER_API_KEY" else d):
            assert sc.check_nstbrowser_api() is True

    print("[SmokeTest] ✓ Session pre-check mocked tests passed!")


def test_reporter_mocked():
    print("[SmokeTest] Verifying core.google_sheets_reporter with mock data...")
    import core.google_sheets_reporter as gsr
    
    dummy_stats = {
        "date": "2026-07-16",
        "discovered_total": 10,
        "discovered_portals": {"indeed": 4, "glassdoor": 3, "linkedin": 2, "workopolis": 1},
        "applied_total": 5,
        "applied_portals": {"indeed": 2, "glassdoor": 1, "linkedin": 1, "workopolis": 1},
        "bookmarked": 2,
        "failed": 1,
        "queued": 20,
    }
    
    report_text = gsr.generate_text_report(dummy_stats)
    assert "Discover" in report_text
    assert "Applied" in report_text
    assert "2026-07-16" in report_text
    print("[SmokeTest] ✓ Report text generation OK!")
    
    # Mock sheets supporting False to verify graceful fallback
    with patch("core.google_sheets_reporter.GOOGLE_SHEETS_SUPPORT", False):
        assert gsr.write_to_google_sheet(dummy_stats) is False
        assert gsr.upload_to_google_drive(report_text, "test.txt") is None

    print("[SmokeTest] ✓ Reporting mocked tests passed!")


def test_vm_lifecycle_mocked():
    print("[SmokeTest] Verifying core.vm_lifecycle with mock data...")
    import core.vm_lifecycle as vl
    
    # Mock boto3 and AWS CLI describe-instances failures
    with patch("core.vm_lifecycle.get_instance_id_boto3", return_value=None):
        with patch("core.vm_lifecycle.get_instance_id_cli", return_value=None):
            assert vl.resolve_instance_id() is None
            
    with patch("core.vm_lifecycle.resolve_instance_id", return_value="i-0123456789abcdef0"):
        # Mock stop_instances in boto3
        mock_boto3 = MagicMock()
        sys.modules["boto3"] = mock_boto3
        
        with patch("core.vm_lifecycle.send_telegram_alert") as mock_alert:
            # We mock subprocess.run to verify it fallbacks or completes
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                
                # Should send alert and return True (using mocked boto3/CLI)
                result = vl.stop_vm()
                mock_alert.assert_called_once()
                print(f"[SmokeTest] Stop VM Mock result: {result}")

    # Remove mock module
    sys.modules.pop("boto3", None)
    print("[SmokeTest] ✓ VM lifecycle mocked tests passed!")


def test_error_recovery_mocked():
    print("[SmokeTest] Verifying core.error_recovery with mock data...")
    import core.error_recovery as er
    
    # Verify diagnosis routes
    with patch("core.error_recovery.clean_port_conflict", return_value=True):
        assert er.execute_recovery_actions("indeed_it", "CDP port 9222 connection failed") is True
        
    with patch("core.error_recovery.restore_profile", return_value=True):
        assert er.execute_recovery_actions("indeed_it", "Chrome profile corrupted Lockfile present") is True
        
    with patch("core.error_recovery.handle_rate_limit") as mock_rate:
        assert er.execute_recovery_actions("indeed_it", "Rate limit exceeded (HTTP 429)") is True
        mock_rate.assert_called_once()

    print("[SmokeTest] ✓ SRE error recovery mocked tests passed!")


def test_orchestrator_mocked():
    print("[SmokeTest] Verifying orchestrator with mock data...")
    import orchestrator as orch
    
    orchestrator = orch.DailyOrchestrator(workers=2, once=True)
    
    # Mock the run cycle stages
    with patch.object(orchestrator, "run_preflight_checks") as mock_preflight:
        with patch.object(orchestrator, "run_discovery") as mock_discover:
            with patch.object(orchestrator, "run_applications") as mock_apply:
                with patch.object(orchestrator, "compile_reports") as mock_report:
                    with patch.object(orchestrator, "run_backup") as mock_backup:
                        with patch.object(orchestrator, "shutdown_vm") as mock_shutdown:
                            
                            # Case 1: Preflight fails MongoDB
                            mock_preflight.return_value = {"mongodb": False, "nstbrowser_api": True}
                            with patch("orchestrator.send_telegram_alert") as mock_alert:
                                orchestrator.execute_auto_cycle()
                                mock_alert.assert_called_with(
                                    "🚨 Orchestrator HALTING: MongoDB is unreachable. Manual SRE review required.",
                                    bot_name="system",
                                    alert_type="db_fatal",
                                    force=True
                                )
                                
                            # Case 2: Preflight succeeds
                            mock_preflight.return_value = {
                                "mongodb": True,
                                "nstbrowser_api": True,
                                "bots": {"indeed_it": True, "glassdoor_it": False}
                            }
                            orchestrator.execute_auto_cycle()
                            mock_discover.assert_called_with(["indeed"])
                            mock_apply.assert_called_with(["indeed"])
                            mock_report.assert_called_once()
                            mock_backup.assert_called_once()
                            mock_shutdown.assert_called_once()

    print("[SmokeTest] ✓ Orchestrator mocked tests passed!")


def main():
    print("=======================================")
    print(" Jobbots Orchestration Smoke Test Suite")
    print("=======================================")
    test_imports()
    test_session_check_mocked()
    test_reporter_mocked()
    test_vm_lifecycle_mocked()
    test_error_recovery_mocked()
    test_orchestrator_mocked()
    print("\n🎉 ALL SMOKE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
