"""Disposable message-header index. JSONL and member receipts remain authoritative.

Only delivery paths opt in to writes. Browsing remains read-only. Bodies are read
by byte offset from the canonical log and verified before use; no pickle, native
transport, background worker or new authority is introduced.
"""
import fcntl
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote

VERSION = 1
NAME = ".messages-cache.sqlite3"
MIN_BYTES = 128 * 1024
INVALID = ".messages-cache-invalid"
OWNER = ".messages-cache-owner"
OWNER_TEXT = b"agentic-hive header cache\n"
FIELDS = ("sequence", "id", "channel", "sender", "kind", "priority", "sender_authority",
          "created_at", "recipients", "task_id", "reply_to", "related_message_id", "resolution")
REQUIRED = set(FIELDS[:8]) | {"body"}


class CacheMismatch(ValueError):
    pass


def owned(root):
    marker = Path(root) / OWNER
    return (not marker.is_symlink() and marker.is_file() and
            marker.stat().st_size == len(OWNER_TEXT) and marker.read_bytes() == OWNER_TEXT)


def invalidate(root):
    try:
        fd = os.open(Path(root) / INVALID, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        pass  # The caller still retries directly from the canonical log.


def stamp(value):
    return [value.st_ino, value.st_size, value.st_mtime_ns]


class LogReader:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY)

    def __del__(self):
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    def payload(self, offset, length, digest):
        data = os.pread(self.fd, length, offset)
        if hashlib.sha256(data).hexdigest() != digest:
            raise CacheMismatch("message log changed while reading a body; retry from canonical history")
        return json.loads(data)


class Header(dict):
    def __init__(self, value, reader, offset, length, digest):
        super().__init__(value)
        self.reader, self.offset, self.length, self.digest = reader, offset, length, digest

    def __missing__(self, key):
        if key != "body":
            raise KeyError(key)
        record = self.reader.payload(self.offset, self.length, self.digest)
        if {key: value for key, value in record.items() if key != "body"} != dict(self):
            raise CacheMismatch("message index mismatch; retry from canonical history")
        self["body"] = record["body"]
        return record["body"]

    def get(self, key, default=None):
        return self[key] if key == "body" else super().get(key, default)


def connect(path, readonly=False):
    connection = sqlite3.connect("file:" + quote(str(path.absolute()), safe="/") + "?mode=ro" if readonly else str(path),
                                 uri=readonly, timeout=0.1)
    try:
        connection.execute("PRAGMA busy_timeout=100")
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def meta(connection):
    if connection.execute("PRAGMA user_version").fetchone()[0] != VERSION:
        return None
    value = connection.execute("SELECT value FROM metadata WHERE name='source'").fetchone()
    return json.loads(value[0]) if value else None


def read_headers(connection, reader, info, member=None, cursor=0):
    selected = ",".join(FIELDS) + ",byte_offset,byte_length,record_hash"
    query = "SELECT " + selected + " FROM messages"
    arguments = ()
    if member is not None:
        # Keep old obligations/results plus every unread candidate. Older,
        # irrelevant rows need only their ID/sequence for contiguous advancement.
        query += " WHERE sequence>? OR (kind='request' AND (sender=? OR instr(recipients,?)>0)) OR kind='result' OR resolution IS NOT NULL"
        arguments = (cursor, member, json.dumps(member))
    chosen = {}
    for values in connection.execute(query + " ORDER BY sequence", arguments):
        value = dict(zip(FIELDS, values[:len(FIELDS)]))
        if value["recipients"] is not None:
            value["recipients"] = json.loads(value["recipients"])
        value = {key: entry for key, entry in value.items() if entry is not None}
        chosen[value["sequence"]] = Header(value, reader, *values[len(FIELDS):])
    if member is None:
        result = list(chosen.values())
    else:
        result = [chosen.get(sequence) or {"sequence": sequence, "id": identity, "kind": "info", "_skipped": True}
                  for sequence, identity in connection.execute("SELECT sequence,id FROM messages ORDER BY sequence")]
    if len(result) != info["count"]:
        raise ValueError("message cache count mismatch")
    token = {"offset": info["offset"], "prefix_hash": info["prefix_hash"]}
    return result, token


def compatible(row):
    if not REQUIRED.issubset(row) or set(row) - (set(FIELDS) | {"body"}):
        return False
    return type(row["sequence"]) is int and all(
        isinstance(value, str) if key != "recipients" else
        isinstance(value, list) and all(isinstance(member, str) for member in value)
        for key, value in row.items() if key != "sequence"
    )


def packed(rows, data, offset=0):
    for row in rows:
        end = data.find(b"\n", offset) + 1
        if end <= offset or not compatible(row):
            raise ValueError("message schema requires canonical fallback")
        values = [row.get(key) for key in FIELDS]
        if row.get("recipients") is not None:
            values[FIELDS.index("recipients")] = json.dumps(row["recipients"])
        yield (*values, offset, end - offset, hashlib.sha256(data[offset:end]).hexdigest())
        offset = end


