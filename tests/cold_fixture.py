from dataclasses import dataclass

from lake import ConflictError, require_name, transaction
from lake.custody.storage import VolumeStorage


class WorkSlots:
    """A fixed number of durable physical byte buffers."""

    def __init__(self, db, capacity):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('capacity must be a positive integer')
        self.db = db
        self.capacity = capacity
        with transaction(db, write=True):
            db.execute('''
                CREATE TABLE IF NOT EXISTS fixture_slot_config (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    capacity INTEGER NOT NULL CHECK (capacity > 0)
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS fixture_slots (
                    slot INTEGER PRIMARY KEY,
                    body BLOB NOT NULL
                )
            ''')
            config = db.execute('SELECT capacity FROM fixture_slot_config').fetchone()
            if config is None:
                db.execute('INSERT INTO fixture_slot_config VALUES (1, ?)', (capacity,))
                db.executemany('INSERT INTO fixture_slots VALUES (?, ?)',
                               ((slot, b'') for slot in range(capacity)))
            elif config != (capacity,):
                raise ConflictError('work slot capacity already fixed')

    def _check_slot(self, slot):
        if type(slot) is not int or not 0 <= slot < self.capacity:
            raise IndexError(slot)

    def read(self, slot) -> bytes:
        self._check_slot(slot)
        return self.db.execute('SELECT body FROM fixture_slots WHERE slot = ?',
                               (slot,)).fetchone()[0]

    def append(self, slot, data: bytes):
        self._check_slot(slot)
        if not isinstance(data, bytes):
            raise TypeError('fragment must be bytes')
        self.db.execute('''
            UPDATE fixture_slots SET body = CAST(body || ? AS BLOB) WHERE slot = ?
        ''', (data, slot))

    def clear(self, slot):
        self._check_slot(slot)
        self.db.execute('UPDATE fixture_slots SET body = ? WHERE slot = ?', (b'', slot))


@dataclass(frozen=True)
class ColdStart:
    session_id: str
    volume_id: str
    target: str
    slot: int
    fragment_size: int


@dataclass(frozen=True)
class Fragment:
    session_id: str
    slot: int
    ordinal: int
    data: bytes


@dataclass(frozen=True)
class Finished:
    session_id: str
    slot: int
    piece_count: int


class ColdFeed:
    """An external fixture whose issued events outlive a lake connection."""

    def __init__(self):
        self._issued = {}

    @property
    def starts(self) -> tuple[ColdStart, ...]:
        return tuple(start for start, _ in self._issued.values())

    def start(self, db, volume_id, target, session_id, slot, *, fragment_size=16) -> ColdStart:
        require_name(session_id, 'session identifier')
        if type(slot) is not int or slot < 0:
            raise ValueError('slot must be a nonnegative integer')
        if type(fragment_size) is not int or fragment_size < 1:
            raise ValueError('fragment size must be a positive integer')
        start = ColdStart(session_id, volume_id, target, slot, fragment_size)
        previous = self._issued.get(session_id)
        if previous is not None:
            if previous[0] != start:
                raise ConflictError('cold source event identifier already issued')
            return previous[0]
        body = VolumeStorage(db).get_cold_bytes(volume_id, target)
        if body is None:
            raise KeyError((volume_id, target))
        pieces = tuple(Fragment(session_id, slot, ordinal, body[offset:offset + fragment_size])
                       for ordinal, offset in enumerate(range(0, len(body), fragment_size)))
        self._issued[session_id] = start, (*pieces, Finished(session_id, slot, len(pieces)))
        return start

    def events(self, session_id) -> tuple[Fragment | Finished, ...]:
        return self._issued[session_id][1]
