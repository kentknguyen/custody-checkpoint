"""
Scenario tests for the custody state machine.

Exercises every path through handoff.py against a temporary database and
fixture registries. Requires no hardware and does not touch the real
custody log.

    pytest
"""

import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

import database as db

# Redirect all writes to a throwaway database before anything opens one.
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test_custody.db")

import config
import handoff
import verify

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


# --- Fixtures and helpers ---------------------------------------------

@pytest.fixture
def controller():
    """A controller backed by an empty custody log."""
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    return handoff.HandoffController()


def event_types():
    return [row[3] for row in db.get_recent_events(50)]


def last_event():
    types = event_types()
    return types[0] if types else None


def is_idle(c):
    return c.state == handoff.STATE_IDLE


# --- Completing a transfer --------------------------------------------

def test_happy_path_completes_and_resets(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(WAREHOUSE)
    assert is_idle(controller)
    assert last_event() == handoff.EV_CUSTODY_ASSIGNED


def test_custody_moves_to_the_receiving_party(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(WAREHOUSE)
    assert db.get_current_custodian(ASSET_ID, WAREHOUSE) == CARRIER


# --- Scans that should be refused --------------------------------------

def test_credential_with_no_asset_in_session_is_rejected(controller):
    controller.handle_scan(CARRIER)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


def test_recipient_not_on_the_manifest_aborts_the_session(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(OTHER_CARRIER)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


def test_releasing_credential_cannot_accept_custody(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(WAREHOUSE)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


def test_a_party_who_does_not_hold_the_asset_cannot_release_it(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(OTHER_WAREHOUSE)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


def test_receiving_credential_cannot_release_custody(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(OTHER_CARRIER)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


def test_asset_presented_mid_session_aborts(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(ASSET_TAG)
    assert is_idle(controller)
    assert last_event() == handoff.EV_REJECTED


# --- Credentials the registry does not accept --------------------------

def test_unregistered_tag_at_idle_is_logged_and_rejected(controller):
    controller.handle_scan(STRANGER)
    assert is_idle(controller)
    assert last_event() == handoff.EV_UNKNOWN


def test_unregistered_tag_mid_session_aborts(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(STRANGER)
    assert is_idle(controller)
    assert handoff.EV_UNKNOWN in event_types()


def test_revoked_credential_aborts_and_logs_distinctly(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(REVOKED)
    assert is_idle(controller)
    assert handoff.EV_REVOKED in event_types()


# --- Session lifecycle --------------------------------------------------

def test_an_overdue_window_expires_at_the_next_scan(controller):
    controller.handle_scan(ASSET_TAG)
    controller.window_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    controller.handle_scan(CARRIER)
    assert is_idle(controller)
    assert handoff.EV_EXPIRED in event_types()


def test_delivered_asset_cannot_open_a_new_session(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(WAREHOUSE)
    controller.handle_scan(ASSET_TAG)
    assert is_idle(controller)
    assert last_event() == handoff.EV_DELIVERED


# --- Log integrity ------------------------------------------------------

def test_hash_chain_verifies_after_a_full_transfer(controller):
    controller.handle_scan(ASSET_TAG)
    controller.handle_scan(CARRIER)
    controller.handle_scan(WAREHOUSE)
    assert verify.verify_chain()
