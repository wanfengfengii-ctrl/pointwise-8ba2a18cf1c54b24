from dataclasses import dataclass, field

from lake import require_name, transaction
from lake.publication.daybook_store import DaybookStore
from lake.reading.coverage import collect_coverage
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
