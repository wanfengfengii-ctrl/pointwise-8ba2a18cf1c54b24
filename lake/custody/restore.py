"""夜班回温的保管入口：冷卷会话与实体工作槽。

本实现选择"排空至终结"的槽位归还策略，归还槽位的条件：

- 进行中的会话（receiving）始终占用其实体槽，直到接纳一条片数与已接纳
  连续前缀一致的终结通知；需求已解除的会话保留到终结（排空），
  plan_restores 会继续派发它的工作指令，槽满时新需求保持待回温。
- 终结时按固定卷核对实际长度与 SHA-256：验明的正文保留在实体槽内，
  只要仍有活跃请求需要该卷（已验卷在仍有需求时保留）；核对失败，
  或最后一个需求结束后，槽位由下一次 plan_restores 交还给新会话，
  坏卷无可读承诺，后续计划以新会话重试。
- 槽位只在接受新会话时清空。重开后判断旧通知资格所依据的事实是
  restore_sessions 中持久化的会话状态与槽号绑定：只有当前绑定该槽且
  仍处于 receiving 的会话持有写入资格；已终结（verified/failed）或
  已归还（released）的旧会话，其迟到分片与旧终结通知一律无权改动
  槽字节、验收结果或账状态。
"""

from dataclasses import dataclass

from lake import DataIntegrityError, require_name, transaction
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import VolumeRef, matches_volume, read_online_volume
from lake.publication.daybook_store import DaybookStore


@dataclass(frozen=True)
class RestoreOrder:
    session_id: str
    volume_id: str
    target: str
    slot: int
    fragment_size: int


@dataclass(frozen=True)
class RestoreRequest:
    request_id: str
    number: str
    ended: bool


@dataclass(frozen=True)
class RestoreSession:
    session_id: str
    volume_id: str
    target: str
    slot: int
    fragment_size: int
    accepted: int
    state: str


class RestoreStore:
    """Durable restore requests and cold-volume sessions in the caller's transaction."""

    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS restore_requests (
                request_id TEXT PRIMARY KEY NOT NULL,
                number TEXT NOT NULL REFERENCES daybooks(number),
                ended INTEGER NOT NULL CHECK (ended IN (0, 1))
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS restore_sessions (
                session_id TEXT PRIMARY KEY NOT NULL,
                seq INTEGER NOT NULL,
                volume_id TEXT NOT NULL REFERENCES volumes(volume_id),
                target TEXT NOT NULL,
                slot INTEGER NOT NULL CHECK (slot >= 0),
                fragment_size INTEGER NOT NULL CHECK (fragment_size > 0),
                accepted INTEGER NOT NULL CHECK (accepted >= 0),
                state TEXT NOT NULL CHECK (state IN
                    ('receiving', 'verified', 'failed', 'released'))
            )
        ''')
        self.db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS restore_live_slot
            ON restore_sessions (slot) WHERE state IN ('receiving', 'verified')
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS restore_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                next_session INTEGER NOT NULL
            )
        ''')
        self.db.execute('INSERT OR IGNORE INTO restore_meta VALUES (1, 1)')

    def get_request(self, request_id: str) -> RestoreRequest | None:
        row = self.db.execute('''
            SELECT request_id, number, ended FROM restore_requests WHERE request_id = ?
        ''', (request_id,)).fetchone()
        return None if row is None else RestoreRequest(row[0], row[1], bool(row[2]))

    def insert_request(self, request_id: str, number: str):
        self.db.execute('''
            INSERT INTO restore_requests (request_id, number, ended) VALUES (?, ?, 0)
        ''', (request_id, number))

    def end_request(self, request_id: str):
        self.db.execute('UPDATE restore_requests SET ended = 1 WHERE request_id = ?',
                        (request_id,))

    def active_requests(self) -> tuple[RestoreRequest, ...]:
        return tuple(RestoreRequest(request_id, number, bool(ended))
                     for request_id, number, ended in self.db.execute('''
                         SELECT request_id, number, ended FROM restore_requests
                         WHERE ended = 0 ORDER BY rowid
                     '''))

    def get_session(self, session_id: str) -> RestoreSession | None:
        row = self.db.execute('''
            SELECT session_id, volume_id, target, slot, fragment_size, accepted, state
            FROM restore_sessions WHERE session_id = ?
        ''', (session_id,)).fetchone()
        return None if row is None else RestoreSession(*row)

    def insert_session(self, session_id: str, seq: int, volume_id: str, target: str,
                       slot: int, fragment_size: int):
        self.db.execute('''
            INSERT INTO restore_sessions
                (session_id, seq, volume_id, target, slot, fragment_size, accepted, state)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'receiving')
        ''', (session_id, seq, volume_id, target, slot, fragment_size))

    def set_accepted(self, session_id: str, accepted: int):
        self.db.execute('UPDATE restore_sessions SET accepted = ? WHERE session_id = ?',
                        (accepted, session_id))

    def set_state(self, session_id: str, state: str):
        self.db.execute('UPDATE restore_sessions SET state = ? WHERE session_id = ?',
                        (state, session_id))

    def sessions(self, *states: str) -> tuple[RestoreSession, ...]:
        marks = ', '.join('?' for _ in states)
        return tuple(RestoreSession(*row) for row in self.db.execute(f'''
            SELECT session_id, volume_id, target, slot, fragment_size, accepted, state
            FROM restore_sessions WHERE state IN ({marks}) ORDER BY seq
        ''', states))

    def next_sequence(self) -> int:
        seq = self.db.execute('''
            SELECT next_session FROM restore_meta WHERE singleton = 1
        ''').fetchone()[0]
        self.db.execute('UPDATE restore_meta SET next_session = ? WHERE singleton = 1',
                        (seq + 1,))
        return seq


