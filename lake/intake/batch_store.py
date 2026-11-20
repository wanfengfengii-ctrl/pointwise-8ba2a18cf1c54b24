from dataclasses import dataclass
import sqlite3

from lake import DataIntegrityError, decode_integer, encode_integer
from lake.intake.identity import BatchIdentity
from lake.intake.receipts import BatchReceipt
from lake.intake.rows import RawRow


@dataclass(frozen=True)
class AcceptedBatch:
    identity: BatchIdentity
    rows: tuple[RawRow, ...]
    receipt: BatchReceipt


class BatchStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS batches (
                station TEXT NOT NULL,
                day TEXT NOT NULL,
                number TEXT NOT NULL,
                row_count INTEGER NOT NULL CHECK (row_count >= 0),
                receipt TEXT NOT NULL,
                PRIMARY KEY (station, day, number)
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS raw_rows (
                station TEXT NOT NULL,
                day TEXT NOT NULL,
                number TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                value_hex TEXT,
                PRIMARY KEY (station, day, number, position),
                FOREIGN KEY (station, day, number) REFERENCES batches
            )
        ''')

    def get(self, identity: BatchIdentity) -> AcceptedBatch | None:
        key = identity.key()
        header = self.db.execute('''
            SELECT row_count, receipt FROM batches
            WHERE station = ? AND day = ? AND number = ?
        ''', key).fetchone()
        if header is None:
            return None
        raw = self.db.execute('''
            SELECT position, value_hex FROM raw_rows
            WHERE station = ? AND day = ? AND number = ? ORDER BY position
        ''', key)
        rows = tuple(RawRow(pos, None if value is None else decode_integer(value))
                     for pos, value in raw)
        count, code = header
        if len(rows) != count or any(row.position != pos for pos, row in enumerate(rows, 1)):
            raise DataIntegrityError('accepted batch has incomplete positions')
        return AcceptedBatch(identity, rows, BatchReceipt(identity, count, code))

    def insert(self, batch: AcceptedBatch):
        key = batch.identity.key()
        self.db.execute('''
            INSERT INTO batches (station, day, number, row_count, receipt)
            VALUES (?, ?, ?, ?, ?)
        ''', (*key, batch.receipt.row_count, batch.receipt.code))
        self.db.executemany('''
            INSERT INTO raw_rows (station, day, number, position, value_hex)
            VALUES (?, ?, ?, ?, ?)
        ''', ((*key, row.position, None if row.value is None else encode_integer(row.value))
              for row in batch.rows))
