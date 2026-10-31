"""Bounded transactional review/cache storage, independent of the job database."""
from contextlib import contextmanager
import json
import os
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, root):
        self.path=Path(root)/'adviser.sqlite3'

    @contextmanager
    def connect(self):
        if not self.path.parent.is_dir():
            raise ValueError('Adviser needs a writable DATA_DIR')
        try:
            conn=sqlite3.connect(self.path,timeout=3)
            os.chmod(self.path,0o600)
            conn.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, expires REAL, body TEXT, PRIMARY KEY(kind,id))')
        except sqlite3.Error as exc:
            raise ValueError('Adviser storage is unavailable') from exc
        try:
            with conn: yield conn
        except sqlite3.Error as exc:
            raise ValueError('Adviser storage is unavailable') from exc
        finally:
            conn.close()

    def put(self,kind,key,value,ttl=600):
        body=json.dumps(value,allow_nan=False,separators=(',',':'))
        if len(body.encode())>512_000: raise ValueError('Review data exceeds limit')
        if kind == 'review' and 'expires' in value:
            ttl = min(ttl, value['expires']-time.time())
        with self.connect() as conn:
            conn.execute('DELETE FROM records WHERE expires < ?',(time.time(),))
            conn.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(kind,key,time.time()+ttl,body))
            conn.execute('DELETE FROM records WHERE kind=? AND id NOT IN (SELECT id FROM records WHERE kind=? ORDER BY expires DESC LIMIT ?)',(kind,kind,512 if kind=='cache' else 64))

    def get(self,kind,key,owner=None):
        with self.connect() as conn:
            row=conn.execute('SELECT body FROM records WHERE kind=? AND id=? AND expires>=?',(kind,key,time.time())).fetchone()
        value=json.loads(row[0]) if row else None
        return value if value and (owner is None or value.get('owner')==owner) else None

    def interrupt(self):
        with self.connect() as conn:
            rows=conn.execute("SELECT id,body FROM records WHERE kind='review'").fetchall()
            for key,body in rows:
                value=json.loads(body)
                if value.get('status') in ('queued','researching'):
                    value['status']='interrupted'
                    conn.execute("UPDATE records SET body=? WHERE kind='review' AND id=?",(json.dumps(value),key))
