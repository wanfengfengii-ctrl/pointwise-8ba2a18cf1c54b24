from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lake import ConflictError, open_lake
from lake.custody.cold_copy import archive_volume
from lake.custody.retirement import retire_volume
from lake.intake.batches import receive_batch
from lake.publication.daybooks import publish_day
from lake.quality.day_report import preview_day
from lake.quality.rules import register_ruleset
from lake.reading.daybook import ReadUnavailable, read_day


DAY = '2026-10-02'
REVISION = 'daily-20'
NUMBER = '交班-01'


def emit(case_id, inputs, expected, actual, downstream_expected, downstream_actual, paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'{case_id} business outcome differs from the sample oracle')
    record = dict(case_id=case_id, input=inputs, expected=expected, actual=actual,
                  downstream_expected=downstream_expected, downstream_actual=downstream_actual,
                  code_paths=paths, oracle=oracle)
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False, separators=(',', ':')))


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'lake.sqlite3'
        east = [0, 7, None]
        south = [11, 25]
        with closing(open_lake(path)) as db:
            register_ruleset(db, REVISION, 0, 20)
            x = receive_batch(db, '东堤', DAY, 'x', east)
            y = receive_batch(db, '南闸', DAY, 'y', south)
            if receive_batch(db, '东堤', DAY, 'x', east) != x:
                raise AssertionError('batch replay changed the first receipt')
            try:
                receive_batch(db, '东堤', DAY, 'x', [7, 0, None])
            except ConflictError:
                pass
            else:
                raise AssertionError('reordered batch unexpectedly accepted')

            selection = [x.identity, y.identity]
            report = preview_day(db, DAY, selection, REVISION)
            book = publish_day(db, DAY, selection, REVISION, NUMBER)
            read = read_day(db, NUMBER)
            actual = {'stations': [[s.station, s.liters] for s in report.totals.stations],
                      'liters': report.totals.liters, 'missing': report.totals.missing,
                      'out_of_range': report.totals.out_of_range}
            downstream = {'liters': read.totals.liters, 'rows': book.receipt.row_count,
                          'details': [[d.value, d.classification.value, d.origin.batch.station,
                                       d.origin.batch.day, d.origin.batch.number, d.origin.position]
                                      for d in read.details]}
            emit('v1', {'day': DAY, 'bounds': [0, 20], '东堤': east, '南闸': south},
                 {'stations': [['东堤', 7], ['南闸', 11]], 'liters': 18,
                  'missing': 1, 'out_of_range': 1}, actual,
                 {'liters': 18, 'rows': 5, 'details': [
                     [0, 'included', '东堤', DAY, 'x', 1],
                     [7, 'included', '东堤', DAY, 'x', 2],
                     [None, 'missing', '东堤', DAY, 'x', 3],
                     [11, 'included', '南闸', DAY, 'y', 1],
                     [25, 'out_of_range', '南闸', DAY, 'y', 2],
                 ]}, downstream, ['lake/intake/batches.py', 'lake/quality/day_report.py',
                                   'lake/publication/daybooks.py', 'lake/reading/daybook.py'],
                 '手算 0+7 与 11，按输入批及批内位置逐行核对分类和出处。')

            first, second = (entry.volume.volume_id for entry in book.manifest.volumes)
            record = archive_volume(db, second, '二号冷柜')
            retire_volume(db, second, record)

        with closing(open_lake(path)) as db:
            after = read_day(db, NUMBER)
            if not isinstance(after, ReadUnavailable):
                raise AssertionError('expected an unavailable historical account')
            emit('v7', {'number': NUMBER, 'manifest': [first, second], 'retired': second},
                 [second], list(after.missing_volume_ids), [], list(after.details),
                 ['lake/custody/cold_copy.py', 'lake/custody/retirement.py',
                  'lake/reading/coverage.py', 'lake/reading/daybook.py'],
                 '清单中只对第二卷完成退温，固定账号应仅报告该卷并交付空明细。')


if __name__ == '__main__':
    main()
