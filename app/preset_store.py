"""Small transactional NAS store for presets and prompt snippets; no credentials or uploaded filenames.

A snippet is named text shared by every device. In a prompt, `__name__` picks one of its lines per run
(a wildcard list); the phone does the expansion (static/mobile-tools.js `expandPrompt`)."""
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_PRESETS = 100
MAX_PRESET_BYTES = 64_000
MAX_SNIPPETS = 200
MAX_SNIPPET_TEXT = 4000
SNIPPET_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}')


class PresetStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.path = data_dir / 'presets.sqlite3'

    @property
    def enabled(self):
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    @contextmanager
    def connection(self):
        if not self.enabled or self.path.is_symlink():
            raise OSError('Presets need a writable DATA_DIR')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS presets (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS snippets (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, data TEXT NOT NULL)')
            with connection:
                yield connection
        finally:
            connection.close()

    def list(self):
        with self.connection() as db:
            return sorted((json.loads(row[0]) for row in db.execute('SELECT data FROM presets')),
                          key=lambda p: (p['name'].casefold(), p['id']))

    def get(self, preset_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM presets WHERE id = ?', (preset_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, name, job, choices, preset_id=None):
        preset = {'id': preset_id or str(uuid.uuid4()), 'name': name, 'job': job,
                  'choices': choices, 'updated': int(time.time())}
        data = json.dumps(preset, allow_nan=False)
        if len(data.encode()) > MAX_PRESET_BYTES:
            raise ValueError('Preset is too large')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT 1 FROM presets WHERE id = ?', (preset['id'],)).fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM presets').fetchone()[0] >= MAX_PRESETS:
                raise ValueError('Remove a preset before saving more (limit 100)')
            db.execute('INSERT OR REPLACE INTO presets (id, data) VALUES (?, ?)', (preset['id'], data))
        return preset

    def delete(self, preset_id):
        with self.connection() as db:
            return db.execute('DELETE FROM presets WHERE id = ?', (preset_id,)).rowcount > 0

    def snippets(self):
        with self.connection() as db:
            return sorted((json.loads(row[0]) for row in db.execute('SELECT data FROM snippets')),
                          key=lambda x: x['name'].casefold())

    def save_snippet(self, name, text):
        """Create a snippet, or replace the text of the one with this name (names are case-insensitive)."""
        if not SNIPPET_NAME.fullmatch(name):
            raise ValueError('Snippet names use letters, digits, - and _ (up to 40), starting with a letter or digit')
        text = text.strip()
        if not text or len(text) > MAX_SNIPPET_TEXT:
            raise ValueError(f'Snippet text must be 1 to {MAX_SNIPPET_TEXT} characters')
        key = name.casefold()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM snippets WHERE name = ?', (key,)).fetchone()
            if not row and db.execute('SELECT COUNT(*) FROM snippets').fetchone()[0] >= MAX_SNIPPETS:
                raise ValueError(f'Remove a snippet before saving more (limit {MAX_SNIPPETS})')
            snippet = {'id': row[0] if row else str(uuid.uuid4()), 'name': name, 'text': text, 'updated': int(time.time())}
            db.execute('INSERT OR REPLACE INTO snippets (id, name, data) VALUES (?, ?, ?)', (snippet['id'], key, json.dumps(snippet)))
        return snippet

    def delete_snippet(self, snippet_id):
        with self.connection() as db:
            return db.execute('DELETE FROM snippets WHERE id = ?', (snippet_id,)).rowcount > 0
