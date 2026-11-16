"""Durable per-target Discord delivery obligations.

The journal is deliberately independent of the high-water cursor: a newer
success must not erase an older unresolved delivery. Callers must register an
obligation before attempting delivery and resolve it only after a confirmed
delivery or a deliberate filter skip.
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from plugins.paths import data_path
from plugins.json_io import run_in_thread_to_completion


GAP_FILE = data_path("discord_gaps.sqlite3")
LEGACY_OWNER = "__legacy__"


def current_owner():
    from plugins.bot_profiles import CURRENT
    return CURRENT.get() or LEGACY_OWNER


async def register_async(channel, message_id, targets, message):
    snapshot = dict(message)
    if isinstance(snapshot.get("created_at"), datetime):
        snapshot["created_at"] = snapshot["created_at"].isoformat()
    snapshot["message_id"] = str(message_id)
    return await run_in_thread_to_completion(register, current_owner(), str(channel), str(message_id), list(targets), snapshot)


async def resolve_async(channel, message_id, targets):
    await run_in_thread_to_completion(resolve, current_owner(), str(channel), str(message_id), list(targets))


async def pending_targets_async(channel, message_id, targets):
    return await run_in_thread_to_completion(pending_targets, current_owner(), str(channel), str(message_id), list(targets))


async def processed_targets_async(channel, message_id, targets):
    return await run_in_thread_to_completion(processed_targets, current_owner(), str(channel), str(message_id), list(targets))


async def defer_async(channel, message_id, targets):
    await run_in_thread_to_completion(defer, current_owner(), str(channel), str(message_id), list(targets))


async def initialize_cursors_async(channel, baselines):
    await run_in_thread_to_completion(initialize_cursors, current_owner(), str(channel), baselines)


def _identity(value, label):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"invalid {label}")
    return value


def _message_id(value):
    value = str(value)
    if not value.isascii() or not value.isdecimal() or len(value) > 32:
        raise ValueError("invalid Discord message ID")
    return value


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_gaps (
            owner TEXT NOT NULL, channel TEXT NOT NULL, message_id TEXT NOT NULL,
            target TEXT NOT NULL, message TEXT NOT NULL,
            PRIMARY KEY (owner, channel, message_id, target)
        ) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_scan_cursor (
            owner TEXT NOT NULL, channel TEXT NOT NULL, target TEXT NOT NULL,
            message_id TEXT NOT NULL,
            PRIMARY KEY (owner, channel, target)
        ) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_processed (
            owner TEXT NOT NULL, channel TEXT NOT NULL, message_id TEXT NOT NULL,
            target TEXT NOT NULL,
            PRIMARY KEY (owner, channel, message_id, target)
        ) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_scan_run (
            owner TEXT NOT NULL, channel TEXT NOT NULL, target TEXT NOT NULL,
            base TEXT NOT NULL, before_id TEXT, high_id TEXT,
            PRIMARY KEY (owner, channel, target)
        ) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_scan_buffer (
            owner TEXT NOT NULL, channel TEXT NOT NULL, target TEXT NOT NULL,
            message_id TEXT NOT NULL, message TEXT NOT NULL,
            PRIMARY KEY (owner, channel, target, message_id)
        ) WITHOUT ROWID""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(discord_gaps)")}
        if "attempts" not in columns:
            db.execute("ALTER TABLE discord_gaps ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "next_attempt" not in columns:
            db.execute("ALTER TABLE discord_gaps ADD COLUMN next_attempt REAL NOT NULL DEFAULT 0")
        db.commit()
        return db
    except BaseException:
        db.close()
        raise


def register(owner, channel, message_id, targets, message, path=None):
    """Record only unfinished obligations and return their targets atomically."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    message_id = _message_id(message_id)
    targets = tuple(dict.fromkeys(_identity(target, "target") for target in targets))
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    payload = json.dumps(message, ensure_ascii=False, allow_nan=False)
    if not targets:
        return set()
    db = _connect(path or GAP_FILE)
    try:
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO discord_gaps (owner, channel, message_id, target, message) "
                "SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM discord_processed WHERE owner=? AND channel=? AND message_id=? AND target=?)",
                ((owner, channel, message_id, target, payload, owner, channel, message_id, target)
                 for target in targets),
            )
            processed = {
                row[0] for row in db.execute(
                    "SELECT target FROM discord_processed WHERE owner=? AND channel=? AND message_id=?",
                    (owner, channel, message_id),
                )
            }
            return set(targets) - processed
    finally:
        db.close()


