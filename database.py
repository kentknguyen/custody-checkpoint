"""
Schema, writes, and hash chaining for the custody log.

custody_events is an append-only evidence log. Each row's hash is computed
over its own fields plus the previous row's hash, so a modified row or a
deleted interior row breaks the chain (see verify.py).

transfers is a mutable summary derived from that log. It is deliberately not
hash-chained, because rows are updated as a transfer resolves.

Current custody is derived from custody_events, never from transfers - the
authoritative answer to "who holds this asset" comes from the tamper-evident
record, not the convenience summary.
"""

import sqlite3
import os
import hashlib
from datetime import datetime, timezone

# Resolved relative to this file so a fresh clone runs from any directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "logs", "custody.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Anchor for the first event in the chain.
GENESIS_HASH = "0" * 64

EV_CUSTODY_ASSIGNED = "CUSTODY_ASSIGNED"


def _connect():
    return sqlite3.connect(DB_PATH)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS custody_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            card_id TEXT NOT NULL,
            role TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            transfer_id INTEGER,
            asset_id TEXT,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT NOT NULL,
            expected_custodian TEXT NOT NULL,
            expected_recipient TEXT NOT NULL,
            receiving_card TEXT,
            releasing_card TEXT,
            started_at TEXT NOT NULL,
            resolved_at TEXT,
            result TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def compute_hash(prev_hash, timestamp, card_id, role,
                 event_type, message, transfer_id, asset_id):
    """SHA-256 over this event's fields, chained to the previous event."""
    payload = "|".join([
        prev_hash,
        timestamp,
        card_id,
        role,
        event_type,
        message,
        str(transfer_id),
        str(asset_id),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_last_hash():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT row_hash FROM custody_events ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else GENESIS_HASH


def log_event(card_id, role, event_type, message,
              transfer_id=None, asset_id=None):
    """Append one event. Read and write share a transaction so concurrent
    writers cannot both chain from the same prev_hash."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute("SELECT row_hash FROM custody_events ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        prev_hash = row[0] if row else GENESIS_HASH

        timestamp = _now()
        row_hash = compute_hash(prev_hash, timestamp, card_id, role,
                                event_type, message, transfer_id, asset_id)

        cursor.execute("""
            INSERT INTO custody_events
                (timestamp, card_id, role, event_type, message,
                 transfer_id, asset_id, prev_hash, row_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, card_id, role, event_type, message,
              transfer_id, asset_id, prev_hash, row_hash))

        conn.commit()
        return row_hash
    finally:
        conn.close()


def get_current_custodian(asset_id, declared_initial):
    """Who holds this asset now.

    Derived from the event log: the receiving party of the most recent
    completed transfer for this asset. If the asset has never been
    transferred, custody rests with the registry's declared initial holder.
    """
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT card_id FROM custody_events
        WHERE asset_id = ? AND event_type = ?
        ORDER BY id DESC LIMIT 1
    """, (asset_id, EV_CUSTODY_ASSIGNED))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else declared_initial


def open_transfer(asset_id, expected_custodian, expected_recipient):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO transfers
            (asset_id, expected_custodian, expected_recipient, started_at, result)
        VALUES (?, ?, ?, ?, 'PENDING')
    """, (asset_id, expected_custodian, expected_recipient, _now()))
    transfer_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return transfer_id


def close_transfer(transfer_id, result, receiving_card=None, releasing_card=None):
    """Resolve a pending transfer as COMPLETE, REJECTED, or EXPIRED."""
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE transfers
        SET receiving_card = ?, releasing_card = ?, resolved_at = ?, result = ?
        WHERE transfer_id = ?
    """, (receiving_card, releasing_card, _now(), result, transfer_id))
    conn.commit()
    conn.close()


def get_recent_events(limit=8):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, card_id, role, event_type, message, transfer_id, asset_id
        FROM custody_events
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows
