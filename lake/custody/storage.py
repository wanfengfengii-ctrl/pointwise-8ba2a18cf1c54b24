import sqlite3


class VolumeStorage:
    """Physical byte locations and inspection records in the caller's transaction."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS volumes (
                volume_id TEXT PRIMARY KEY NOT NULL,
                byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
                digest TEXT NOT NULL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS hot_bodies (
                volume_id TEXT PRIMARY KEY NOT NULL REFERENCES volumes(volume_id),
                body BLOB NOT NULL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS cold_copies (
                volume_id TEXT NOT NULL REFERENCES volumes(volume_id),
                target TEXT NOT NULL,
                body BLOB NOT NULL,
                PRIMARY KEY (volume_id, target)
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS inspections (
                volume_id TEXT NOT NULL REFERENCES volumes(volume_id),
                target TEXT NOT NULL,
                byte_length INTEGER NOT NULL,
                digest TEXT NOT NULL,
                PRIMARY KEY (volume_id, target)
            )
        ''')

    def get_volume(self, volume_id: str) -> tuple[str, int, str] | None:
        return self.db.execute('''
            SELECT volume_id, byte_length, digest FROM volumes WHERE volume_id = ?
        ''', (volume_id,)).fetchone()

    def insert_online(self, volume_id: str, byte_length: int, digest: str, body: bytes):
        self.db.execute('''
            INSERT INTO volumes (volume_id, byte_length, digest) VALUES (?, ?, ?)
        ''', (volume_id, byte_length, digest))
        self.db.execute('INSERT INTO hot_bodies (volume_id, body) VALUES (?, ?)',
                        (volume_id, body))

    def get_hot_bytes(self, volume_id: str) -> bytes | None:
        row = self.db.execute('SELECT body FROM hot_bodies WHERE volume_id = ?',
                              (volume_id,)).fetchone()
        return None if row is None else row[0]

    def put_cold_bytes(self, volume_id: str, target: str, body: bytes):
        self.db.execute('''
            INSERT INTO cold_copies (volume_id, target, body) VALUES (?, ?, ?)
            ON CONFLICT (volume_id, target) DO UPDATE SET body = excluded.body
        ''', (volume_id, target, body))

    def get_cold_bytes(self, volume_id: str, target: str) -> bytes | None:
        row = self.db.execute('''
            SELECT body FROM cold_copies WHERE volume_id = ? AND target = ?
        ''', (volume_id, target)).fetchone()
        return None if row is None else row[0]

    def add_inspection(self, volume_id: str, target: str, byte_length: int, digest: str):
        self.db.execute('''
            INSERT INTO inspections (volume_id, target, byte_length, digest) VALUES (?, ?, ?, ?)
            ON CONFLICT (volume_id, target) DO UPDATE SET
                byte_length = excluded.byte_length, digest = excluded.digest
        ''', (volume_id, target, byte_length, digest))

    def get_inspection(self, volume_id: str, target: str) -> tuple[str, str, int, str] | None:
        return self.db.execute('''
            SELECT volume_id, target, byte_length, digest FROM inspections
            WHERE volume_id = ? AND target = ?
        ''', (volume_id, target)).fetchone()

    def list_verified_targets(self, volume_id: str) -> tuple[str, ...]:
        """List recorded targets. The caller verifies the current bytes when needed."""
        return tuple(target for (target,) in self.db.execute('''
            SELECT target FROM inspections WHERE volume_id = ? ORDER BY target
        ''', (volume_id,)))

    def delete_hot(self, volume_id: str):
        self.db.execute('DELETE FROM hot_bodies WHERE volume_id = ?', (volume_id,))
