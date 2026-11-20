import sqlite3

from lake import DataIntegrityError
from lake.custody.cold_copy import archive_volume
from lake.custody.inspection import InspectionRecord
from lake.custody.retirement import retire_volume
from lake.custody.storage import VolumeStorage
from lake.publication.daybook_store import DaybookStore
from lake.publication.daybooks import publish_day
from lake.reading.daybook import read_day
from tests.support import DAY, RULE, LakeCase


class ArchiveTests(LakeCase):
    def single_volume(self):
        source = self.receive('东堤', 'x', [7, 11]).identity
        book = publish_day(self.db, DAY, [source], RULE, 'fixed')
        return book, book.manifest.volumes[0].volume

    def test_bad_cold_copy(self):
        _, ref = self.single_volume()
        storage = VolumeStorage(self.db)
        before = storage.get_hot_bytes(ref.volume_id)
        good = archive_volume(self.db, ref.volume_id, 'good')
        self.db.execute('''
            CREATE TEMP TRIGGER spoil_bad_copy AFTER INSERT ON main.cold_copies
            WHEN NEW.target = 'bad'
            BEGIN
                UPDATE cold_copies SET body = zeroblob(length(NEW.body))
                WHERE volume_id = NEW.volume_id AND target = NEW.target;
            END
        ''')
        with self.assertRaisesRegex(DataIntegrityError, 'cold target has missing or mismatched bytes'):
            archive_volume(self.db, ref.volume_id, 'bad')
        self.assertIsNone(storage.get_inspection(ref.volume_id, 'bad'))
        self.assertIsNone(storage.get_cold_bytes(ref.volume_id, 'bad'))
        self.assertEqual(storage.get_cold_bytes(ref.volume_id, 'good'), before)
        self.assertEqual(storage.get_inspection(ref.volume_id, 'good'),
                         (good.volume_id, good.target, good.byte_length, good.digest))
        unrecorded = InspectionRecord(ref.volume_id, 'bad', ref.byte_length, ref.digest)
        with self.assertRaises(DataIntegrityError):
            retire_volume(self.db, ref.volume_id, unrecorded)
        self.assertEqual(storage.get_hot_bytes(ref.volume_id), before)
        self.assertEqual([row.value for row in read_day(self.db, 'fixed').details], [7, 11])

    def test_cold_write_failure(self):
        _, ref = self.single_volume()
        self.db.execute('''
            CREATE TEMP TRIGGER reject_copy BEFORE INSERT ON main.cold_copies
            BEGIN SELECT RAISE(ABORT, 'cold device write failed'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'cold device write failed'):
            archive_volume(self.db, ref.volume_id, 'unwritable')
        storage = VolumeStorage(self.db)
        self.assertIsNone(storage.get_cold_bytes(ref.volume_id, 'unwritable'))
        self.assertIsNone(storage.get_inspection(ref.volume_id, 'unwritable'))
        self.assertEqual(read_day(self.db, 'fixed').totals.liters, 18)

    def test_verified_retirement(self):
        book, ref = self.single_volume()
        previous = VolumeStorage(self.db).get_hot_bytes(ref.volume_id)
        record = archive_volume(self.db, ref.volume_id, 'cabinet')
        self.assertEqual((record.volume_id, record.byte_length, record.digest),
                         (ref.volume_id, ref.byte_length, ref.digest))
        self.assertEqual(retire_volume(self.db, ref.volume_id, record), ref)
        self.reopen()
        storage = VolumeStorage(self.db)
        self.assertEqual(storage.get_cold_bytes(ref.volume_id, 'cabinet'), previous)
        self.assertEqual(storage.list_verified_targets(ref.volume_id), ('cabinet',))
        self.assertEqual(storage.get_inspection(ref.volume_id, 'cabinet'),
                         (ref.volume_id, 'cabinet', ref.byte_length, ref.digest))
        self.assertIsNone(storage.get_hot_bytes(ref.volume_id))
        self.assertEqual(DaybookStore(self.db).get('fixed'), book)
        self.assertEqual(read_day(self.db, 'fixed').missing_volume_ids, (ref.volume_id,))
        self.assertEqual(retire_volume(self.db, ref.volume_id, record), ref)

    def test_target_removed_after_inspection(self):
        _, ref = self.single_volume()
        record = archive_volume(self.db, ref.volume_id, 'cabinet')
        self.db.execute('DELETE FROM cold_copies WHERE volume_id = ? AND target = ?',
                        (ref.volume_id, record.target))
        self.reopen()
        storage = VolumeStorage(self.db)
        self.assertIsNotNone(storage.get_inspection(ref.volume_id, record.target))
        with self.assertRaises(DataIntegrityError):
            retire_volume(self.db, ref.volume_id, record)
        self.assertEqual([row.value for row in read_day(self.db, 'fixed').details], [7, 11])

    def test_retirement_record_must_match_current_object(self):
        _, ref = self.single_volume()
        record = archive_volume(self.db, ref.volume_id, 'cabinet')
        forged_target = InspectionRecord(ref.volume_id, 'other', ref.byte_length, ref.digest)
        with self.assertRaises(DataIntegrityError):
            retire_volume(self.db, ref.volume_id, forged_target)
        storage = VolumeStorage(self.db)
        storage.put_cold_bytes(ref.volume_id, 'cabinet', bytes(ref.byte_length))
        with self.assertRaises(DataIntegrityError):
            retire_volume(self.db, ref.volume_id, record)
        self.assertEqual(read_day(self.db, 'fixed').totals.liters, 18)

    def test_reuse_cold_volume_preserves_locations(self):
        book, ref = self.single_volume()
        record = archive_volume(self.db, ref.volume_id, 'cabinet')
        retire_volume(self.db, ref.volume_id, record)
        copy = publish_day(self.db, DAY, book.manifest.batches, RULE, 'second-account')
        self.assertEqual(copy.manifest, book.manifest)
        self.assertNotEqual(copy.receipt, book.receipt)
        storage = VolumeStorage(self.db)
        self.assertIsNone(storage.get_hot_bytes(ref.volume_id))
        self.assertEqual(storage.list_verified_targets(ref.volume_id), ('cabinet',))
        with self.assertRaises(DataIntegrityError):
            archive_volume(self.db, ref.volume_id, 'extra')
        storage.put_cold_bytes(ref.volume_id, 'cabinet', b'lost')
        with self.assertRaises(DataIntegrityError):
            publish_day(self.db, DAY, book.manifest.batches, RULE, 'missing-source')
        self.assertIsNone(DaybookStore(self.db).get('missing-source'))
        self.assertEqual(publish_day(self.db, DAY, book.manifest.batches, RULE, 'fixed'), book)