def plan_restores(db, slots, target: str, *, fragment_size=16) -> tuple[RestoreOrder, ...]:
    """Assign free slots to missing-volume demand and list unterminated work orders."""
    require_name(target, 'cold target')
    if type(fragment_size) is not int or fragment_size < 1:
        raise ValueError('fragment size must be a positive integer')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        books = DaybookStore(db)
        storage = VolumeStorage(db)
        manifests = []
        demanded = set()
        for request in store.active_requests():
            book = books.get(request.number)
            if book is None:
                raise DataIntegrityError('restore request bound to an unknown daybook')
            manifests.append(book)
            demanded.update(entry.volume.volume_id for entry in book.manifest.volumes)
        # 需求已解除的已验会话归还槽位；进行中的会话一律保留到终结（排空）。
        for session in store.sessions('verified'):
            if session.volume_id not in demanded:
                store.set_state(session.session_id, 'released')
        live = store.sessions('receiving', 'verified')
        receiving = {session.volume_id for session in live if session.state == 'receiving'}
        verified = {session.volume_id for session in live if session.state == 'verified'}
        occupied = {session.slot for session in live}
        for book in manifests:
            for entry in book.manifest.volumes:
                volume_id = entry.volume.volume_id
                if volume_id in receiving or volume_id in verified:
                    continue  # 共享进行中的回温或同一份已验槽正文
                if read_online_volume(storage, entry.volume) is not None:
                    continue
                free = [slot for slot in range(slots.capacity) if slot not in occupied]
                if not free:
                    continue  # 槽满：请求保持待回温，释放后由下一次计划推进
                slot = free[0]
                seq = store.next_sequence()
                session_id = f'restore-{seq}'
                slots.clear(slot)
                store.insert_session(session_id, seq, volume_id, target, slot, fragment_size)
                receiving.add(volume_id)
                occupied.add(slot)
        return tuple(
            RestoreOrder(session.session_id, session.volume_id, session.target,
                         session.slot, session.fragment_size)
            for session in sorted(store.sessions('receiving'),
                                  key=lambda session: session.slot))


def accept_restore_event(db, slots, event):
    """Accept one fragment or finish notice; ineligible events change nothing."""
    session_id = require_name(getattr(event, 'session_id', None), 'session identifier')
    slot = getattr(event, 'slot', None)
    if type(slot) is not int or slot < 0:
        raise ValueError('event slot must be a nonnegative integer')
    ordinal = getattr(event, 'ordinal', None)
    data = getattr(event, 'data', None)
    piece_count = getattr(event, 'piece_count', None)
    is_fragment = ordinal is not None and data is not None
    if is_fragment:
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('fragment ordinal must be a nonnegative integer')
        if not isinstance(data, bytes):
            raise ValueError('fragment data must be bytes')
    elif piece_count is None:
        raise ValueError('restore event must be a fragment or a finish notice')
    elif type(piece_count) is not int or piece_count < 0:
        raise ValueError('finish piece count must be a nonnegative integer')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        session = store.get_session(session_id)
        if session is None or session.state != 'receiving' or session.slot != slot:
            return  # 迟到、重投、错槽或失去写入资格的事件不改动正文或请求状态
        if is_fragment:
            if ordinal != session.accepted:
                return  # 只接纳连续下一片，已接纳序位的重投不再追加
            slots.append(slot, data)
            store.set_accepted(session_id, session.accepted + 1)
            return
        if piece_count != session.accepted:
            return  # 终结的片数须与已接纳的连续前缀一致
        meta = VolumeStorage(db).get_volume(session.volume_id)
        if meta is None:
            raise DataIntegrityError('restore session bound to an unknown volume')
        if matches_volume(VolumeRef(*meta), slots.read(slot)):
            store.set_state(session_id, 'verified')  # 已验证的正文保留在实体槽内
        else:
            store.set_state(session_id, 'failed')  # 坏卷无可读承诺，槽位交还后续计划重试