def refresh(connection, reader, parser):
    before = os.fstat(reader.fd)
    data = os.pread(reader.fd, before.st_size, 0)
    old = meta(connection)
    offset = old.get("offset", 0) if old else 0
    append = bool(old and len(data) >= offset and
                  hashlib.sha256(data[:offset]).hexdigest() == old.get("prefix_hash"))
    start = offset if append else 0
    rows, consumed = parser(data[start:], old["last_sequence"] if append else 0)
    if any(not compatible(row) for row in rows):
        raise ValueError("message schema requires canonical fallback")
    end = start + consumed
    after = os.fstat(reader.fd)
    prefix_hash = old["prefix_hash"] if append and end == offset else hashlib.sha256(data[:end]).hexdigest()
    info = {"stamp": stamp(before) if stamp(before) == stamp(after) else None,
            "offset": end, "prefix_hash": prefix_hash,
            "count": (old["count"] if append else 0) + len(rows),
            "last_sequence": rows[-1]["sequence"] if rows else old["last_sequence"] if append else 0}
    with connection:
        if not append:
            connection.execute("DELETE FROM messages")
        placeholders = ",".join("?" for _ in range(len(FIELDS) + 3))
        connection.executemany("INSERT INTO messages VALUES (" + placeholders + ")", packed(rows, data, start))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('source',?)", (json.dumps(info),))
    return info


def read(root, parser, member=None, cursor=0):
    """Return indexed headers and a verified-prefix token, or None on cache trouble.

    Corrupt canonical data is parsed again by the caller and must still fail.
    Cache failure never repairs, truncates or replaces the canonical log.
    """
    root = Path(root)
    source, path = root / "messages.jsonl", root / NAME
    if source.stat().st_size < MIN_BYTES:
        return None
    # Never adopt an unrelated file/database or follow a cache-path symlink.
    if any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
        return None
    try:
        ours = owned(root)
        if not ours and (path.exists() or (root / OWNER).exists() or (root / INVALID).exists()):
            return None
    except OSError:
        return None
    reader = LogReader(source)
    try:
        try:
            with closing(connect(path, True)) as connection:
                connection.execute("BEGIN")
                info = meta(connection)
                if info and not (root / INVALID).exists() and info["stamp"] == stamp(os.fstat(reader.fd)):
                    return read_headers(connection, reader, info, member, cursor)
        except (OSError, sqlite3.Error, ValueError, KeyError):
            pass
        # One builder; followers wait instead of all decoding the log at once.
        fd = os.open(root / ".messages-cache.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                     stat.S_IMODE(source.stat().st_mode) & 0o666)
        with os.fdopen(fd, "a") as lock:
            deadline = time.monotonic() + 1.5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return None
                    time.sleep(0.01)
            if not owned(root):
                if path.exists() or (root / OWNER).exists():
                    return None
                marker = os.open(root / OWNER, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(marker, "wb") as stream:
                    stream.write(OWNER_TEXT)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), stat.S_IMODE(source.stat().st_mode) & 0o444)
            if not path.exists():
                created = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(created)
                os.chmod(path, stat.S_IMODE(source.stat().st_mode) & 0o666)
            try:
                with closing(connect(path)) as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("PRAGMA synchronous=FULL")
                    if connection.execute("PRAGMA user_version").fetchone()[0] != VERSION:
                        connection.executescript("DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS metadata;"
                        "CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT);"
                        "CREATE TABLE messages(sequence INTEGER PRIMARY KEY,id TEXT,channel TEXT,sender TEXT,kind TEXT,"
                        "priority TEXT,sender_authority TEXT,created_at TEXT,recipients TEXT,task_id TEXT,reply_to TEXT,"
                        "related_message_id TEXT,resolution TEXT,byte_offset INTEGER,byte_length INTEGER,record_hash TEXT);"
                        "PRAGMA user_version=1;")
                    info = meta(connection)
                    if (root / INVALID).exists() or info and connection.execute("SELECT count(*) FROM messages").fetchone()[0] != info["count"]:
                        with connection:
                            connection.execute("DELETE FROM metadata")
                            connection.execute("DELETE FROM messages")
                        info = None
                    if not info or info["stamp"] != stamp(os.fstat(reader.fd)):
                        info = refresh(connection, reader, parser)
                    result = read_headers(connection, reader, info, member, cursor)
                    (root / INVALID).unlink(missing_ok=True)
                    return result
            except sqlite3.DatabaseError:
                # This path is a disposable index owned by this implementation.
                # Rebuild on a future call; never touch message/receipt files.
                try:
                    connection.close()
                except (UnboundLocalError, sqlite3.Error):
                    pass
                for suffix in ("", "-wal", "-shm"):
                    (Path(str(path) + suffix)).unlink(missing_ok=True)
                return None
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return None