def resolve(owner, channel, message_id, targets, path=None):
    """Remove only confirmed targets, leaving other targets and older gaps intact."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    message_id = _message_id(message_id)
    targets = tuple(dict.fromkeys(_identity(target, "target") for target in targets))
    if not targets:
        return
    db = _connect(path or GAP_FILE)
    try:
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO discord_processed VALUES (?, ?, ?, ?)",
                ((owner, channel, message_id, target) for target in targets),
            )
            db.executemany(
                "DELETE FROM discord_gaps WHERE owner=? AND channel=? AND message_id=? AND target=?",
                ((owner, channel, message_id, target) for target in targets),
            )
    finally:
        db.close()


def pending(owner, limit=100, path=None):
    """Read oldest obligations first; invalid payloads raise instead of disappearing."""
    owner = _identity(owner, "owner")
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    db = _connect(path or GAP_FILE)
    try:
        rows = db.execute(
            "SELECT channel, message_id, target, message FROM discord_gaps "
            "WHERE owner=? ORDER BY length(message_id), message_id, channel, target LIMIT ?",
            (owner, limit),
        ).fetchall()
    finally:
        db.close()
    result = []
    for channel, message_id, target, payload in rows:
        message = json.loads(payload)
        if not isinstance(message, dict):
            raise ValueError("invalid Discord gap payload")
        result.append((channel, message_id, target, message))
    return result


def pending_targets(owner, channel, message_id, targets, path=None):
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    message_id = _message_id(message_id)
    targets = set(_identity(target, "target") for target in targets)
    if not targets:
        return set()
    db = _connect(path or GAP_FILE)
    try:
        rows = db.execute("SELECT target FROM discord_gaps WHERE owner=? AND channel=? AND message_id=?",
                          (owner, channel, message_id)).fetchall()
        return targets.intersection(row[0] for row in rows)
    finally:
        db.close()


def processed_targets(owner, channel, message_id, targets, path=None):
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    message_id = _message_id(message_id)
    targets = set(_identity(target, "target") for target in targets)
    if not targets:
        return set()
    db = _connect(path or GAP_FILE)
    try:
        rows = db.execute(
            "SELECT target FROM discord_processed WHERE owner=? AND channel=? AND message_id=?",
            (owner, channel, message_id),
        ).fetchall()
        return targets.intersection(row[0] for row in rows)
    finally:
        db.close()


def defer(owner, channel, message_id, targets, path=None, now=None):
    """Back off each failing target independently without discarding its gap."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    message_id = _message_id(message_id)
    targets = set(_identity(target, "target") for target in targets)
    now = time.time() if now is None else float(now)
    if not targets:
        return
    db = _connect(path or GAP_FILE)
    try:
        with db:
            for target in targets:
                row = db.execute("SELECT attempts FROM discord_gaps WHERE owner=? AND channel=? AND message_id=? AND target=?",
                                 (owner, channel, message_id, target)).fetchone()
                if row is None:
                    continue
                attempts = min(max(0, int(row[0])) + 1, 5)
                delay = min(60 * (2 ** (attempts - 1)), 900)
                db.execute("UPDATE discord_gaps SET attempts=?, next_attempt=? WHERE owner=? AND channel=? AND message_id=? AND target=?",
                           (attempts, now + delay, owner, channel, message_id, target))
    finally:
        db.close()


def clear_through(owner, baselines, path=None):
    """Discard selected targets only through their confirmed clear baselines."""
    owner = _identity(owner, "owner")
    validated = []
    for channel, targets, latest in baselines:
        validated.append((_identity(str(channel), "channel"),
                          [_identity(target, "target") for target in targets],
                          _message_id(latest)))
    db = _connect(path or GAP_FILE)
    try:
        with db:
            for channel, targets, latest in validated:
                for target in targets:
                    db.execute("DELETE FROM discord_scan_run WHERE owner=? AND channel=? AND target=?",
                               (owner, channel, target))
                    db.execute("DELETE FROM discord_scan_buffer WHERE owner=? AND channel=? AND target=?",
                               (owner, channel, target))
                    rows = db.execute(
                        "SELECT message_id FROM discord_gaps WHERE owner=? AND channel=? AND target=?",
                        (owner, channel, target),
                    ).fetchall()
                    for (message_id,) in rows:
                        if int(_message_id(message_id)) <= int(latest):
                            db.execute("DELETE FROM discord_gaps WHERE owner=? AND channel=? AND target=? AND message_id=?",
                                       (owner, channel, target, message_id))
                    processed = db.execute(
                        "SELECT message_id FROM discord_processed WHERE owner=? AND channel=? AND target=?",
                        (owner, channel, target),
                    ).fetchall()
                    for (message_id,) in processed:
                        if int(_message_id(message_id)) <= int(latest):
                            db.execute("DELETE FROM discord_processed WHERE owner=? AND channel=? AND target=? AND message_id=?",
                                       (owner, channel, target, message_id))
                    row = db.execute("SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
                                     (owner, channel, target)).fetchone()
                    if row is None:
                        db.execute("INSERT INTO discord_scan_cursor VALUES (?, ?, ?, ?)", (owner, channel, target, latest))
                    elif int(_message_id(row[0])) < int(latest):
                        db.execute("UPDATE discord_scan_cursor SET message_id=? WHERE owner=? AND channel=? AND target=?",
                                   (latest, owner, channel, target))
    finally:
        db.close()


