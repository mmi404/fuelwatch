"""Who may send write and control requests, what each may do, and the name the audit log records.

Two ways, set by the environment. With `ACCOUNTS` empty, everyone shares `OPERATOR_TOKEN`, may do everything and is
recorded as "operator". With `ACCOUNTS` set to `name:role:key,...`, each person has a key of their own and is recorded
by name, and the shared token stops working. Removing an entry and restarting the backend revokes that person.

Roles: an operator decides on shipments (approve or reject). A supervisor can also send while the safety gate is
holding shipments (override), change the autopilot and drive the simulator.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from typing import NamedTuple

OPERATOR, SUPERVISOR = "operator", "supervisor"
ROLES = (OPERATOR, SUPERVISOR)
SHARED_ACTOR = "operator"  # the name recorded for the shared token
# Names the audit log already gives a meaning to.
RESERVED = frozenset({SHARED_ACTOR, "autopilot", "system"})
MIN_KEY_LENGTH = 12
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,31}")


class Account(NamedTuple):
    name: str
    role: str
    key: str

    def __repr__(self) -> str:  # a key must not reach a log or a traceback
        return f"Account(name={self.name!r}, role={self.role!r})"


def parse_accounts(spec: str) -> dict[str, Account]:
    """`name:role:key,name:role:key` by name. A mistake raises, so the backend does not start with fewer people, or
    other rights, than meant. Messages name the entry by position or by name and never repeat a key.
    """
    out: dict[str, Account] = {}
    entries = [e.strip() for e in spec.split(",") if e.strip()]
    for position, entry in enumerate(entries, start=1):
        parts = [part.strip() for part in entry.split(":", 2)]
        if len(parts) != 3 or not _NAME.fullmatch(parts[0]):
            raise ValueError(f"ACCOUNTS entry {position} must be name:role:key, the name being a letter then up to 31 letters, digits, '_', '.' or '-'")
        name, role, key = parts[0], parts[1].lower(), parts[2]
        if name.lower() in RESERVED:
            raise ValueError(f"ACCOUNTS: the name {name!r} is reserved")
        if name.lower() in (n.lower() for n in out):
            raise ValueError(f"ACCOUNTS: the name {name!r} is listed twice")
        if role not in ROLES:
            raise ValueError(f"ACCOUNTS: the role for {name!r} must be {' or '.join(ROLES)}")
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(f"ACCOUNTS: the key for {name!r} must be at least {MIN_KEY_LENGTH} characters")
        if any(a.key == key for a in out.values()):
            raise ValueError(f"ACCOUNTS: the key for {name!r} is also another person's key")
        out[name] = Account(name, role, key)
    return out


def identify(given: str, accounts: Mapping[str, Account], shared_token: str) -> Account | None:
    """The account a presented key belongs to, or None. Personal accounts, when there are any, replace the shared token."""
    known = list(accounts.values()) or ([Account(SHARED_ACTOR, SUPERVISOR, shared_token)] if shared_token else [])
    found = None
    for account in known:  # every key is compared, so the time taken does not tell which one came close
        if hmac.compare_digest(given.encode(), account.key.encode()):
            found = account
    return found
