import sqlite3

from lake import ConflictError
from lake.custody.restore import accept_restore_event, plan_restores
from lake.publication.daybooks import publish_day
from lake.quality.classify import Classification
from lake.reading.daybook import (
    ReadUnavailable,
    end_restore,
    read_restored_day,
    request_restore,
    restore_status,
)
from lake.reading.reconcile import ReadResult
from restore_samples import TARGET, dispatch
from tests.cold_fixture import ColdFeed, Finished, Fragment, WorkSlots
from tests.support import DAY, LakeCase, make_cold_daybooks


class RestoreTests(LakeCase):
    def prepare(self, capacity):
        alpha, beta, gamma = make_cold_daybooks(self.db)
        self.books = dict(zip(('甲', '乙', '丙'), (alpha, beta, gamma), strict=True))
        self.x, self.y = (entry.volume for entry in alpha.manifest.volumes)
        self.z = gamma.manifest.volumes[0].volume
        self.feed = ColdFeed()
        self.slots = WorkSlots(self.db, capacity)

    def reopen_slots(self, capacity):
        self.reopen()
        self.slots = WorkSlots(self.db, capacity)

    def deliver(self, session_id):
        for event in self.feed.events(session_id):
            accept_restore_event(self.db, self.slots, event)

    def test_shared_volume_survives_cold_disconnect_and_reopen(self):
        self.prepare(2)
        first = request_restore(self.db, self.slots, 'req-甲', '甲')
        self.assertEqual((first.number, first.state, first.missing_volume_ids),
                         ('甲', 'waiting', (self.x.volume_id, self.y.volume_id)))
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual(len(orders), 2)
        dispatch(self.db, self.feed, orders)
        x_order = next(order for order in orders if order.volume_id == self.x.volume_id)
        y_order = next(order for order in orders if order.volume_id == self.y.volume_id)
        self.assertNotEqual(x_order.slot, y_order.slot)
        # 甲的 X 落首片，再登记乙并结束甲
        accept_restore_event(self.db, self.slots, self.feed.events(x_order.session_id)[0])
        shared = request_restore(self.db, self.slots, 'req-乙', '乙')
        self.assertEqual((shared.state, shared.missing_volume_ids), ('waiting', (self.x.volume_id,)))
        ended = end_restore(self.db, self.slots, 'req-甲')
        self.assertEqual((ended.state, ended.missing_volume_ids), ('ended', ()))
        # 冷库只为 X 开一次会话；失去需求的 Y 作为排空会话仍需终结
        orders = plan_restores(self.db, self.slots, TARGET)
        dispatch(self.db, self.feed, orders)
        self.assertEqual({order.volume_id for order in orders},
                         {self.x.volume_id, self.y.volume_id})
        x_starts = [start for start in self.feed.starts if start.volume_id == self.x.volume_id]
        self.assertEqual(len(x_starts), 1)
        self.assertEqual(x_starts[0].session_id, x_order.session_id)
        # 送齐 X（整批重投也不再追加），乙凭共享的已验槽正文领账
        self.deliver(x_order.session_id)
        self.deliver(x_order.session_id)
        status = restore_status(self.db, self.slots, 'req-乙')
        self.assertEqual((status.state, status.missing_volume_ids), ('ready', ()))
        read = read_restored_day(self.db, self.slots, 'req-乙')
        self.assertIsInstance(read, ReadResult)
        self.assertEqual([line.value for line in read.details], [7, 11])
        self.assertEqual([(line.origin.batch.station, line.origin.batch.day,
                           line.origin.batch.number, line.origin.position)
                          for line in read.details],
                         [('东堤', DAY, 'x', 1), ('东堤', DAY, 'x', 2)])
        self.assertEqual((read.totals.liters, read.totals.missing, read.totals.out_of_range),
                         (18, 0, 0))
        self.assertEqual(read.receipt, self.books['乙'].receipt)
        # 停止供片并断开冷件正文读取，重开后乙仍可领原账，甲为 ended
        self.db.execute('DELETE FROM cold_copies')
        self.reopen_slots(2)
        self.assertEqual(read_restored_day(self.db, self.slots, 'req-乙'), read)
        self.assertEqual(restore_status(self.db, self.slots, 'req-甲').state, 'ended')
        # 已验的 X 仍为乙保留；只有排空中的 Y 可重领
        self.assertEqual([order.volume_id for order in plan_restores(self.db, self.slots, TARGET)],
                         [self.y.volume_id])

    def test_single_slot_handover_rejects_stale_events(self):
        self.prepare(1)
        request_restore(self.db, self.slots, 'req-乙', '乙')
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.x.volume_id])
        dispatch(self.db, self.feed, orders)
        (x_order,) = orders
        # 乙的 X 落首片后结束乙，再登记丙
        accept_restore_event(self.db, self.slots, self.feed.events(x_order.session_id)[0])
        end_restore(self.db, self.slots, 'req-乙')
        request_restore(self.db, self.slots, 'req-丙', '丙')
        # 排空策略：原 X 终结前不派发 Z，重领的指令参数固定
        draining = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual(draining, (x_order,))
        self.assertEqual(restore_status(self.db, self.slots, 'req-丙').state, 'waiting')
        for event in self.feed.events(x_order.session_id)[1:]:
            accept_restore_event(self.db, self.slots, event)
        # X 已验但需求已解除：归还槽位后派发 Z
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.z.volume_id])
        (z_order,) = orders
        self.assertEqual(z_order.slot, x_order.slot)
        self.assertNotEqual(z_order.session_id, x_order.session_id)
        dispatch(self.db, self.feed, orders)
        accept_restore_event(self.db, self.slots, self.feed.events(z_order.session_id)[0])
        held = self.slots.read(0)
        self.reopen_slots(1)
        # 重开后可重领未终结指令；重放 X 的旧分片和旧终结均无权改动槽位
        self.assertEqual(plan_restores(self.db, self.slots, TARGET), (z_order,))
        for event in self.feed.events(x_order.session_id):
            accept_restore_event(self.db, self.slots, event)
        self.assertEqual(self.slots.read(0), held)
        status = restore_status(self.db, self.slots, 'req-丙')
        self.assertEqual((status.state, status.missing_volume_ids),
                         ('waiting', (self.z.volume_id,)))
        # 正常收齐 Z，丙领到西渠批 z 原位 1 的 23 L
        for event in self.feed.events(z_order.session_id)[1:]:
            accept_restore_event(self.db, self.slots, event)
        read = read_restored_day(self.db, self.slots, 'req-丙')
        self.assertEqual([line.value for line in read.details], [23])
        self.assertEqual([(line.origin.batch.station, line.origin.batch.number,
                           line.origin.position) for line in read.details],
                         [('西渠', 'z', 1)])
        self.assertEqual(read.totals.liters, 23)
        self.assertEqual(read.receipt, self.books['丙'].receipt)

    def test_whole_account_gating_and_retry(self):
        self.prepare(2)
        request_restore(self.db, self.slots, 'req-甲', '甲')
        orders = plan_restores(self.db, self.slots, TARGET)
        dispatch(self.db, self.feed, orders)
        by_volume = {order.volume_id: order for order in orders}
        x_order = by_volume[self.x.volume_id]
        y_order = by_volume[self.y.volume_id]
        # X 先验毕，甲仍待回温且无部分明细
        self.deliver(x_order.session_id)
        waiting = restore_status(self.db, self.slots, 'req-甲')
        self.assertEqual((waiting.state, waiting.missing_volume_ids),
                         ('waiting', (self.y.volume_id,)))
        unavailable = read_restored_day(self.db, self.slots, 'req-甲')
        self.assertIsInstance(unavailable, ReadUnavailable)
        self.assertEqual((unavailable.number, unavailable.missing_volume_ids,
                          unavailable.details), ('甲', (self.y.volume_id,), ()))
        # 错序的 Y 分片不改动正文或请求状态
        y_events = self.feed.events(y_order.session_id)
        accept_restore_event(self.db, self.slots, y_events[1])
        self.assertEqual(self.slots.read(y_order.slot), b'')
        self.assertEqual(restore_status(self.db, self.slots, 'req-甲').state, 'waiting')
        # 编号连续但指纹错误的 Y：验毕失败，仍不可读，后续计划以新会话重试
        accept_restore_event(self.db, self.slots,
                             Fragment(y_order.session_id, y_order.slot, 0, b'forged-y'))
        accept_restore_event(self.db, self.slots,
                             Finished(y_order.session_id, y_order.slot, 1))
        still = restore_status(self.db, self.slots, 'req-甲')
        self.assertEqual((still.state, still.missing_volume_ids),
                         ('waiting', (self.y.volume_id,)))
        retry = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in retry], [self.y.volume_id])
        self.assertNotEqual(retry[0].session_id, y_order.session_id)
        self.assertEqual(self.slots.read(retry[0].slot), b'')
        dispatch(self.db, self.feed, retry)
        self.deliver(retry[0].session_id)
        # 合法的 X、Y 均验毕后，甲按原序领取，原汇总 23 L，缺测一行
        read = read_restored_day(self.db, self.slots, 'req-甲')
        self.assertEqual([line.value for line in read.details], [7, 11, 5, None])
        self.assertEqual([line.classification for line in read.details],
                         [Classification.INCLUDED, Classification.INCLUDED,
                          Classification.INCLUDED, Classification.MISSING])
        self.assertEqual([(line.origin.batch.number, line.origin.position)
                          for line in read.details],
                         [('x', 1), ('x', 2), ('y', 1), ('y', 2)])
        self.assertEqual((read.totals.liters, read.totals.missing), (23, 1))
        self.assertEqual(read.receipt, self.books['甲'].receipt)
        # 重开保持原收据及各行出处
        self.reopen_slots(2)
        self.assertEqual(read_restored_day(self.db, self.slots, 'req-甲'), read)

    def test_request_lifecycle_and_errors(self):
        self.prepare(2)
        for bad in ('', '   ', None, 7):
            with self.assertRaises(ValueError):
                request_restore(self.db, self.slots, bad, '甲')
            with self.assertRaises(ValueError):
                request_restore(self.db, self.slots, 'req', bad)
        with self.assertRaises(KeyError):
            request_restore(self.db, self.slots, 'req', '未知账号')
        with self.assertRaises(KeyError):
            restore_status(self.db, self.slots, 'req')
        with self.assertRaises(KeyError):
            read_restored_day(self.db, self.slots, 'req')
        with self.assertRaises(KeyError):
            end_restore(self.db, self.slots, 'req')
        first = request_restore(self.db, self.slots, 'req', '甲')
        self.assertEqual(first.state, 'waiting')
        self.assertEqual(request_restore(self.db, self.slots, 'req', '甲'), first)
        with self.assertRaises(ConflictError):
            request_restore(self.db, self.slots, 'req', '乙')
        # 空清单立即可读
        publish_day(self.db, DAY, [], 'night-30', '空账')
        empty = request_restore(self.db, self.slots, 'req-empty', '空账')
        self.assertEqual((empty.state, empty.missing_volume_ids), ('ready', ()))
        read = read_restored_day(self.db, self.slots, 'req-empty')
        self.assertIsInstance(read, ReadResult)
        self.assertEqual((read.details, read.totals.liters), ((), 0))
        # 未退温的账号直接可读
        w = self.receive('西渠', 'w', [3]).identity
        publish_day(self.db, DAY, [w], 'night-30', '新账')
        hot = request_restore(self.db, self.slots, 'req-hot', '新账')
        self.assertEqual(hot.state, 'ready')
        self.assertEqual([line.value for line in
                          read_restored_day(self.db, self.slots, 'req-hot').details], [3])
        # 结束：重复结束仍为 ended，结束后读取抛 ValueError
        ended = end_restore(self.db, self.slots, 'req')
        self.assertEqual((ended.state, ended.missing_volume_ids), ('ended', ()))
        self.assertEqual(end_restore(self.db, self.slots, 'req'), ended)
        self.assertEqual(restore_status(self.db, self.slots, 'req').state, 'ended')
        self.assertEqual(request_restore(self.db, self.slots, 'req', '甲').state, 'ended')
        with self.assertRaises(ValueError):
            read_restored_day(self.db, self.slots, 'req')

    def test_capacity_full_waits_for_release(self):
        self.prepare(1)
        request_restore(self.db, self.slots, 'req-乙', '乙')
        request_restore(self.db, self.slots, 'req-丙', '丙')
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.x.volume_id])
        self.assertEqual(restore_status(self.db, self.slots, 'req-丙').missing_volume_ids,
                         (self.z.volume_id,))
        dispatch(self.db, self.feed, orders)
        self.deliver(orders[0].session_id)
        self.assertEqual(restore_status(self.db, self.slots, 'req-乙').state, 'ready')
        # 已验的 X 仍为乙保留：槽满，丙保持待回温
        self.assertEqual(plan_restores(self.db, self.slots, TARGET), ())
        self.assertEqual(restore_status(self.db, self.slots, 'req-丙').state, 'waiting')
        # 乙结束后归还槽位，下一次计划推进丙
        end_restore(self.db, self.slots, 'req-乙')
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.z.volume_id])
        dispatch(self.db, self.feed, orders)
        self.deliver(orders[0].session_id)
        read = read_restored_day(self.db, self.slots, 'req-丙')
        self.assertEqual([line.value for line in read.details], [23])

    def test_unknown_and_malformed_events_are_safe(self):
        self.prepare(1)
        request_restore(self.db, self.slots, 'req-乙', '乙')
        orders = plan_restores(self.db, self.slots, TARGET)
        dispatch(self.db, self.feed, orders)
        (x_order,) = orders
        accept_restore_event(self.db, self.slots, Fragment('ghost', 0, 0, b'nope'))
        accept_restore_event(self.db, self.slots, Finished('ghost', 0, 0))
        accept_restore_event(self.db, self.slots, Fragment(x_order.session_id, 1, 0, b'x'))
        self.assertEqual(self.slots.read(0), b'')
        with self.assertRaises(ValueError):
            accept_restore_event(self.db, self.slots, object())
        with self.assertRaises(ValueError):
            accept_restore_event(self.db, self.slots, Fragment('', 0, 0, b'x'))
        with self.assertRaises(ValueError):
            accept_restore_event(self.db, self.slots, Fragment(x_order.session_id, -1, 0, b'x'))
        self.assertEqual(restore_status(self.db, self.slots, 'req-乙').state, 'waiting')

    def test_write_failure_preserves_progress(self):
        self.prepare(1)
        self.assertEqual(plan_restores(self.db, self.slots, TARGET), ())
        # 申请写入失败：不留下请求或会话进度
        self.db.execute('''
            CREATE TEMP TRIGGER reject_request BEFORE INSERT ON main.restore_requests
            BEGIN SELECT RAISE(ABORT, 'request store write failed'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'request store write failed'):
            request_restore(self.db, self.slots, 'req-乙', '乙')
        self.assertFalse(self.db.in_transaction)
        self.db.execute('DROP TRIGGER reject_request')
        with self.assertRaises(KeyError):
            restore_status(self.db, self.slots, 'req-乙')
        request_restore(self.db, self.slots, 'req-乙', '乙')
        # 计划写入失败：不留下会话，槽字节保持原样
        self.db.execute('''
            CREATE TEMP TRIGGER reject_session BEFORE INSERT ON main.restore_sessions
            BEGIN SELECT RAISE(ABORT, 'session store write failed'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'session store write failed'):
            plan_restores(self.db, self.slots, TARGET)
        self.assertEqual(self.slots.read(0), b'')
        self.db.execute('DROP TRIGGER reject_session')
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.x.volume_id])
        dispatch(self.db, self.feed, orders)
        # 槽字节写入失败：已接纳进度保留在操作前状态
        self.db.execute('''
            CREATE TEMP TRIGGER reject_slot_write BEFORE UPDATE ON main.fixture_slots
            BEGIN SELECT RAISE(ABORT, 'slot device write failed'); END
        ''')
        first = self.feed.events(orders[0].session_id)[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'slot device write failed'):
            accept_restore_event(self.db, self.slots, first)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.slots.read(0), b'')
        self.db.execute('DROP TRIGGER reject_slot_write')
        accept_restore_event(self.db, self.slots, first)
        self.assertEqual(self.slots.read(0), first.data)
        self.deliver(orders[0].session_id)
        self.assertEqual(restore_status(self.db, self.slots, 'req-乙').state, 'ready')
        # 释放与重派写入失败：已验正文、申请与验收进度一起保留
        end_restore(self.db, self.slots, 'req-乙')
        request_restore(self.db, self.slots, 'req-丙', '丙')
        x_body = self.slots.read(0)
        self.db.execute('''
            CREATE TEMP TRIGGER reject_session BEFORE INSERT ON main.restore_sessions
            BEGIN SELECT RAISE(ABORT, 'session store write failed'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'session store write failed'):
            plan_restores(self.db, self.slots, TARGET)
        self.assertEqual(self.slots.read(0), x_body)
        self.assertEqual(restore_status(self.db, self.slots, 'req-丙').missing_volume_ids,
                         (self.z.volume_id,))
        self.db.execute('DROP TRIGGER reject_session')
        orders = plan_restores(self.db, self.slots, TARGET)
        self.assertEqual([order.volume_id for order in orders], [self.z.volume_id])
