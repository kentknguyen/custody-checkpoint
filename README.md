# Custody Checkpoint

A physical custody checkpoint for high-value assets. Two parties and the asset
itself must authenticate against a registry-held manifest before custody
transfers, and every attempt is written to a hash-chained log.


## Problem

On March 24, 2026, fifteen Ceres Air C31 agricultural spray drones with a value of approximately $870,000 total were stolen from CAC International, a logistics and shipping company in Harrison, NJ, using forged bill of lading documentation and a forged confirmation email. No break-in occurred, and the custody transfer was authorized and accepted entirely by these forged documents as a legitimate handoff. The drones were subsequently recovered at a Prudent Corporation warehouse in Dover, NJ on April 27, 2026.


## Demo Purpose

This demo is a checkpoint system where two parties (releaser/receiver) plus the asset must verify and authorize chain of custody with physical badges. The asset scan retrieves a shipping manifest, which is never presented with the shipment, and the receiver's credential is checked against the manifest before custody of the asset is transferable. Various scenarios of attempting the transfer are recorded with hash chaining to make tampering detectable. This demo is intended to provide a potential solution to validating transfer handoffs from releaser to receiver from the outset in addition to paperwork verification. With two-party plus asset verification, a bad actor would not only have to forge documents and emails, but they would also need to obtain or clone a receiver's physical credentials.


## Hardware

- Raspberry Pi 4 4GB Model B
- CanaKit Premium High-Gloss Raspberry Pi 4 Case with Integrated Fan Mount
- CanaKit Low Noise Bearing System Fan
- Heat sinks (placed on CPU, memory module, and USB controller)
- 3.5A USB-C Raspberry Pi 4 power supply
- 32GB Samsung EVO+ micro SD card
- Mifare RC522 RF IC card sensor module
- S50 blank card — asset tag
- S50 key fob — releasing credential (warehouse custodian)
- S50 key fob — receiving credential (authorized carrier)
- S50 key fob — unregistered, for demonstrating a rejected scan
- S50 key fob — revoked credential (optional)
- S50 key fob — second receiving credential, different organization (optional)
- WWZMDiB small breadboard with 170 tie points
- 10-pin male/female jumper wires


## Software

- Python 3.13.5
- mfrc522
- RPi.GPIO
- Raspberry Pi OS (64-bit), Debian 13 (trixie)
- Kernel 6.18.34+rpt-rpi-v8, aarch64
- sqlite3 CLI (optional, for reproducing the tamper demonstration by editing a logged row by hand)


## Setup

Assumes Raspberry Pi OS (64-bit) installed with SSH enabled.

```
# 1. Enable SPI (required for the RC522 reader)
sudo raspi-config      # Interface Options → SPI → Enable
sudo reboot

# 2. Clone the repository
git clone https://github.com/kentknguyen/custody-checkpoint.git
cd custody-checkpoint

# 3. Install dependencies
pip install -r requirements.txt --break-system-packages

# 4. Register your own tags. config.py ships with the author's test tags
#    already registered and working, so you'll be replacing those UIDs with
#    your own. Read each tag's UID one at a time:
python3 -c "from mfrc522 import SimpleMFRC522; print(SimpleMFRC522().read()[0])"

# 5. Edit config.py:
#    - In CREDENTIALS, replace the UID keys with your own, keeping at least
#      one RELEASING and one RECEIVING credential.
#    - In ASSETS, replace the asset tag UID key, then update initial_custodian
#      and intended_recipient to point at your new credential UIDs.
#
#    main.py runs config.validate() on startup and will refuse to start,
#    naming the problem, if the registries are inconsistent. For instance, if
#    an asset's intended_recipient isn't a credential you registered.
```


## Usage

```
python3 main.py        # run the checkpoint
python3 verify.py      # check custody log integrity
python3 test_scenarios.py   # run the scenario tests, no hardware required
```


## Files