def pending_channel(owner, channel, targets, limit, path=None, now=None):
    """Read only enabled targets, so a paused group cannot block the queue."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    targets = set(_identity(target, "target") for target in targets)
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if not targets:
        return []
    now = time.time() if now is None else float(now)
    db = _connect(path or GAP_FILE)
    try:
        rows = []
        for target in targets:
            rows.extend(db.execute(
                "SELECT message_id, target, message FROM discord_gaps "
                "WHERE owner=? AND channel=? AND target=? AND next_attempt <= ? "
                "ORDER BY length(message_id), message_id LIMIT ?",
                (owner, channel, target, now, limit),
            ).fetchall())
        rows.sort(key=lambda row: (int(row[0]), row[1]))
        result = []
        for message_id, target, payload in rows[:limit]:
            message = json.loads(payload)
            if not isinstance(message, dict):
                raise ValueError("invalid Discord gap payload")
            result.append((message_id, target, message))
        return result
    finally:
        db.close()


def channel_status(owner, channel, targets, cap=1000, path=None):
    """Return distinct recorded messages and scan progress for enabled targets."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    targets = set(_identity(target, "target") for target in targets)
    if type(cap) is not int or cap <= 0:
        raise ValueError("cap must be positive")
    db = _connect(path or GAP_FILE)
    try:
        ids = set()
        cursors = {}
        running = False
        capped = False
        for target in targets:
            rows = db.execute("SELECT message_id FROM discord_gaps WHERE owner=? AND channel=? AND target=? LIMIT ?",
                              (owner, channel, target, cap + 1)).fetchall()
            if len(rows) > cap:
                capped = True
            ids.update(_message_id(row[0]) for row in rows[:cap])
            row = db.execute("SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
                             (owner, channel, target)).fetchone()
            cursors[target] = _message_id(row[0]) if row else None
            running = running or bool(db.execute(
                "SELECT 1 FROM discord_scan_run WHERE owner=? AND channel=? AND target=?",
                (owner, channel, target),
            ).fetchone())
        return {"count": min(cap, len(ids)), "capped": capped or len(ids) >= cap,
                "cursors": cursors, "scanning": running}
    finally:
        db.close()


class CursorChanged(RuntimeError):
    """Another scan advanced this target after the caller read its cursor."""


