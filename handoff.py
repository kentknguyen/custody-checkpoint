"""
Three-party custody transfer state machine.

A transfer moves one identified asset from its current custodian to the
recipient named in the registry manifest. Three presentations are required,
in this order:

  1. Asset tag       identifies what is moving. The checkpoint derives the
                     current custodian from the custody log and reads the
                     intended recipient from the asset registry.

  2. Receiving party must match the manifest.

  3. Releasing party must be the current custodian. Scanning last makes the
                     release informed consent - the custodian has seen the
                     recipient verified before giving the asset up.

Any rejected scan aborts the session; resuming requires presenting the asset
again. Expiry is evaluated lazily, because the reader blocks between scans,
so an overdue window is detected at the next presentation rather than in
real time.
"""

from datetime import datetime, timezone, timedelta

import config
import database as db

# States
STATE_IDLE = "IDLE"
STATE_AWAITING_RECEIVING = "AWAITING_RECEIVING"
STATE_AWAITING_RELEASING = "AWAITING_RELEASING"

# Event types
EV_ASSET_PRESENTED = "ASSET_PRESENTED"
EV_RECEIVER_VERIFIED = "RECEIVER_VERIFIED"
EV_COMPLETED = "TRANSFER_COMPLETED"
EV_CUSTODY_ASSIGNED = "CUSTODY_ASSIGNED"
EV_EXPIRED = "TRANSFER_EXPIRED"
EV_DELIVERED = "ASSET_ALREADY_DELIVERED"
EV_REJECTED = "SCAN_REJECTED"
EV_UNKNOWN = "TAG_UNKNOWN"
EV_REVOKED = "CREDENTIAL_REVOKED"

LOCKED = "SYSTEM LOCKED - Operating software inaccessible"
INCOMPLETE = "SYSTEM LOCKED - Custody transfer incomplete"
ACTIVE = "SYSTEM ACTIVE - Drone operating software accessible"


def _who(uid):
    """Short description of a credential for log messages."""
    c = config.CREDENTIALS.get(uid)
    if not c:
        return f"unregistered tag {uid}"
    return f"{c['name']} ({c['organization']})"


