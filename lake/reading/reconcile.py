from dataclasses import dataclass

from lake import DataIntegrityError
from lake.publication.daybook_store import PublishedDaybook
from lake.publication.manifests import DayManifest
from lake.publication.provenance import matches_origin_span
from lake.publication.receipts import PublicationReceipt
from lake.quality.station_totals import DayTotals
from lake.reading.details import ReadDetail


@dataclass(frozen=True)
class ReadResult:
    manifest: DayManifest
    totals: DayTotals
    receipt: PublicationReceipt
    details: tuple[ReadDetail, ...]


def reconcile_daybook(book: PublishedDaybook, details: tuple[ReadDetail, ...]) -> ReadResult:
    offset = 0
    for entry in book.manifest.volumes:
        end = offset + entry.row_count
        rows = details[offset:end]
        if any(row.volume_id != entry.volume.volume_id for row in rows):
            raise DataIntegrityError('detail has a different volume identity')
        if not matches_origin_span((row.origin for row in rows), entry.batch,
                                   entry.first_position, entry.last_position):
            raise DataIntegrityError('detail origins differ from the fixed manifest')
        offset = end
    if offset != len(details) or offset != book.receipt.row_count:
        raise DataIntegrityError('whole daybook row count differs from its receipt')
    return ReadResult(book.manifest, book.totals, book.receipt, details)
