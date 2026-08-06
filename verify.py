"""
Integrity verification for the custody event log.

Recomputes the hash chain from the genesis anchor forward and reports the
first row where it breaks. Detects two tampering modes:

  MODIFICATION       - a field was altered, so the row no longer hashes to
                       its stored row_hash.
  INTERIOR DELETION  - a row was removed from the middle, so the following
                       row's prev_hash points at a hash no longer present.

It does NOT detect truncation. Deleting rows from the end of the log leaves
every remaining link valid, and an empty log verifies trivially. Detecting
that requires comparing the head hash against a value recorded somewhere the
attacker does not control. See the Limitations section of the README.

Exit code 0 if intact, 1 if broken or unreadable.
"""

import os
import sqlite3
import sys

from database import DB_PATH, GENESIS_HASH, compute_hash


def load_events():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"No custody log at {DB_PATH}. Run main.py first."
        )

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, timestamp, card_id, role, event_type, message,
                   transfer_id, asset_id, prev_hash, row_hash
            FROM custody_events
            ORDER BY id ASC
        """)
        return cursor.fetchall()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Custody log at {DB_PATH} is missing or malformed ({exc}). "
            f"Delete it and run main.py to recreate the schema."
        ) from exc
    finally:
        conn.close()


def verify_chain():
    try:
        events = load_events()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"CANNOT VERIFY: {exc}")
        return False

    if not events:
        print("Custody log is empty - nothing to verify.")
        print("NOTE: an empty log is indistinguishable from a fully truncated one.")
        return True

    print(f"Verifying {len(events)} event(s)...\n")
    expected_prev = GENESIS_HASH

    for row in events:
        (row_id, timestamp, card_id, role, event_type,
         message, transfer_id, asset_id, prev_hash, row_hash) = row

        if prev_hash != expected_prev:
            print(f"CHAIN BROKEN at event id {row_id}")
            print(f"  Expected prev_hash: {expected_prev}")
            print(f"  Found prev_hash:    {prev_hash}")
            print("  A preceding event was deleted or reordered.")
            return False

        recomputed = compute_hash(prev_hash, timestamp, card_id, role,
                                  event_type, message, transfer_id, asset_id)
        if recomputed != row_hash:
            print(f"CHAIN BROKEN at event id {row_id}")
            print(f"  Stored hash:     {row_hash}")
            print(f"  Recomputed hash: {recomputed}")
            print("  This event's contents were modified after logging.")
            print(f"  Row: [{timestamp}] {card_id} {event_type} "
                  f"({asset_id}) - {message}")
            return False

        expected_prev = row_hash

    print("CHAIN INTACT")
    print(f"  Events:    {len(events)}")
    print(f"  Head hash: {expected_prev}")
    print("\n  Verification confirms no event was modified or removed from the")
    print("  interior. It cannot detect truncation - compare the head hash")
    print("  above against an independently recorded value.")
    return True


if __name__ == "__main__":
    sys.exit(0 if verify_chain() else 1)
