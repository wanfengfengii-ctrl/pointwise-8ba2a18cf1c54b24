from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lake import ConflictError, open_lake
from lake.custody.cold_copy import archive_volume
from lake.custody.restore import accept_restore_event, plan_restores
from lake.custody.retirement import retire_volume
from lake.intake.batches import receive_batch
from lake.publication.daybooks import publish_day
from lake.quality.day_report import preview_day
from lake.quality.rules import register_ruleset
from lake.reading.daybook import (
    ReadUnavailable,
    end_restore,
    read_day,
    read_restored_day,
    request_restore,
    restore_status,
)
import restore_samples
from tests.cold_fixture import Finished, Fragment, WorkSlots


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


def restore_demos(directory):
    paths = ['lake/reading/daybook.py', 'lake/custody/restore.py']
    target = restore_samples.TARGET

    # 两槽共享与断源领取：甲需 X、Y，乙需 X，冷库只为 X 开一次会话
    path = Path(directory) / 'restore-share.sqlite3'
    with closing(open_lake(path)) as db:
        slots, feed, books, refs = restore_samples.prepare(db, 2)
        names = {ref.volume_id: name for name, ref in refs.items()}
        request_restore(db, slots, '夜班-1', '甲')
        restore_samples.dispatch(db, feed, plan_restores(db, slots, target))
        x_order = next(order for order in plan_restores(db, slots, target)
                       if names[order.volume_id] == 'X')
        accept_restore_event(db, slots, feed.events(x_order.session_id)[0])
        request_restore(db, slots, '夜班-2', '乙')
        end_restore(db, slots, '夜班-1')
        restore_samples.dispatch(db, feed, plan_restores(db, slots, target))
        x_sessions = [start for start in feed.starts if names[start.volume_id] == 'X']
        for event in feed.events(x_order.session_id):
            accept_restore_event(db, slots, event)
        status = restore_status(db, slots, '夜班-2')
        read = read_restored_day(db, slots, '夜班-2')
        actual = {'state': status.state, 'x_sessions': len(x_sessions),
                  'details': [[line.value, line.origin.batch.station,
                               line.origin.batch.number, line.origin.position]
                              for line in read.details],
                  'liters': read.totals.liters}
        db.execute('DELETE FROM cold_copies')  # 停止供片并断开冷件正文读取
    with closing(open_lake(path)) as db:
        slots = WorkSlots(db, 2)
        again = read_restored_day(db, slots, '夜班-2')
        downstream = {'state': restore_status(db, slots, '夜班-2').state,
                      'liters': again.totals.liters,
                      '甲': restore_status(db, slots, '夜班-1').state,
                      'cold_copies': db.execute('SELECT COUNT(*) FROM cold_copies').fetchone()[0]}
    emit('r1', {'capacity': 2, '甲': ['X', 'Y'], '乙': ['X'], 'shared': 'X'},
         {'state': 'ready', 'x_sessions': 1,
          'details': [[7, '东堤', 'x', 1], [11, '东堤', 'x', 2]], 'liters': 18}, actual,
         {'state': 'ready', 'liters': 18, '甲': 'ended', 'cold_copies': 0}, downstream,
         paths, '甲乙共用 X 的一次回温会话与已验槽正文；断源重开后仅凭本地已验槽领原账。')

    # 单槽转手：乙的 X 落首片后结束乙，登记丙，排空至原 X 终结才派发 Z
    path = Path(directory) / 'restore-handover.sqlite3'
    with closing(open_lake(path)) as db:
        slots, feed, books, refs = restore_samples.prepare(db, 1)
        names = {ref.volume_id: name for name, ref in refs.items()}
        request_restore(db, slots, '夜班-1', '乙')
        restore_samples.dispatch(db, feed, plan_restores(db, slots, target))
        (x_order,) = plan_restores(db, slots, target)
        accept_restore_event(db, slots, feed.events(x_order.session_id)[0])
        end_restore(db, slots, '夜班-1')
        request_restore(db, slots, '夜班-2', '丙')
        drain = [names[order.volume_id] for order in plan_restores(db, slots, target)]
        for event in feed.events(x_order.session_id)[1:]:
            accept_restore_event(db, slots, event)
        handover = plan_restores(db, slots, target)
        restore_samples.dispatch(db, feed, handover)
        (z_order,) = handover
        accept_restore_event(db, slots, feed.events(z_order.session_id)[0])
        held = slots.read(z_order.slot)
        actual = {'drain': drain, 'handover': [names[order.volume_id] for order in handover]}
    with closing(open_lake(path)) as db:
        slots = WorkSlots(db, 1)
        reclaimed = [names[order.volume_id] for order in plan_restores(db, slots, target)]
        for event in feed.events(x_order.session_id):  # 重放 X 的旧分片和旧终结
            accept_restore_event(db, slots, event)
        stale_held = slots.read(z_order.slot) == held
        waiting = restore_status(db, slots, '夜班-2')
        for event in feed.events(z_order.session_id)[1:]:
            accept_restore_event(db, slots, event)
        read = read_restored_day(db, slots, '夜班-2')
        downstream = {'reclaimed': reclaimed, 'stale_replay_held': stale_held,
                      '丙_after_replay': waiting.state,
                      'details': [[line.value, line.origin.batch.station,
                                   line.origin.batch.number, line.origin.position]
                                  for line in read.details],
                      'liters': read.totals.liters}
    emit('r2', {'capacity': 1, '乙': ['X'], '丙': ['Z']},
         {'drain': ['X'], 'handover': ['Z']}, actual,
         {'reclaimed': ['Z'], 'stale_replay_held': True, '丙_after_replay': 'waiting',
          'details': [[23, '西渠', 'z', 1]], 'liters': 23}, downstream,
         paths, '单槽排空至原 X 终结后转手 Z；重开重放的旧分片与旧终结无权改动槽字节或丙状态。')

    # 整账门控：X 先验毕仍待回温，错序与坏卷不可读，合法 X、Y 齐备后按原序领取
    path = Path(directory) / 'restore-gating.sqlite3'
    with closing(open_lake(path)) as db:
        slots, feed, books, refs = restore_samples.prepare(db, 2)
        names = {ref.volume_id: name for name, ref in refs.items()}
        request_restore(db, slots, '夜班-1', '甲')
        orders = plan_restores(db, slots, target)
        restore_samples.dispatch(db, feed, orders)
        by_name = {names[order.volume_id]: order for order in orders}
        for event in feed.events(by_name['X'].session_id):
            accept_restore_event(db, slots, event)
        gated = restore_status(db, slots, '夜班-1')
        y_order = by_name['Y']
        accept_restore_event(db, slots, feed.events(y_order.session_id)[1])  # 错序分片
        out_of_order_bytes = len(slots.read(y_order.slot))
        accept_restore_event(db, slots, Fragment(y_order.session_id, y_order.slot, 0, b'forged-y'))
        accept_restore_event(db, slots, Finished(y_order.session_id, y_order.slot, 1))
        forged = restore_status(db, slots, '夜班-1')
        retry = plan_restores(db, slots, target)
        restore_samples.dispatch(db, feed, retry)
        y_retry = next(order for order in retry if names[order.volume_id] == 'Y')
        for event in feed.events(y_retry.session_id):
            accept_restore_event(db, slots, event)
        read = read_restored_day(db, slots, '夜班-1')
        actual = {'gated': [gated.state, [names[v] for v in gated.missing_volume_ids]],
                  'out_of_order_bytes': out_of_order_bytes, 'forged': forged.state,
                  'values': [line.value for line in read.details],
                  'liters': read.totals.liters, 'missing': read.totals.missing}
    with closing(open_lake(path)) as db:
        slots = WorkSlots(db, 2)
        again = read_restored_day(db, slots, '夜班-1')
        downstream = {'receipt': again.receipt.code,
                      'values': [line.value for line in again.details],
                      'origins': [[line.origin.batch.station, line.origin.batch.number,
                                   line.origin.position] for line in again.details]}
    emit('r3', {'capacity': 2, '甲': ['X', 'Y']},
         {'gated': ['waiting', ['Y']], 'out_of_order_bytes': 0, 'forged': 'waiting',
          'values': [7, 11, 5, None], 'liters': 23, 'missing': 1}, actual,
         {'receipt': books['甲'].receipt.code, 'values': [7, 11, 5, None],
          'origins': [['东堤', 'x', 1], ['东堤', 'x', 2], ['南闸', 'y', 1], ['南闸', 'y', 2]]},
         downstream, paths,
         '整账按同一可见状态核对：错序与指纹错误的 Y 不可读，齐备后按原序交付明细、出处与原汇总。')


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

        restore_demos(directory)


if __name__ == '__main__':
    main()
