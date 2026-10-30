"""Small transactional NAS preset store; no credentials or uploaded filenames."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_PRESETS = 100
MAX_PRESET_BYTES = 64_000


class PresetStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.path = data_dir / 'presets.sqlite3'

    @property
    def enabled(self):
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    @contextmanager
    def connection(self):
        if not self.enabled:
            raise OSError('Presets need a writable DATA_DIR')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS presets (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
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
