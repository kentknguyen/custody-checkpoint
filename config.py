"""
Registries and demo configuration.

Two separate registries, deliberately not merged:

  CREDENTIALS - people. Who they are and what role they may play.
  ASSETS      - items. What is being transferred, who holds it at genesis,
                and who it is manifested to.

Keeping them separate is what lets the checkpoint reject a credential
presented where an asset is expected, and vice versa.

The UIDs below are the author's own test tags. Replace them with your own -
see the Setup section of the README.

SECURITY NOTE: These are Mifare Classic 1K (S50) tags. The Crypto-1 cipher
was broken in 2008 and the UID is transmitted in the clear before
authentication, so all tags here are trivially cloneable. They demonstrate
the custody state model, which is tag-agnostic. Production requires
DESFire EV3 or equivalent. See README.
"""

# Roles
ROLE_RELEASING = "RELEASING"   # may release an asset they currently hold
ROLE_RECEIVING = "RECEIVING"   # may accept custody of an asset
ROLE_UNKNOWN = "UNKNOWN"       # used for logging unregistered tags
ROLE_ASSET = "ASSET"           # used for logging asset tag scans

# --- People -----------------------------------------------------------

CREDENTIALS = {
    "34271814237": {
        "name": "M. Reyes",
        "organization": "Harrison Distribution Center",
        "role": ROLE_RELEASING,
        "revoked": False,
        "revoked_on": None,
        "label": "Blue fob",
    },
    "698893498670": {
        "name": "J. Okafor",
        "organization": "Northline Freight",
        "role": ROLE_RECEIVING,
        "revoked": False,
        "revoked_on": None,
        "label": "Green fob",
    },
    "698489731393": {
        "name": "D. Serrano",
        "organization": "Harrison Distribution Center (former)",
        "role": ROLE_RELEASING,
        "revoked": True,
        "revoked_on": "2026-06-30",
        "label": "Yellow fob",
    },
    "695958334362": {
        "name": "R. Vance",
        "organization": "Cross Ridge Carriers",
        "role": ROLE_RECEIVING,
        "revoked": False,
        "revoked_on": None,
        "label": "Black fob",
    },
}

# --- Items ------------------------------------------------------------
#
# intended_recipient is the manifest. It lives here, in the registry - it is
# never presented with the shipment. A manifest that arrives with the goods
# is just a bill of lading, and bills of lading forge.

ASSETS = {
    "979094549427": {
        "asset_id": "CERES-C31-0001",
        "description": "Ceres Air C31 spray drone",
        "initial_custodian": "34271814237",
        "intended_recipient": "698893498670",
        "label": "White card",
    },
}

# --- Tunables ---------------------------------------------------------

# Seconds a handoff session stays open after an asset is presented.
TRANSFER_WINDOW_SECONDS = 60

# A tag left in the reader's field produces repeated reads. An identical UID
# seen again within this many seconds is not treated as a new presentation.
DEBOUNCE_SECONDS = 3

# Print the recent event log after each scan.
DISPLAY_EVENT_LOG = True


def validate():
    """Check the registries for configuration errors. Returns a list of
    problem descriptions; empty means the config is coherent."""
    problems = []

    overlap = set(CREDENTIALS) & set(ASSETS)
    if overlap:
        problems.append(
            f"UID(s) registered as both credential and asset: {sorted(overlap)}"
        )

    cred_fields = ("name", "organization", "role", "revoked", "revoked_on", "label")
    for uid, cred in CREDENTIALS.items():
        missing = [k for k in cred_fields if k not in cred]
        if missing:
            problems.append(
                f"credential {uid} is missing: {', '.join(missing)}"
            )
        elif cred["role"] not in (ROLE_RELEASING, ROLE_RECEIVING):
            problems.append(
                f"credential {uid} has unrecognised role {cred['role']}"
            )

    asset_fields = ("asset_id", "description", "initial_custodian",
                    "intended_recipient", "label")
    for uid, asset in ASSETS.items():
        missing = [k for k in asset_fields if k not in asset]
        if missing:
            problems.append(
                f"asset {uid} is missing: {', '.join(missing)}"
            )

    # Malformed entries would break the relational checks below.
    if problems:
        return problems

    for uid, asset in ASSETS.items():
        custodian = asset["initial_custodian"]
        recipient = asset["intended_recipient"]
        aid = asset["asset_id"]

        if custodian not in CREDENTIALS:
            problems.append(
                f"{aid}: initial_custodian {custodian} is not in CREDENTIALS"
            )
        elif CREDENTIALS[custodian]["role"] != ROLE_RELEASING:
            problems.append(
                f"{aid}: initial_custodian {custodian} does not hold the "
                f"{ROLE_RELEASING} role"
            )

        if recipient not in CREDENTIALS:
            problems.append(
                f"{aid}: intended_recipient {recipient} is not in CREDENTIALS"
            )
        elif CREDENTIALS[recipient]["role"] != ROLE_RECEIVING:
            problems.append(
                f"{aid}: intended_recipient {recipient} does not hold the "
                f"{ROLE_RECEIVING} role"
            )

        if custodian == recipient:
            problems.append(
                f"{aid}: initial_custodian and intended_recipient are the "
                f"same credential; a transfer could never complete"
            )

    return problems