```
config.py     — Two registries. CREDENTIALS maps credential UIDs to holder,
                organization, role (RELEASING or RECEIVING), and revocation
                status. ASSETS maps asset tag UIDs to an asset ID,
                description, initial custodian, and intended recipient. The
                intended recipient is the manifest. Also holds the transfer
                window and debounce settings.

database.py   — Schema, writes, and hashing. Creates the custody_events log
                and the transfers summary table. Every event is hashed
                together with the previous event's hash, forming the chain.

handoff.py    — Three-stage state machine. Decides what each scan is permitted
                to do given what has already happened: opening a transfer,
                completing it, rejecting it, or expiring it.

verify.py     — Chain verifier. Recomputes the log from the genesis anchor and
                reports the first event where the chain breaks, distinguishing
                a modified row from an interior deletion.

main.py       — Entry point. Reads the RC522, applies the debounce, passes each
                presentation to the state machine, and prints the result.

test_scenarios.py — Scenario tests for the custody state machine. Exercises
                every path through handoff.py against a temporary database and
                fixture registries. Requires no hardware and does not touch
                the real custody log.

logs/custody.db — SQLite database, created on first run. Not committed.
```


## Wiring — case fan

```
Fan wire    → Pi GPIO Pin (Physical)

Red (5V)    → Pin 4
Black (GND) → Pin 6
```

The fan is powered directly from the GPIO header and runs whenever the Pi has power. It is not under software control. Because it occupies Pin 6, the RC522 ground uses Pin 14, one of the header's other ground pins.


## Wiring — RC522 reader

```
RC522 Pin → Pi GPIO Pin (Physical)

SDA       → Pin 24
SCK       → Pin 23
MOSI      → Pin 19
MISO      → Pin 21
GND       → Pin 14
RST       → Pin 22
3.3V      → Pin 1
IRQ       → Unconnected
```


## Limitations

The S50 cards and key fobs used for this demo use the Crypto-1 cipher, which was broken in 2008. These cards and key fobs can be cloned with off the shelf parts or an Android NFC device. The demo credential layer is insecure as a deliberate design choice, and is NOT intended to be used for production deployment which would require DESFire EV3 or equivalent. The main purpose of this demo is merely to outline a viable chain of custody model to address the flaw that was exploited during the March 24, 2026 drone theft incident.

The transfers table is NOT tamper-evident by design and is only updated when handoff is complete, when a session is rejected, and when a timing window is closed, and is merely a summary derived from the event log itself.

Custody events logging is tamper-evident, but not tamper-proof since there is no secret in hashing. If a bad actor has write access to custody.db and a copy of database.py, they can edit rows and produce counterfeit hashing resulting in a verified chain. The system detects chain modification and interior deletion. Truncation is not detectable, because nothing in the log records its own length. Delete events from the end and every remaining link still validates, so the chain reports intact. Detecting it requires comparing the head hash against a value recorded somewhere the operator does not control.

"System active" is merely text. In this demo, there is no actual drone or drone operating software running; this would require additional layers for production, which is not part of this demo.

In this demo, the reader only verifies the credential, not the person holding it. A valid credential in the wrong hands still passes. Two-party verification mitigates this by requiring two credentials to be compromised rather than one, and the log is what makes reconstruction of a handoff possible afterward. In a production version at the reader, another layer of verification such as a PIN entry, biometrics, or photo capture would likely be necessary, but is NOT built into this demo.

Credential enrollment is out of scope for this demo. A system that issues credentials on the strength of forged paperwork inherits the same weakness one step earlier, and enrollment requires its own verification process.


## Design Decisions

Implementation was AI-assisted. Project ideation, scope, and design decisions are my own.

The scanning order of operations for this demo is asset → receiver → releaser. The purpose is to maintain both a zero trust protocol and final gatekeeper functionality for the releaser in this situation by validating the receiver first. If the asset is scanned first and the receiver is invalidated, then it is up to the releaser to follow next steps. Next steps could include contacting the manufacturer who created the manifest with a purchase order or the shipping and logistics company to determine who that receiver is, but the asset is never handed over to an invalid receiver. If the receiver has valid credentials plus any other required documentation and identification, then the releaser has everything they would need to then scan out the asset and complete the handoff.

