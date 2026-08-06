"""
Scenario tests for the custody state machine.

Exercises every path through handoff.py against a temporary database and
fixture registries. Requires no hardware and does not touch the real
custody log.

    python3 test_scenarios.py

Exit code 0 if every scenario behaves as expected, 1 otherwise.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import database as db

# Redirect all writes to a throwaway database before anything opens one.
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test_custody.db")

import config
import handoff

# --- Fixture registries -----------------------------------------------

WAREHOUSE = "TEST-WAREHOUSE-1"
OTHER_WAREHOUSE = "TEST-WAREHOUSE-2"
CARRIER = "TEST-CARRIER-1"
OTHER_CARRIER = "TEST-CARRIER-2"
REVOKED = "TEST-REVOKED-1"
ASSET_TAG = "TEST-ASSET-1"
STRANGER = "TEST-UNREGISTERED"
ASSET_ID = "TEST-C31-0001"


def _cred(name, org, role, revoked=False):
    return {
        "name": name,
        "organization": org,
        "role": role,
        "revoked": revoked,
        "revoked_on": "2026-07-01" if revoked else None,
        "label": name,
    }


config.CREDENTIALS = {
    WAREHOUSE: _cred("M. Reyes", "Harrison Distribution", config.ROLE_RELEASING),
    OTHER_WAREHOUSE: _cred("T. Nakamura", "Elsewhere Depot", config.ROLE_RELEASING),
    CARRIER: _cred("J. Okafor", "Northline Freight", config.ROLE_RECEIVING),
    OTHER_CARRIER: _cred("R. Vance", "Unrelated Carrier", config.ROLE_RECEIVING),
    REVOKED: _cred("D. Serrano", "Former Staff", config.ROLE_RELEASING, revoked=True),
}

config.ASSETS = {
    ASSET_TAG: {
        "asset_id": ASSET_ID,
        "description": "Test drone",
        "initial_custodian": WAREHOUSE,
        "intended_recipient": CARRIER,
        "label": "Asset tag",
    },
}

# --- Harness ----------------------------------------------------------

results = []


def fresh():
    """A new controller against an empty log."""
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    return handoff.HandoffController()


def event_types():
    return [row[3] for row in db.get_recent_events(50)]


def last_event():
    types = event_types()
    return types[0] if types else None


def check(name, passed, detail=""):
    results.append(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {name}")
    if not passed and detail:
        print(f"        {detail}")


def idle(c):
    return c.state == handoff.STATE_IDLE


# --- Scenarios --------------------------------------------------------

def scenario_happy_path():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(WAREHOUSE)
    check("happy path completes and resets",
          idle(c) and last_event() == handoff.EV_CUSTODY_ASSIGNED,
          f"state={c.state} last={last_event()}")
    check("custody moves to the receiving party",
          db.get_current_custodian(ASSET_ID, WAREHOUSE) == CARRIER,
          f"custodian={db.get_current_custodian(ASSET_ID, WAREHOUSE)}")


def scenario_credential_without_asset():
    c = fresh()
    c.handle_scan(CARRIER)
    check("credential with no asset in session is rejected",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_unmanifested_recipient():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(OTHER_CARRIER)
    check("recipient not on the manifest aborts the session",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_releasing_credential_at_receive_stage():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(WAREHOUSE)
    check("releasing credential cannot accept custody",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_wrong_custodian():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(OTHER_WAREHOUSE)
    check("a party who does not hold the asset cannot release it",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_receiving_credential_at_release_stage():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(OTHER_CARRIER)
    check("receiving credential cannot release custody",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_unregistered_idle():
    c = fresh()
    c.handle_scan(STRANGER)
    check("unregistered tag at idle is logged and rejected",
          idle(c) and last_event() == handoff.EV_UNKNOWN)


def scenario_unregistered_mid_session():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(STRANGER)
    check("unregistered tag mid-session aborts",
          idle(c) and handoff.EV_UNKNOWN in event_types())


def scenario_revoked_credential():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(REVOKED)
    check("revoked credential aborts and logs distinctly",
          idle(c) and handoff.EV_REVOKED in event_types())


def scenario_asset_mid_session():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(ASSET_TAG)
    check("asset presented mid-session aborts",
          idle(c) and last_event() == handoff.EV_REJECTED)


def scenario_expiry():
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.window_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    c.handle_scan(CARRIER)
    check("an overdue window expires at the next scan",
          idle(c) and handoff.EV_EXPIRED in event_types())


def scenario_single_hop_limitation():
    """Documented limitation: a RECEIVING credential can never release, so
    the chain supports one hop. Asserted here so the limit is explicit."""
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(WAREHOUSE)
    c.handle_scan(ASSET_TAG)
    check("delivered asset cannot open a new session",
          idle(c) and last_event() == handoff.EV_DELIVERED)


def scenario_chain_integrity():
    import verify
    c = fresh()
    c.handle_scan(ASSET_TAG)
    c.handle_scan(CARRIER)
    c.handle_scan(WAREHOUSE)
    check("hash chain verifies after a full transfer", verify.verify_chain())


# --- Runner -----------------------------------------------------------

if __name__ == "__main__":
    for fn in [
        scenario_happy_path,
        scenario_credential_without_asset,
        scenario_unmanifested_recipient,
        scenario_releasing_credential_at_receive_stage,
        scenario_wrong_custodian,
        scenario_receiving_credential_at_release_stage,
        scenario_unregistered_idle,
        scenario_unregistered_mid_session,
        scenario_revoked_credential,
        scenario_asset_mid_session,
        scenario_expiry,
        scenario_single_hop_limitation,
        scenario_chain_integrity,
    ]:
        fn()

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
