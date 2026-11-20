from lake import ConflictError
from lake.custody.storage import VolumeStorage
from lake.publication.body import decode_volume_body
from lake.reading.daybook import read_day
from tests.cold_fixture import ColdFeed, Finished, Fragment, WorkSlots
from tests.support import DAY, LakeCase, make_cold_daybooks


class FacilityTests(LakeCase):
    def test_physical_slot_and_external_events_survive_reopen(self):
        alpha, beta, gamma = make_cold_daybooks(self.db)
        x, y = (entry.volume.volume_id for entry in alpha.manifest.volumes)
        z = gamma.manifest.volumes[0].volume.volume_id
        self.assertEqual(beta.manifest.volumes[0].volume.volume_id, x)
        self.assertEqual(read_day(self.db, '甲').missing_volume_ids, (x, y))
        self.assertEqual(read_day(self.db, '乙').missing_volume_ids, (x,))

        feed = ColdFeed()
        slots = WorkSlots(self.db, 1)
        start = feed.start(self.db, x, '夜班冷柜', 'old-x', 0, fragment_size=8)
        original = VolumeStorage(self.db).get_cold_bytes(x, '夜班冷柜')
        events = feed.events('old-x')
        fragments = events[:-1]
        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(isinstance(piece, Fragment) for piece in fragments))
        self.assertEqual([piece.ordinal for piece in fragments], list(range(len(fragments))))
        self.assertEqual(events[-1], Finished('old-x', 0, len(fragments)))
        slots.append(fragments[0].slot, fragments[0].data)
        with self.assertRaises(IndexError):
            slots.append(1, b'overflow')

        self.reopen()
        with self.assertRaises(ConflictError):
            WorkSlots(self.db, 2)
        slots = WorkSlots(self.db, 1)
        self.assertEqual(slots.read(0), original[:8])
        self.assertEqual(feed.start(self.db, x, '夜班冷柜', 'old-x', 0, fragment_size=8), start)
        for piece in fragments[1:]:
            slots.append(piece.slot, piece.data)
        self.assertEqual(slots.read(0), original)
        decoded = decode_volume_body(slots.read(0))
        self.assertEqual([(row.value, *row.origin.batch.key(), row.origin.position) for row in decoded],
                         [(7, '东堤', DAY, 'x', 1), (11, '东堤', DAY, 'x', 2)])
        slots.clear(0)
        feed.start(self.db, z, '夜班冷柜', 'new-z', 0)
        self.assertEqual([issued.volume_id for issued in feed.starts], [x, z])
        self.assertEqual(feed.events('old-x'), events)
        self.assertEqual(slots.read(0), b'')
        self.assertEqual(read_day(self.db, '丙').missing_volume_ids, (z,))

    def test_slots_preserve_independent_binary_contents(self):
        slots = WorkSlots(self.db, 2)
        slots.append(0, b'\x00\xff')
        slots.append(1, b'second')
        slots.append(0, b'\xfe\x00')
        self.reopen()
        slots = WorkSlots(self.db, 2)
        self.assertEqual(slots.read(0), b'\x00\xff\xfe\x00')
        self.assertEqual(slots.read(1), b'second')
        slots.clear(0)
        self.assertEqual((slots.read(0), slots.read(1)), (b'', b'second'))
