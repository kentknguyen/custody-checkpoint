"""
Custody Checkpoint
Custody Authentication Layer

Entry point. Reads tags at the checkpoint and drives the three-stage handoff
state machine: asset, then receiving party, then releasing party.

All custody events are written to a hash-chained log. Run verify.py to check
that log's integrity.
"""

import sys
import time

import RPi.GPIO as GPIO
GPIO.setwarnings(False)

from mfrc522 import SimpleMFRC522

import config
import database as db
import handoff


def print_banner():
    print("=" * 46)
    print("  CUSTODY CHECKPOINT")
    print("=" * 46)
    print(f"  Credentials:     {len(config.CREDENTIALS)}")
    print(f"  Assets:          {len(config.ASSETS)}")
    print(f"  Transfer window: {config.TRANSFER_WINDOW_SECONDS}s")
    print(f"  Debounce:        {config.DEBOUNCE_SECONDS}s")
    print("=" * 46)
    print()


def print_event_log(limit=3):
    events = db.get_recent_events(limit)
    print("--- Custody Event Log (most recent first) ---")
    if not events:
        print("  (empty)")
    for timestamp, card_id, role, event_type, message, transfer_id, asset_id in events:
        tid = f"#{transfer_id}" if transfer_id is not None else "--"
        aid = asset_id if asset_id else "-"
        print(f"  [{timestamp}] {tid:>4}  {event_type:<24} {aid}")
        print(f"      {message}")
    print("-" * 45)
    print()


def prompt_for(state):
    if state == handoff.STATE_AWAITING_RECEIVING:
        return "Awaiting RECEIVING party — must match the manifest..."
    if state == handoff.STATE_AWAITING_RELEASING:
        return "Awaiting RELEASING party — must be the current custodian..."
    return "Awaiting ASSET TAG to open a handoff session..."


def resolve_open_session(controller):
    """Close out a session interrupted by shutdown, rather than leaving a
    PENDING transfer with no terminal event."""
    if controller.state == handoff.STATE_IDLE:
        return
    print(f"WARNING: session for {controller.asset_id} "
          f"(transfer #{controller.transfer_id}) was still open.")
    db.log_event(controller.asset_uid, config.ROLE_ASSET, handoff.EV_REJECTED,
                 "Session ended by checkpoint shutdown before completion",
                 controller.transfer_id, controller.asset_id)
    db.close_transfer(controller.transfer_id, "REJECTED")
    print("Logged as an incomplete session.")


def main():
    print_banner()

    problems = config.validate()
    if problems:
        print("CONFIGURATION ERROR — checkpoint will not start:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nFix config.py and try again.")
        sys.exit(1)

    db.init_db()
    controller = handoff.HandoffController()
    reader = SimpleMFRC522()

    last_uid = None
    last_scan_at = 0.0
    last_prompt = None

    try:
        while True:
            prompt = prompt_for(controller.state)
            if prompt != last_prompt:
                print(prompt)
                last_prompt = prompt

            raw_uid, _ = reader.read()
            uid = str(raw_uid)
            now = time.time()

            # Same tag still in the field - not a new presentation.
            if uid == last_uid and (now - last_scan_at) < config.DEBOUNCE_SECONDS:
                last_scan_at = now
                time.sleep(0.5)
                continue

            last_uid = uid
            last_scan_at = now

            print(f"\nTag presented: {uid}")
            for line in controller.handle_scan(uid):
                print(line)
            print()

            if config.DISPLAY_EVENT_LOG:
                print_event_log()

            last_prompt = None
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nCheckpoint shutting down.")
        resolve_open_session(controller)
        print("Run 'python3 verify.py' to check custody log integrity.")
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
