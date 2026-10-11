"""Transactional store supporting local SQLite and PostgreSQL deployments."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time


class Store:
    def __init__(self, url):
        self.url = str(url)
        self.postgres = self.url.startswith(('postgres://', 'postgresql://'))
        self.lock = threading.RLock()
        if not self.postgres:
            Path(self.url).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            self.execute(connection, 'CREATE TABLE IF NOT EXISTS objects (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(namespace,key))')
            self.execute(connection, 'CREATE TABLE IF NOT EXISTS journal (sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, created REAL NOT NULL)')

    def execute(self, connection, query, params=()):
        if self.postgres:
            query = query.replace('?', '%s')
        return connection.execute(query, params)

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.postgres:
                import psycopg
                connection = psycopg.connect(self.url)
                connection.execute('SELECT pg_advisory_xact_lock(729261011)')
            else:
                connection = sqlite3.connect(self.url, timeout=30)
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def get(self, namespace, key, default=None, connection=None):
        if connection is None:
            with self.transaction() as conn:
                return self.get(namespace, key, default, conn)
        row = self.execute(connection, 'SELECT value FROM objects WHERE namespace=? AND key=?', (namespace, str(key))).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, namespace, key, value, connection=None):
        if connection is None:
            with self.transaction() as conn:
                return self.put(namespace, key, value, conn)
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.execute(connection, 'INSERT INTO objects(namespace,key,value,updated) VALUES(?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated', (namespace, str(key), body, time.time()))

    def remove(self, namespace, key, connection=None):
        if connection is None:
            with self.transaction() as conn:
                return self.remove(namespace, key, conn)
        self.execute(connection, 'DELETE FROM objects WHERE namespace=? AND key=?', (namespace, str(key)))

    def list(self, namespace, connection=None):
        if connection is None:
            with self.transaction() as conn:
                return self.list(namespace, conn)
        rows = self.execute(connection, 'SELECT key,value FROM objects WHERE namespace=? ORDER BY updated DESC,key', (namespace,)).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def append(self, kind, value, connection=None):
        if connection is None:
            with self.transaction() as conn:
                return self.append(kind, value, conn)
        sequence = self.execute(connection, 'SELECT COALESCE(MAX(sequence),0)+1 FROM journal').fetchone()[0]
        self.execute(connection, 'INSERT INTO journal VALUES(?,?,?,?)', (sequence, kind, json.dumps(value, ensure_ascii=False), time.time()))
        return sequence

    def events(self, limit=200):
        with self.transaction() as conn:
            rows = self.execute(conn, 'SELECT sequence,kind,value,created FROM journal ORDER BY sequence DESC LIMIT ?', (limit,)).fetchall()
        return [{'sequence': seq, 'kind': kind, 'data': json.loads(body), 'created': at} for seq, kind, body, at in rows]

    def charge(self, key, limit, window=300):
        # Called at ingress before any share binding, comparison or replay lookup.
        now = time.time()
        with self.transaction() as conn:
            times = [at for at in self.get('ingress', key, [], conn) if at > now-window]
            if len(times) >= limit:
                return False
            times.append(now)
            self.put('ingress', key, times, conn)
            return True
