from lake.custody.cold_copy import archive_volume
from lake.custody.retirement import retire_volume
from lake.custody.storage import VolumeStorage
from lake.publication.daybooks import publish_day
from lake.reading.daybook import ReadUnavailable, read_day
from tests.support import DAY, RULE, LakeCase


class AuditTests(LakeCase):
    def test_partial_hot_coverage(self):
        x, y = self.daily_pair()
        book = publish_day(self.db, DAY, [x, y], RULE, 'night')
        first, second = (entry.volume.volume_id for entry in book.manifest.volumes)
        record = archive_volume(self.db, second, 'cabinet')
        retire_volume(self.db, second, record)
        self.reopen()
        self.assertIsNotNone(VolumeStorage(self.db).get_hot_bytes(first))
        unavailable = read_day(self.db, 'night')
        self.assertIsInstance(unavailable, ReadUnavailable)
        self.assertEqual(unavailable.number, 'night')
        self.assertEqual(unavailable.missing_volume_ids, (second,))
        self.assertEqual(unavailable.details, ())

    def test_missing_order_follows_fixed_manifest(self):
        x, y = self.daily_pair()
        book = publish_day(self.db, DAY, [y, x], RULE, 'reverse')
        first, second = (entry.volume.volume_id for entry in book.manifest.volumes)
        for volume_id in (second, first):
            record = archive_volume(self.db, volume_id, 'cabinet')
            retire_volume(self.db, volume_id, record)
        unavailable = read_day(self.db, 'reverse')
        self.assertEqual(unavailable.missing_volume_ids, (first, second))
        self.assertEqual(unavailable.details, ())

    def test_historical_receipt(self):
        first = self.receive('东堤', 'first', [7, 11]).identity
        old = publish_day(self.db, DAY, [first], RULE, 'original')
        late = self.receive('南闸', 'late', [19]).identity
        new = publish_day(self.db, DAY, [first, late], RULE, 'updated')
        self.reopen()
        original = read_day(self.db, 'original')
        updated = read_day(self.db, 'updated')
        self.assertEqual((original.receipt, updated.receipt), (old.receipt, new.receipt))
        self.assertEqual((original.totals.liters, updated.totals.liters), (18, 37))
        self.assertEqual([(d.value, d.origin.batch, d.origin.position) for d in original.details],
                         [(7, first, 1), (11, first, 2)])
        self.assertEqual([(d.value, d.origin.batch, d.origin.position) for d in updated.details],
                         [(7, first, 1), (11, first, 2), (19, late, 1)])
        with self.assertRaises(KeyError):
            read_day(self.db, 'unknown-account')

    def test_ordered_provenance_after_late_batch(self):
        y = self.receive('南闸', 'b', [7]).identity
        x = self.receive('东堤', 'a', [11, 7]).identity
        old = publish_day(self.db, DAY, [x, y], RULE, 'older')
        late = self.receive('西渠', 'c', [19]).identity
        publish_day(self.db, DAY, [late, y, x], RULE, 'newer')
        self.reopen()
        original = read_day(self.db, 'older')
        self.assertEqual(original.manifest, old.manifest)
        self.assertEqual([(row.value, *row.origin.batch.key(), row.origin.position)
                          for row in original.details],
                         [(11, '东堤', DAY, 'a', 1), (7, '东堤', DAY, 'a', 2),
                          (7, '南闸', DAY, 'b', 1)])
        self.assertEqual(original.totals.liters, 25)
        latest = read_day(self.db, 'newer')
        self.assertEqual([row.value for row in latest.details], [19, 7, 11, 7])
        self.assertEqual(latest.totals.liters, 44)