def start_scan(owner, channel, target, path=None):
    """Resume a saved descending scan, or start one at the current cursor."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    target = _identity(target, "target")
    db = _connect(path or GAP_FILE)
    try:
        with db:
            row = db.execute("SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
                             (owner, channel, target)).fetchone()
            if row is None:
                raise ValueError("scan cursor is not initialized")
            base = _message_id(row[0])
            db.execute("INSERT OR IGNORE INTO discord_scan_run VALUES (?, ?, ?, ?, NULL, NULL)",
                       (owner, channel, target, base))
            run = db.execute("SELECT base, before_id FROM discord_scan_run WHERE owner=? AND channel=? AND target=?",
                             (owner, channel, target)).fetchone()
            if run[0] != base:
                raise CursorChanged("Discord scan cursor changed during scan")
            return run
    finally:
        db.close()


def append_scan_page(owner, channel, target, base, before, messages, page_size=100, path=None):
    """Persist one descending page and finalize atomically after reaching base."""
    owner, channel, target = (_identity(owner, "owner"), _identity(channel, "channel"), _identity(target, "target"))
    base = _message_id(base)
    before = _message_id(before) if before is not None else None
    if type(page_size) is not int or not 1 <= page_size <= 100 or not isinstance(messages, list) or len(messages) > page_size:
        raise ValueError("invalid Discord scan page")
    prepared = []
    previous = int(before) if before is not None else None
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        message_id = _message_id(message.get("message_id"))
        if previous is not None and int(message_id) >= previous:
            raise ValueError("Discord scan page is not strictly descending")
        previous = int(message_id)
        prepared.append((message_id, json.dumps(message, ensure_ascii=False, allow_nan=False)))
    done = len(prepared) < page_size or (prepared and int(prepared[-1][0]) <= int(base))
    db = _connect(path or GAP_FILE)
    try:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute("SELECT base, before_id, high_id FROM discord_scan_run WHERE owner=? AND channel=? AND target=?",
                         (owner, channel, target)).fetchone()
        cursor = db.execute("SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
                            (owner, channel, target)).fetchone()
        if run is None or cursor is None or run[0] != base or run[1] != before or cursor[0] != base:
            raise CursorChanged("Discord scan position changed")
        high = run[2] or (prepared[0][0] if prepared and int(prepared[0][0]) > int(base) else base)
        db.executemany("INSERT OR IGNORE INTO discord_scan_buffer VALUES (?, ?, ?, ?, ?)",
                       ((owner, channel, target, mid, payload) for mid, payload in prepared if int(mid) > int(base)))
        if done:
            db.execute("""INSERT OR IGNORE INTO discord_gaps (owner, channel, message_id, target, message)
                SELECT b.owner, b.channel, b.message_id, b.target, b.message FROM discord_scan_buffer b
                WHERE b.owner=? AND b.channel=? AND b.target=? AND NOT EXISTS
                (SELECT 1 FROM discord_processed p WHERE p.owner=b.owner AND p.channel=b.channel
                 AND p.target=b.target AND p.message_id=b.message_id)""", (owner, channel, target))
            db.execute("UPDATE discord_scan_cursor SET message_id=? WHERE owner=? AND channel=? AND target=?",
                       (high, owner, channel, target))
            db.execute("""DELETE FROM discord_processed WHERE owner=? AND channel=? AND target=?
                       AND (length(message_id) < length(?) OR
                            (length(message_id) = length(?) AND message_id <= ?))""",
                       (owner, channel, target, high, high, high))
            db.execute("DELETE FROM discord_scan_buffer WHERE owner=? AND channel=? AND target=?", (owner, channel, target))
            db.execute("DELETE FROM discord_scan_run WHERE owner=? AND channel=? AND target=?", (owner, channel, target))
        else:
            db.execute("UPDATE discord_scan_run SET before_id=?, high_id=? WHERE owner=? AND channel=? AND target=?",
                       (prepared[-1][0], high, owner, channel, target))
        db.commit()
        return bool(done)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get_scan_cursor(owner, channel, target, path=None):
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    target = _identity(target, "target")
    db = _connect(path or GAP_FILE)
    try:
        row = db.execute(
            "SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
            (owner, channel, target),
        ).fetchone()
        return _message_id(row[0]) if row else None
    finally:
        db.close()


def initialize_cursor(owner, channel, target, message_id, path=None):
    """Set a first-use baseline without resetting an existing scanning position."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    target = _identity(target, "target")
    message_id = _message_id(message_id)
    db = _connect(path or GAP_FILE)
    try:
        with db:
            db.execute("INSERT OR IGNORE INTO discord_scan_cursor VALUES (?, ?, ?, ?)",
                       (owner, channel, target, message_id))
    finally:
        db.close()


def initialize_cursors(owner, channel, baselines, path=None):
    """Initialize all target scan positions together without moving existing ones."""
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    entries = [(_identity(target, "target"), _message_id(message_id))
               for target, message_id in baselines.items()]
    if not entries:
        return
    db = _connect(path or GAP_FILE)
    try:
        with db:
            db.executemany("INSERT OR IGNORE INTO discord_scan_cursor VALUES (?, ?, ?, ?)",
                           ((owner, channel, target, message_id) for target, message_id in entries))
    finally:
        db.close()


def stage_scan(owner, channel, target, expected_cursor, messages, path=None):
    """Atomically save an ordered, fully fetched batch and its scan position.

    The caller must establish that the batch contains every message between the
    expected cursor and its final item. A stale concurrent scan is rejected.
    An empty batch leaves the cursor unchanged.
    """
    owner = _identity(owner, "owner")
    channel = _identity(channel, "channel")
    target = _identity(target, "target")
    expected_cursor = _message_id(expected_cursor)
    prepared = []
    previous = int(expected_cursor)
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        message_id = _message_id(message.get("message_id"))
        if int(message_id) <= previous:
            raise ValueError("scan messages must be in strictly increasing order")
        previous = int(message_id)
        prepared.append((message_id, json.dumps(message, ensure_ascii=False, allow_nan=False)))

    db = _connect(path or GAP_FILE)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT message_id FROM discord_scan_cursor WHERE owner=? AND channel=? AND target=?",
            (owner, channel, target),
        ).fetchone()
        if row is None or row[0] != expected_cursor:
            raise CursorChanged("Discord scan cursor changed")
        db.executemany(
            "INSERT OR IGNORE INTO discord_gaps (owner, channel, message_id, target, message) "
            "SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM discord_processed WHERE owner=? AND channel=? AND message_id=? AND target=?)",
            ((owner, channel, message_id, target, payload, owner, channel, message_id, target)
             for message_id, payload in prepared),
        )
        if prepared:
            db.execute(
                "UPDATE discord_scan_cursor SET message_id=? WHERE owner=? AND channel=? AND target=?",
                (prepared[-1][0], owner, channel, target),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
