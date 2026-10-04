"""Persistent cross-source delivery deduplication for automatic RSS items."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from plugins.json_io import run_in_thread_to_completion
from plugins.paths import data_path


DELIVERY_DEDUP_DB_FILE = data_path("delivery_dedup.sqlite3")
DELIVERY_DEDUP_TTL_SECONDS = 90 * 24 * 60 * 60
DELIVERY_DEDUP_MAX_RECORDS = 100_000

_async_lock = asyncio.Lock()
_volatile_lock = threading.Lock()
_volatile_deliveries = OrderedDict()


def _open_database():
    db_path = Path(DELIVERY_DEDUP_DB_FILE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivered_items (
            identity_key TEXT NOT NULL,
            group_id TEXT NOT NULL,
            delivered_at REAL NOT NULL,
            PRIMARY KEY (identity_key, group_id)
        )
        """
    )
    return connection


def _clean_expired(connection, now):
    connection.execute(
        "DELETE FROM delivered_items WHERE delivered_at < ?",
        (now - DELIVERY_DEDUP_TTL_SECONDS,),
    )
    count = connection.execute(
        "SELECT COUNT(*) FROM delivered_items"
    ).fetchone()[0]
    excess = count - DELIVERY_DEDUP_MAX_RECORDS
    if excess > 0:
        connection.execute(
            """
            DELETE FROM delivered_items
            WHERE rowid IN (
                SELECT rowid FROM delivered_items
                ORDER BY delivered_at ASC
                LIMIT ?
            )
            """,
            (excess,),
        )


def _read_delivered_groups(identity_key, groups):
    now = time.time()
    connection = _open_database()
    try:
        rows = connection.execute(
            "SELECT group_id FROM delivered_items "
            "WHERE identity_key = ? AND delivered_at >= ?",
            (identity_key, now - DELIVERY_DEDUP_TTL_SECONDS),
        ).fetchall()
        return {str(row[0]) for row in rows} & set(groups)
    finally:
        connection.close()


def _volatile_delivered_groups(identity_key, groups, now):
    cutoff = now - DELIVERY_DEDUP_TTL_SECONDS
    delivered = set()
    with _volatile_lock:
        for group in groups:
            key = (identity_key, group)
            delivered_at = _volatile_deliveries.get(key)
            if delivered_at is None:
                continue
            if delivered_at < cutoff:
                _volatile_deliveries.pop(key, None)
                continue
            delivered.add(group)
    return delivered


def _remember_volatile_deliveries(identity_key, groups):
    recorded_at = time.monotonic()
    cutoff = recorded_at - DELIVERY_DEDUP_TTL_SECONDS
    with _volatile_lock:
        for group in groups:
            key = (identity_key, group)
            _volatile_deliveries[key] = recorded_at
            _volatile_deliveries.move_to_end(key)

        while _volatile_deliveries:
            _key, timestamp = next(iter(_volatile_deliveries.items()))
            if timestamp >= cutoff:
                break
            _volatile_deliveries.popitem(last=False)
        while len(_volatile_deliveries) > DELIVERY_DEDUP_MAX_RECORDS:
            _volatile_deliveries.popitem(last=False)


async def delivered_groups(identity_key, groups):
    """Return groups already sent this platform item within the retention window."""
    identity_key = str(identity_key or "").strip()
    groups = list(dict.fromkeys(str(group).strip() for group in groups if group))
    if not identity_key or not groups:
        return set()
    async with _async_lock:
        now = time.monotonic()
        volatile = _volatile_delivered_groups(identity_key, groups, now)
        try:
            persisted = await run_in_thread_to_completion(
                _read_delivered_groups,
                identity_key,
                groups,
            )
        except Exception:
            # If SQLite is unreadable, suppress only when every requested group
            # is known to have succeeded in this process. Unknown groups must
            # keep failing closed so an unavailable ledger cannot cause sends.
            if volatile == set(groups):
                return volatile
            raise
        return persisted | volatile


def _record_delivered_groups(identity_key, groups, delivered_at):
    connection = _open_database()
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO delivered_items(identity_key, group_id, delivered_at)
                VALUES (?, ?, ?)
                ON CONFLICT(identity_key, group_id)
                DO UPDATE SET delivered_at = excluded.delivered_at
                """,
                [(identity_key, group, delivered_at) for group in groups],
            )
            _clean_expired(connection, delivered_at)
    finally:
        connection.close()


async def record_delivered_groups(identity_key, groups, delivered_at=None):
    """Persist confirmed deliveries per platform item and QQ group."""
    identity_key = str(identity_key or "").strip()
    groups = list(dict.fromkeys(str(group).strip() for group in groups if group))
    if not identity_key or not groups:
        return
    delivered_at = time.time() if delivered_at is None else float(delivered_at)
    async with _async_lock:
        try:
            await run_in_thread_to_completion(
                _record_delivered_groups,
                identity_key,
                groups,
                delivered_at,
            )
        except Exception:
            _remember_volatile_deliveries(identity_key, groups)
            raise