class HandoffController:
    def __init__(self):
        self._reset()

    def _reset(self):
        self.state = STATE_IDLE
        self.transfer_id = None
        self.asset_uid = None
        self.asset_id = None
        self.custodian = None
        self.recipient = None
        self.window_expires_at = None

    # -- session control -------------------------------------------------

    def _abort(self, uid, role, message, display):
        """Log a rejection, resolve the open transfer, and end the session."""
        db.log_event(uid, role, EV_REJECTED, message,
                     self.transfer_id, self.asset_id)
        if self.transfer_id is not None:
            db.close_transfer(self.transfer_id, "REJECTED")
        self._reset()
        return display + ["Session aborted. Present the asset again to retry."]

    def _expire_if_stale(self):
        if self.state == STATE_IDLE:
            return False
        if datetime.now(timezone.utc) < self.window_expires_at:
            return False

        db.log_event(self.asset_uid, config.ROLE_ASSET, EV_EXPIRED,
                     f"Session for {self.asset_id} expired after "
                     f"{config.TRANSFER_WINDOW_SECONDS}s without completion",
                     self.transfer_id, self.asset_id)
        db.close_transfer(self.transfer_id, "EXPIRED")
        self._reset()
        return True

    # -- entry point -----------------------------------------------------

    def handle_scan(self, uid):
        uid = str(uid)
        out = []

        if self._expire_if_stale():
            out.append("PRIOR SESSION EXPIRED - window closed, no transfer recorded")

        asset = config.ASSETS.get(uid)
        credential = config.CREDENTIALS.get(uid)

        if asset is None and credential is None:
            return out + self._handle_unregistered(uid)

        if asset is not None:
            return out + self._handle_asset(uid, asset)

        return out + self._handle_credential(uid, credential)

    # -- tag classes -----------------------------------------------------

    def _handle_unregistered(self, uid):
        db.log_event(uid, config.ROLE_UNKNOWN, EV_UNKNOWN,
                     "Presentation of a tag in neither registry",
                     self.transfer_id, self.asset_id)
        display = [f"UNREGISTERED TAG - UID {uid} is not a known credential or asset"]

        if self.state == STATE_IDLE:
            self._reset()
            return display + [LOCKED]
        return self._abort(uid, config.ROLE_UNKNOWN,
                           "Unregistered tag presented mid-session",
                           display + [INCOMPLETE])

    def _handle_asset(self, uid, asset):
        if self.state != STATE_IDLE:
            return self._abort(
                uid, config.ROLE_ASSET,
                f"Asset {asset['asset_id']} presented while a session was open",
                [f"REJECTED - {asset['asset_id']} presented mid-session", INCOMPLETE],
            )

        self.asset_uid = uid
        self.asset_id = asset["asset_id"]
        self.custodian = db.get_current_custodian(
            self.asset_id, asset["initial_custodian"]
        )
        self.recipient = asset["intended_recipient"]
        if self.custodian == self.recipient:
            db.log_event(uid, config.ROLE_ASSET, EV_DELIVERED,
                         f"{self.asset_id} is already held by its manifested "
                         f"recipient; the manifest names no onward destination",
                         None, self.asset_id)
            delivered_id = self.asset_id
            self._reset()
            return [
                f"DELIVERED - {delivered_id} has reached its manifested recipient",
                "  The manifest names no onward destination.",
                LOCKED,
            ]

        self.transfer_id = db.open_transfer(
            self.asset_id, self.custodian, self.recipient
        )
        self.window_expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=config.TRANSFER_WINDOW_SECONDS)
        )
        self.state = STATE_AWAITING_RECEIVING

        db.log_event(uid, config.ROLE_ASSET, EV_ASSET_PRESENTED,
                     f"{asset['description']} presented; held by "
                     f"{_who(self.custodian)}, manifested to {_who(self.recipient)}",
                     self.transfer_id, self.asset_id)

        return [
            f"ASSET PRESENTED - {self.asset_id} (transfer #{self.transfer_id})",
            f"  {asset['description']}",
            f"  Current custodian: {_who(self.custodian)}",
            f"  Manifested to:     {_who(self.recipient)}",
            f"  Awaiting receiving party within {config.TRANSFER_WINDOW_SECONDS}s",
            INCOMPLETE,
        ]

    def _handle_credential(self, uid, credential):
        role = credential["role"]

        if credential["revoked"]:
            db.log_event(uid, role, EV_REVOKED,
                         f"Revoked credential presented: {_who(uid)}, "
                         f"revoked {credential['revoked_on']}",
                         self.transfer_id, self.asset_id)
            display = [
                f"REVOKED CREDENTIAL - {_who(uid)}",
                f"  Revoked on {credential['revoked_on']}",
            ]
            if self.state == STATE_IDLE:
                self._reset()
                return display + [LOCKED]
            if self.transfer_id is not None:
                db.close_transfer(self.transfer_id, "REJECTED")
            self._reset()
            return display + [INCOMPLETE,
                              "Session aborted. Present the asset again to retry."]

        if self.state == STATE_IDLE:
            db.log_event(uid, role, EV_REJECTED,
                         "Credential presented with no asset in session")
            return [
                f"REJECTED - no asset presented",
                "  Present the asset tag first to open a handoff session.",
                LOCKED,
            ]

        if self.state == STATE_AWAITING_RECEIVING:
            return self._verify_receiver(uid, credential, role)

        return self._verify_releaser(uid, credential, role)

    # -- stage checks ----------------------------------------------------

    def _verify_receiver(self, uid, credential, role):
        if role != config.ROLE_RECEIVING:
            return self._abort(
                uid, role,
                f"{role} credential presented where the manifested recipient "
                f"was expected",
                [f"REJECTED - {role} credential cannot receive custody", INCOMPLETE],
            )

        if uid != self.recipient:
            return self._abort(
                uid, role,
                f"{_who(uid)} is not the manifested recipient for {self.asset_id}",
                [
                    f"REJECTED - not the manifested recipient",
                    f"  Presented:     {_who(uid)}",
                    f"  Manifest says: {_who(self.recipient)}",
                    INCOMPLETE,
                ],
            )

        self.state = STATE_AWAITING_RELEASING
        db.log_event(uid, role, EV_RECEIVER_VERIFIED,
                     f"{_who(uid)} verified against manifest for {self.asset_id}",
                     self.transfer_id, self.asset_id)

        return [
            f"RECIPIENT VERIFIED - {_who(uid)}",
            f"  Matches manifest for {self.asset_id}",
            f"  Awaiting release by custodian {_who(self.custodian)}",
            INCOMPLETE,
        ]

    def _verify_releaser(self, uid, credential, role):
        if role != config.ROLE_RELEASING:
            return self._abort(
                uid, role,
                f"{role} credential presented where the custodian was expected",
                [f"REJECTED - {role} credential cannot release custody", INCOMPLETE],
            )

        if uid != self.custodian:
            return self._abort(
                uid, role,
                f"{_who(uid)} does not hold custody of {self.asset_id}",
                [
                    f"REJECTED - not the current custodian",
                    f"  Presented:      {_who(uid)}",
                    f"  Custody held by: {_who(self.custodian)}",
                    INCOMPLETE,
                ],
            )

        transfer_id = self.transfer_id
        asset_id = self.asset_id
        recipient = self.recipient

        db.log_event(uid, role, EV_COMPLETED,
                     f"{asset_id} released by {_who(uid)} to {_who(recipient)}",
                     transfer_id, asset_id)
        db.log_event(recipient, config.ROLE_RECEIVING, EV_CUSTODY_ASSIGNED,
                     f"Custody of {asset_id} now held by {_who(recipient)}",
                     transfer_id, asset_id)
        db.close_transfer(transfer_id, "COMPLETE",
                          receiving_card=recipient, releasing_card=uid)
        self._reset()

        return [
            f"HANDOFF COMPLETE - {asset_id} (transfer #{transfer_id})",
            f"  Released by: {_who(uid)}",
            f"  Custody now: {_who(recipient)}",
            ACTIVE,
        ]
