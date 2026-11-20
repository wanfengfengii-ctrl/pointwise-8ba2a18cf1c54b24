from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lake import open_lake
from lake.custody.cold_copy import archive_volume
from lake.custody.retirement import retire_volume
from lake.intake.batches import receive_batch
from lake.publication.daybooks import publish_day
from lake.quality.rules import register_ruleset


DAY = '2026-10-02'
RULE = 'daily-20'


class LakeCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'lake.sqlite3'
        self.db = open_lake(self.path)
        self.addCleanup(lambda: self.db.close())
        register_ruleset(self.db, RULE, 0, 20)

    def reopen(self):
        self.db.close()
        self.db = open_lake(self.path)

    def receive(self, station, number, values, *, day=DAY):
        return receive_batch(self.db, station, day, number, values)

    def daily_pair(self):
        return (self.receive('东堤', 'x', [0, 7, None]).identity,
                self.receive('南闸', 'y', [11, 25]).identity)


@contextmanager
def fail_book_commit(db):
    db.execute('CREATE TEMP TABLE required_seal (id INTEGER PRIMARY KEY)')
    db.execute('''
        CREATE TEMP TABLE pending_seal (
            id INTEGER REFERENCES required_seal(id) DEFERRABLE INITIALLY DEFERRED
        )
    ''')
    db.execute('''
        CREATE TEMP TRIGGER require_book_seal AFTER INSERT ON main.daybooks
        BEGIN INSERT INTO pending_seal VALUES (1); END
    ''')
    try:
        yield
    finally:
        db.execute('DROP TRIGGER require_book_seal')
        db.execute('DROP TABLE pending_seal')
        db.execute('DROP TABLE required_seal')


def make_cold_daybooks(db):
    """Build shared fixed inventory using the ordinary V1 public operations."""
    register_ruleset(db, 'night-30', 0, 30)
    x = receive_batch(db, '东堤', DAY, 'x', [7, 11]).identity
    y = receive_batch(db, '南闸', DAY, 'y', [5, None]).identity
    z = receive_batch(db, '西渠', DAY, 'z', [23]).identity
    alpha = publish_day(db, DAY, [x, y], 'night-30', '甲')
    beta = publish_day(db, DAY, [x], 'night-30', '乙')
    gamma = publish_day(db, DAY, [z], 'night-30', '丙')
    for entry in (*alpha.manifest.volumes, *gamma.manifest.volumes):
        volume_id = entry.volume.volume_id
        record = archive_volume(db, volume_id, '夜班冷柜')
        retire_volume(db, volume_id, record)
    return alpha, beta, gamma
