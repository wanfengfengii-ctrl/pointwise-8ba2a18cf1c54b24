import sqlite3

from lake import ConflictError, initialize
from lake.intake.batch_store import BatchStore
from lake.intake.identity import make_batch_identity
from lake.publication.daybook_store import DaybookStore
from lake.publication.daybooks import publish_day
from lake.quality.classify import Classification
from lake.quality.day_report import preview_day
from lake.quality.rules import get_ruleset, register_ruleset
from lake.reading.daybook import read_day
from tests.support import DAY, RULE, LakeCase, fail_book_commit


class DailyTests(LakeCase):
    def test_station_totals(self):
        x, y = self.daily_pair()
        report = preview_day(self.db, DAY, [x, y], RULE)
        self.assertEqual([(s.station, s.liters, s.missing, s.out_of_range)
                          for s in report.totals.stations],
                         [('东堤', 7, 1, 0), ('南闸', 11, 0, 1)])
        self.assertEqual((report.totals.liters, report.totals.missing,
                          report.totals.out_of_range), (18, 1, 1))
        self.assertEqual([line.classification for batch in report.batches for line in batch.rows],
                         [Classification.INCLUDED, Classification.INCLUDED,
                          Classification.MISSING, Classification.INCLUDED,
                          Classification.OUT_OF_RANGE])
        book = publish_day(self.db, DAY, [x, y], RULE, 'handover')
        result = read_day(self.db, 'handover')
        self.assertEqual(book.receipt.row_count, 5)
        self.assertEqual(result.receipt, book.receipt)
        self.assertEqual([line.value for line in result.details], [0, 7, None, 11, 25])
        self.assertEqual([(line.origin.batch, line.origin.position) for line in result.details],
                         [(x, 1), (x, 2), (x, 3), (y, 1), (y, 2)])
        self.assertEqual((result.totals.liters, result.totals.missing,
                          result.totals.out_of_range), (18, 1, 1))

    def test_equal_values_at_distinct_positions(self):
        receipt = self.receive('东堤', 'repeated', [7, 7])
        book = publish_day(self.db, DAY, [receipt.identity, receipt.identity], RULE, 'same-values')
        result = read_day(self.db, 'same-values')
        self.assertEqual(book.manifest.batches, (receipt.identity,))
        self.assertEqual(book.manifest.volumes[0].row_count, 2)
        self.assertEqual(result.totals.liters, 14)
        self.assertEqual([(line.value, line.origin.position) for line in result.details],
                         [(7, 1), (7, 2)])

    def test_invalid_row_rejects_entire_batch(self):
        key = make_batch_identity('东堤', DAY, 'recoverable')
        for invalid in (True, -1, 2.5, '7'):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    self.receive(key.station, key.number, [7, invalid, 11])
                self.assertIsNone(BatchStore(self.db).get(key))
                self.assertEqual(self.db.execute('SELECT COUNT(*) FROM raw_rows').fetchone(), (0,))
        receipt = self.receive(key.station, key.number, [7, None, 11])
        self.assertEqual(receipt.row_count, 3)
        self.assertEqual(preview_day(self.db, DAY, [key], RULE).totals.liters, 18)

    def test_duplicate_and_conflicting_batch(self):
        first = self.receive('东堤', 'x', [7, 11])
        self.reopen()
        self.assertEqual(self.receive('东堤', 'x', [7, 11]), first)
        with self.assertRaises(ConflictError):
            self.receive('东堤', 'x', [9, 11])
        report = preview_day(self.db, DAY, [first.identity], RULE)
        self.assertEqual(report.totals.liters, 18)
        self.assertEqual([line.row.value for line in report.batches[0].rows], [7, 11])

    def test_reordered_rows_are_a_conflict(self):
        first = self.receive('东堤', 'x', [11, 7])
        self.assertEqual(self.receive('东堤', 'x', [11, 7]), first)
        with self.assertRaises(ConflictError):
            self.receive('东堤', 'x', [7, 11])
        book = publish_day(self.db, DAY, [first.identity], RULE, 'original')
        self.assertEqual(book.totals.liters, 18)
        self.assertEqual([row.value for row in read_day(self.db, 'original').details], [11, 7])

    def test_explicit_rule_revision(self):
        x, y = self.daily_pair()
        register_ruleset(self.db, 'tight', 0, 10)
        wide = preview_day(self.db, DAY, [x, y], RULE)
        tight = preview_day(self.db, DAY, [x, y], 'tight')
        self.assertEqual((wide.totals.liters, tight.totals.liters), (18, 7))
        self.assertEqual(wide.batches[1].rows[0].classification, Classification.INCLUDED)
        self.assertEqual(tight.batches[1].rows[0].classification, Classification.OUT_OF_RANGE)
        book = publish_day(self.db, DAY, [x, y], RULE, 'wide')
        self.assertEqual((book.manifest.revision, book.totals.liters), (RULE, 18))
        self.assertEqual(read_day(self.db, 'wide').details[3].classification, Classification.INCLUDED)

    def test_invalid_binding_rejects_preview_and_publication(self):
        unknown = make_batch_identity('东堤', DAY, 'unknown')
        other_day = self.receive('东堤', 'x', [7], day='2026-10-03').identity
        cases = [(DAY, [unknown], RULE, KeyError), (DAY, [other_day], RULE, ValueError),
                 (DAY, [], 'unknown', KeyError), ('20261002', [], RULE, ValueError)]
        for day, batches, revision, error in cases:
            with self.subTest(day=day, batches=batches, revision=revision):
                with self.assertRaises(error):
                    preview_day(self.db, day, batches, revision)
                with self.assertRaises(error):
                    publish_day(self.db, day, batches, revision, 'bad-binding')
                self.assertIsNone(DaybookStore(self.db).get('bad-binding'))

    def test_repeat_initialize_preserves_custom_data(self):
        first = self.receive('东堤', 'custom', [3, 11])
        rule = register_ruleset(self.db, 'custom', 3, 11)
        book = publish_day(self.db, DAY, [first.identity], rule.revision, 'before-init')
        initialize(self.db)
        self.reopen()
        initialize(self.db)
        self.assertEqual(register_ruleset(self.db, 'custom', 3, 11), rule)
        with self.assertRaises(ConflictError):
            register_ruleset(self.db, 'custom', 3, 12)
        for lower, upper in ((-1, 2), (3, 2), (False, 2), (0, 2.0)):
            with self.assertRaises(ValueError):
                register_ruleset(self.db, 'invalid', lower, upper)
        with self.assertRaises(KeyError):
            get_ruleset(self.db, 'invalid')
        self.assertEqual(get_ruleset(self.db, 'custom'), rule)
        self.assertEqual(self.receive('东堤', 'custom', [3, 11]), first)
        self.assertEqual(read_day(self.db, 'before-init').receipt, book.receipt)
        self.assertEqual(read_day(self.db, 'before-init').totals.liters, 14)

    def test_empty_day(self):
        result = publish_day(self.db, DAY, [], RULE, 'no-observations')
        self.assertEqual(result.manifest.volumes, ())
        self.assertEqual(result.manifest.revision, RULE)
        self.assertEqual(result.receipt.row_count, 0)
        self.assertTrue(result.receipt.code)
        self.assertEqual(publish_day(self.db, DAY, [], RULE, 'no-observations'), result)
        read = read_day(self.db, 'no-observations')
        self.assertEqual(read.receipt, result.receipt)
        self.assertEqual((read.details, read.totals.stations), ((), ()))
        self.assertEqual((read.totals.liters, read.totals.missing, read.totals.out_of_range), (0, 0, 0))

    def test_publication_failure(self):
        x, y = self.daily_pair()
        existing = publish_day(self.db, DAY, [x], RULE, 'existing')
        with fail_book_commit(self.db):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
                publish_day(self.db, DAY, [x, y], RULE, 'retry')
        self.assertFalse(self.db.in_transaction)
        self.reopen()
        with self.assertRaises(KeyError):
            read_day(self.db, 'retry')
        for table in ('daybooks', 'daybook_selection', 'volumes', 'hot_bodies'):
            with self.subTest(table=table):
                self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone(), (1,))
        self.assertEqual(read_day(self.db, 'existing').receipt, existing.receipt)
        committed = publish_day(self.db, DAY, [x, y], RULE, 'retry')
        self.assertEqual(committed.receipt.row_count, 5)
        self.assertEqual(committed.manifest.volumes[0].volume, existing.manifest.volumes[0].volume)
        self.assertEqual(publish_day(self.db, DAY, [x, y], RULE, 'retry'), committed)
        self.assertEqual(read_day(self.db, 'retry').totals.liters, 18)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM volumes').fetchone(), (2,))

    def test_same_number_other_selection_is_rejected(self):
        x, y = self.daily_pair()
        first = publish_day(self.db, DAY, [x, y, x], RULE, 'fixed')
        self.assertEqual(publish_day(self.db, DAY, [x, y], RULE, 'fixed'), first)
        with self.assertRaises(ConflictError):
            publish_day(self.db, DAY, [y, x], RULE, 'fixed')
        with self.assertRaises(ConflictError):
            publish_day(self.db, DAY, [x], RULE, 'fixed')
        self.assertEqual(DaybookStore(self.db).get('fixed'), first)

    def test_large_integers_survive_reopen(self):
        large = 10 ** 5000
        register_ruleset(self.db, 'exact', large, large)
        receipt = self.receive('东堤', 'large', [large, large, 0, None])
        publish_day(self.db, DAY, [receipt.identity], 'exact', 'exact')
        self.reopen()
        result = read_day(self.db, 'exact')
        self.assertTrue(result.totals.liters == 2 * large)
        self.assertTrue([row.value for row in result.details] == [large, large, 0, None])
        self.assertEqual((result.totals.missing, result.totals.out_of_range), (1, 1))
