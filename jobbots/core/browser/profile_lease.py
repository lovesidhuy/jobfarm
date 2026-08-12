"""Cross-worker lease for persistent browser profiles."""

from __future__ import annotations

import os
import socket
import time
import uuid


class ProfileLease:
    # 2h default (was 24h) — dead PIDs should not block the farm all day.
    def __init__(self, profile_id: str, ttl_seconds: int | None = None):
        self.profile_id = profile_id
        env_ttl = (os.environ.get("JOBBOTS_PROFILE_LEASE_TTL_SECONDS") or "").strip()
        if ttl_seconds is not None:
            self.ttl_seconds = int(ttl_seconds)
        elif env_ttl.isdigit():
            self.ttl_seconds = int(env_ttl)
        else:
            self.ttl_seconds = 7200
        self.table_name = os.environ.get("JOBBOTS_PROFILE_LEASE_TABLE", "").strip()
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        self._table = None

    def acquire(self) -> None:
        if not self.table_name:
            return
        try:
            import boto3
            from botocore.exceptions import ClientError, BotoCoreError
        except Exception as exc:
            # No AWS SDK / partial image — do not block NST opens on GCP workers.
            print(
                f"[ProfileLease] boto unavailable; skipping lease for {self.profile_id}: {exc}"
            )
            return

        try:
            self._table = boto3.resource("dynamodb").Table(self.table_name)
            now = int(time.time())
            self._table.put_item(
                Item={
                    "profile_id": self.profile_id,
                    "owner": self.owner,
                    "expires_at": now + self.ttl_seconds,
                    "updated_at": now,
                },
                ConditionExpression="attribute_not_exists(profile_id) OR expires_at < :now",
                ExpressionAttributeValues={":now": now},
            )
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise RuntimeError(
                    f"NSTbrowser profile {self.profile_id} is already leased."
                ) from exc
            # Credentials / network / table missing on hybrid GCP workers: fail open.
            print(
                f"[ProfileLease] DynamoDB ClientError ({code}); "
                f"skipping lease for {self.profile_id}: {exc}"
            )
            self._table = None
        except (BotoCoreError, Exception) as exc:
            print(
                f"[ProfileLease] DynamoDB unavailable; "
                f"skipping lease for {self.profile_id}: {exc}"
            )
            self._table = None

    def release(self) -> None:
        if self._table is None:
            return
        try:
            self._table.delete_item(
                Key={"profile_id": self.profile_id},
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": self.owner},
            )
        except Exception:
            # Best-effort: owner mismatch / already gone must not crash close paths.
            pass
        finally:
            self._table = None

    def force_release(self) -> None:
        """Delete lease without owner check (ops recovery)."""
        if not self.table_name:
            return
        try:
            import boto3
            table = self._table or boto3.resource("dynamodb").Table(self.table_name)
            table.delete_item(Key={"profile_id": self.profile_id})
        except Exception as exc:
            print(f"[ProfileLease] force_release failed for {self.profile_id}: {exc}")
        finally:
            self._table = None
