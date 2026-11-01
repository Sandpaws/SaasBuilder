"""SQLite storage. Note: there is deliberately no author/username column anywhere."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Iterable, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    community     TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'post',
    thread_id     TEXT NOT NULL,
    url           TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    created_utc   INTEGER NOT NULL,
    upvotes       INTEGER NOT NULL DEFAULT 0,
    num_comments  INTEGER NOT NULL DEFAULT 0,
    text_hash     TEXT NOT NULL UNIQUE,
    collected_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS extractions (
    post_id          TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    status           TEXT NOT NULL,            -- ok | refused | truncated | error
    is_pain          INTEGER NOT NULL DEFAULT 0,
    is_self_promo    INTEGER NOT NULL DEFAULT 0,
    problem          TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT 'other',
    persona          TEXT NOT NULL DEFAULT '',
    workaround       TEXT,
    urgency          INTEGER NOT NULL DEFAULT 1,
    budget_signal    TEXT NOT NULL DEFAULT 'none',
    budget_detail    TEXT,
    competitors      TEXT NOT NULL DEFAULT '[]',
    quote            TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT '',
    error            TEXT,
    extracted_at     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clusters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    label     TEXT NOT NULL,
    label_key TEXT NOT NULL,
    category  TEXT NOT NULL DEFAULT 'other',
    size      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_members (
    post_id    TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS solution_checks (
    label_key    TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    status       TEXT NOT NULL,                -- ok | no_results | error
    checked_at   INTEGER NOT NULL,
    saturation   INTEGER,
    confidence   TEXT,
    market_summary TEXT,
    gap_notes    TEXT,
    solutions    TEXT NOT NULL DEFAULT '[]',
    model        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id);
CREATE INDEX IF NOT EXISTS idx_members_cluster ON cluster_members(cluster_id);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def label_key(label: str) -> str:
    norm = re.sub(r"\s+", " ", label.strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- posts


def insert_posts(conn: sqlite3.Connection, posts: Iterable) -> tuple[int, int]:
    """Insert Post objects; duplicates (same id or same text) are ignored. Returns (inserted, skipped)."""
    now = int(time.time())
    inserted = skipped = 0
    for p in posts:
        cur = conn.execute(
            "INSERT OR IGNORE INTO posts (id, source, community, kind, thread_id, url, title, body, "
            "created_utc, upvotes, num_comments, text_hash, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p.id, p.source, p.community, p.kind, p.thread_id, p.url, p.title, p.body,
                int(p.created_utc), int(p.upvotes), int(p.num_comments), p.text_hash(), now,
            ),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped


def purge_posts(conn: sqlite3.Connection, ids: Sequence[str]) -> int:
    """Delete posts and everything derived from them (extractions, cluster membership), then physically erase the
    bytes (checkpoint the WAL and VACUUM), because deleted content must not be retained even in free pages."""
    removed = 0
    for start in range(0, len(ids), 500):
        chunk = list(ids[start : start + 500])
        marks = ",".join("?" for _ in chunk)
        removed += conn.execute(f"DELETE FROM posts WHERE id IN ({marks})", chunk).rowcount
    conn.execute("DELETE FROM clusters WHERE id NOT IN (SELECT cluster_id FROM cluster_members)")
    conn.execute("UPDATE clusters SET size = (SELECT COUNT(*) FROM cluster_members WHERE cluster_id = clusters.id)")
    conn.commit()
    if removed:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return removed


def pending_posts(
    conn: sqlite3.Connection, retry_statuses: Sequence[str] = (), limit: int | None = None
) -> list[sqlite3.Row]:
    """Posts with no extraction yet (plus any whose extraction status is in `retry_statuses`)."""
    marks = ",".join("?" for _ in retry_statuses)
    cond = "e.post_id IS NULL" + (f" OR e.status IN ({marks})" if retry_statuses else "")
    sql = f"SELECT p.* FROM posts p LEFT JOIN extractions e ON e.post_id = p.id WHERE {cond} ORDER BY p.created_utc DESC"
    params: list = list(retry_statuses)
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def save_extraction(
    conn: sqlite3.Connection, post_id: str, status: str, model: str, data: dict | None = None, error: str | None = None
) -> None:
    d = data or {}
    conn.execute(
        "INSERT OR REPLACE INTO extractions (post_id, status, is_pain, is_self_promo, problem, summary, category, "
        "persona, workaround, urgency, budget_signal, budget_detail, competitors, quote, model, error, extracted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            post_id,
            status,
            int(bool(d.get("is_pain_point"))),
            int(bool(d.get("is_self_promotion"))),
            (d.get("problem") or "").strip(),
            (d.get("summary") or "").strip(),
            d.get("category") or "other",
            (d.get("persona") or "").strip(),
            d.get("current_workaround"),
            max(1, min(5, int(d.get("urgency") or 1))),
            d.get("budget_signal") or "none",
            d.get("budget_detail"),
            json.dumps(d.get("competitors_named") or []),
            (d.get("evidence_quote") or "").strip(),
            model,
            error,
            int(time.time()),
        ),
    )


def pain_rows(conn: sqlite3.Connection) -> list[dict]:
    """One dict per usable pain-point mention (extraction joined with its post)."""
    rows = conn.execute(
        "SELECT e.*, p.source, p.community, p.kind, p.thread_id, p.url, p.title, p.created_utc, p.upvotes, p.num_comments "
        "FROM extractions e JOIN posts p ON p.id = e.post_id "
        "WHERE e.status = 'ok' AND e.is_pain = 1 AND e.is_self_promo = 0 AND e.problem != ''"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["competitors"] = json.loads(d.get("competitors") or "[]")
        out.append(d)
    return out


# ---------------------------------------------------------------- clusters


def replace_clusters(conn: sqlite3.Connection, clusters: list[dict]) -> None:
    """clusters: [{label, category, members: [post_id, ...]}]. Replaces all existing clusters."""
    conn.execute("DELETE FROM cluster_members")
    conn.execute("DELETE FROM clusters")
    for c in clusters:
        cur = conn.execute(
            "INSERT INTO clusters (label, label_key, category, size) VALUES (?,?,?,?)",
            (c["label"], label_key(c["label"]), c["category"], len(c["members"])),
        )
        cid = cur.lastrowid
        conn.executemany(
            "INSERT INTO cluster_members (post_id, cluster_id) VALUES (?,?)", [(m, cid) for m in c["members"]]
        )
    conn.commit()


def clusters_with_members(conn: sqlite3.Connection) -> list[dict]:
    """Each cluster with its member mention rows (see pain_rows) and its solution check, if any."""
    mentions = {r["post_id"]: r for r in pain_rows(conn)}
    checks = {r["label_key"]: dict(r) for r in conn.execute("SELECT * FROM solution_checks")}
    out = []
    for c in conn.execute("SELECT * FROM clusters ORDER BY id"):
        ids = [m["post_id"] for m in conn.execute("SELECT post_id FROM cluster_members WHERE cluster_id=?", (c["id"],))]
        members = [mentions[i] for i in ids if i in mentions]
        if not members:
            continue
        check = checks.get(c["label_key"])
        if check:
            check["solutions"] = json.loads(check.get("solutions") or "[]")
        out.append({"id": c["id"], "label": c["label"], "label_key": c["label_key"], "category": c["category"],
                    "members": members, "check": check})
    return out


def save_solution_check(
    conn: sqlite3.Connection, label: str, status: str, model: str, data: dict | None = None
) -> None:
    d = data or {}
    conn.execute(
        "INSERT OR REPLACE INTO solution_checks (label_key, label, status, checked_at, saturation, confidence, "
        "market_summary, gap_notes, solutions, model) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            label_key(label), label, status, int(time.time()), d.get("saturation"), d.get("confidence"),
            d.get("market_summary"), d.get("gap_notes"), json.dumps(d.get("solutions") or []), model,
        ),
    )
    conn.commit()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "posts": q("SELECT COUNT(*) FROM posts"),
        "extracted_ok": q("SELECT COUNT(*) FROM extractions WHERE status='ok'"),
        "extract_failed": q("SELECT COUNT(*) FROM extractions WHERE status!='ok'"),
        "pain_points": q("SELECT COUNT(*) FROM extractions WHERE status='ok' AND is_pain=1 AND is_self_promo=0"),
        "clusters": q("SELECT COUNT(*) FROM clusters"),
        "solution_checks": q("SELECT COUNT(*) FROM solution_checks WHERE status='ok'"),
    }
