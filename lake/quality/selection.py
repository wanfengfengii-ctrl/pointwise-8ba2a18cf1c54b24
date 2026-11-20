from dataclasses import dataclass

from lake.intake.batch_store import AcceptedBatch, BatchStore
from lake.intake.identity import BatchIdentity, require_day


@dataclass(frozen=True)
class DaySelection:
    day: str
    batches: tuple[AcceptedBatch, ...]


def selection_keys(day, batches) -> tuple[BatchIdentity, ...]:
    require_day(day)
    ordered = {}
    for identity in batches:
        if not isinstance(identity, BatchIdentity):
            raise ValueError('each selected batch must be a BatchIdentity')
        if identity.day != day:
            raise ValueError('selected batch is from a different day')
        ordered[identity] = None
    return tuple(ordered)


def select_day_batches(db, day, batches) -> DaySelection:
    store = BatchStore(db)
    selected = []
    for identity in selection_keys(day, batches):
        batch = store.get(identity)
        if batch is None:
            raise KeyError(identity)
        selected.append(batch)
    return DaySelection(day, tuple(selected))