Timestamps are generated in Python to use UTC ISO-8601 since the timestamp is part of what gets hashed before the entry row is written.

Roles are strings rather than booleans in order for the system to govern what the scan is allowed to do rather than what is known.

This demo is a single hop transfer. One custody transfer from releaser to receiver, and the manifest does not advance after delivery. Presenting an asset that has already reached its manifested recipient is rejected at the first scan, with the reason stated that the manifest names no onward destination. A multi-hop chain would require credentials to hold both roles and the manifest to advance with each hop, neither of which is implemented here.

A revoked credential is still in the registry, flagged revoked with a date; it was once valid and is now withdrawn. A credential that isn't in the registry at all, whether removed or never added, logs as unknown. Both are rejected identically at the reader and they mean different things afterward. Revoked implies a personnel event and preserves when it happened. Unknown implies a credential the system has never authorized.

CUSTODY_ASSIGNED versus TRANSFER_COMPLETED determines which party possesses the asset vs which party has released or received the asset. This distinction is critical since the releaser is the final scan in this demo. The transfer is a separate event from custody, because otherwise the system would read as TRANSFER_COMPLETED but custody would still be assigned to the releaser. By making transfers and custody separate events, the system authorizes handoff by ensuring custody is tied to the receiver once the final scan by the releaser event actually happens. Custody is derived from custody_events rather than the transfers table because transfers is mutable and unchained. If custody were read from the summary table, someone with database access could reassign an asset by editing a single row, and nothing would detect it. Reading from the hash-chained log means changing who holds an asset requires breaking the chain. The initial custodian is the releaser in the declared registry, so if an invalid receiver attempts to make a scan, the fallback of asset custody goes to the releaser and never moves until a valid receiver credential is presented.

Expiry is evaluated lazily to check the moment another scan occurs and not in real time. A window can therefore be past its deadline in wall-clock time and remain nominally open until someone scans again. The timestamp on a TRANSFER_EXPIRED event records when expiry was detected, not when the window closed. A second asset scan mid-session aborts instead of opening a new window or extending the current one, and is rejected and logged. Otherwise, the holder of an asset tag could keep a window open indefinitely.

A tag left resting on the reader is continuously in the RF field, so read() returns repeatedly. One physical presentation produces many reads. Any credential seen again within 3 seconds of last being in the field is therefore ignored entirely, with no event logged; the interval slides, so a card parked on the reader normally yields exactly one event. Transient read failures at the hardware level can interrupt that sequence, in which case the next successful read is treated as a new presentation. Without this, a receiving party holding their fob a beat too long would generate an event where a second receiving credential is read after the state has advanced. The system is expecting a releasing credential, but gets a receiving credential, which would abort the session, and writes a security event to the custody log that never actually occurred. The cost is that a legitimate re-presentation of the same credential within 3 seconds is silently dropped, and the log therefore records presentations as interpreted by the debounce rather than every RF-level read. DEBOUNCE_SECONDS is tunable; distinguishing "never left the field" from "removed and re-presented" would require presence polling via read_no_block(), which is out of scope here.


## Sources

- Recovery details and analysis of the document-fraud method —
  https://dronexl.co/2026/04/27/stolen-agricultural-drones-recovered-new-jersey/

- Theft mechanics: fraudulent bill of lading and confirmation email —
  https://www.hstoday.us/subject-matter-areas/unmanned-vehicles/15-chemical-spraying-drones-stolen-in-new-jersey-as-fbi-investigates-possible-weaponization-scenario/

- Local reporting on the recovery and the open investigation —
  https://nj1015.com/harrison-drone-theft-news/

- Courtois, Nohl & O'Neil, "Algebraic Attacks on the Crypto-1 Stream Cipher in MiFare Classic and Oyster Cards" (2008) —
  https://eprint.iacr.org/2008/166

Public reporting on this incident traces largely to a single originating outlet,
The High Side (subscription required), and to law-enforcement statements relayed
through media. No charges have been filed and the perpetrator remains
unidentified as of this writing.


## License

MIT — see [LICENSE](LICENSE).
