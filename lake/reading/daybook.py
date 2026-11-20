from dataclasses import dataclass, field

from lake import ConflictError, DataIntegrityError, require_name, transaction
from lake.custody.restore import RestoreStore
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import matches_volume, read_online_volume
from lake.publication.daybook_store import DaybookStore
from lake.reading.coverage import VolumeCoverage, collect_coverage
from lake.reading.details import ReadDetail, assemble_details
from lake.reading.reconcile import ReadResult, reconcile_daybook


@dataclass(frozen=True)
class ReadUnavailable:
    number: str
    missing_volume_ids: tuple[str, ...]
    details: tuple[ReadDetail, ...] = field(default=(), init=False)


def read_day(db, number: str) -> ReadResult | ReadUnavailable:
    require_name(number, 'daybook number')
    with transaction(db):
        book = DaybookStore(db).get(number)
        if book is None:
            raise KeyError(number)
        coverage = collect_coverage(db, book.manifest)
        if coverage.missing_volume_ids:
            return ReadUnavailable(number, coverage.missing_volume_ids)
        details = assemble_details(book.manifest, coverage)
        return reconcile_daybook(book, details)


@dataclass(frozen=True)
class RestoreStatus:
    number: str
    state: str
    missing_volume_ids: tuple[str, ...]


def _restored_coverage(db, slots, store: RestoreStore, book) -> VolumeCoverage:
    """Verified slot bodies first, then V1 online copies, else manifest-ordered missing."""
    verified = {session.volume_id: session.slot for session in store.sessions('verified')}
    storage = VolumeStorage(db)
    bodies = []
    missing = {}
    for entry in book.manifest.volumes:
        ref = entry.volume
        body = None
        slot = verified.get(ref.volume_id)
        if slot is not None:
            candidate = slots.read(slot)
            if matches_volume(ref, candidate):
                body = candidate
        if body is None:
            body = read_online_volume(storage, ref)
        if body is None:
            missing[ref.volume_id] = None
        else:
            bodies.append(body)
    if missing:
        return VolumeCoverage((), tuple(missing))
    return VolumeCoverage(tuple(bodies), ())


def _get_book(db, number: str):
    book = DaybookStore(db).get(number)
    if book is None:
        raise DataIntegrityError('restore request bound to an unknown daybook')
    return book


def _get_request(store: RestoreStore, request_id: str):
    request = store.get_request(request_id)
    if request is None:
        raise KeyError(request_id)
    return request


def _status_of(db, slots, store: RestoreStore, request) -> RestoreStatus:
    if request.ended:
        return RestoreStatus(request.number, 'ended', ())
    coverage = _restored_coverage(db, slots, store, _get_book(db, request.number))
    if coverage.missing_volume_ids:
        return RestoreStatus(request.number, 'waiting', coverage.missing_volume_ids)
    return RestoreStatus(request.number, 'ready', ())


def request_restore(db, slots, request_id: str, number: str) -> RestoreStatus:
    require_name(request_id, 'restore request identifier')
    require_name(number, 'daybook number')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        request = store.get_request(request_id)
        if request is None:
            if DaybookStore(db).get(number) is None:
                raise KeyError(number)
            store.insert_request(request_id, number)
            request = store.get_request(request_id)
        elif request.number != number:
            raise ConflictError('restore request already bound to a different daybook')
        return _status_of(db, slots, store, request)


def restore_status(db, slots, request_id: str) -> RestoreStatus:
    require_name(request_id, 'restore request identifier')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        return _status_of(db, slots, store, _get_request(store, request_id))


def read_restored_day(db, slots, request_id: str) -> ReadResult | ReadUnavailable:
    require_name(request_id, 'restore request identifier')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        request = _get_request(store, request_id)
        if request.ended:
            raise ValueError('restore request already ended')
        book = _get_book(db, request.number)
        coverage = _restored_coverage(db, slots, store, book)
        if coverage.missing_volume_ids:
            return ReadUnavailable(request.number, coverage.missing_volume_ids)
        details = assemble_details(book.manifest, coverage)
        return reconcile_daybook(book, details)


def end_restore(db, slots, request_id: str) -> RestoreStatus:
    require_name(request_id, 'restore request identifier')
    with transaction(db, write=True):
        store = RestoreStore(db)
        store.initialize()
        request = _get_request(store, request_id)
        store.end_request(request_id)
        return RestoreStatus(request.number, 'ended', ())
