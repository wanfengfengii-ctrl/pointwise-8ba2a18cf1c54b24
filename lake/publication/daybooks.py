from lake import ConflictError, require_name, transaction
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import ensure_volume
from lake.publication.body import FrozenRow, encode_volume_body
from lake.publication.daybook_store import DaybookStore, PublishedDaybook
from lake.publication.manifests import VolumeEntry, build_manifest
from lake.publication.provenance import make_row_origin
from lake.publication.receipts import make_publication_receipt
from lake.quality.day_report import build_day_report
from lake.quality.selection import selection_keys


def publish_day(db, day, batches, revision, number) -> PublishedDaybook:
    require_name(number, 'daybook number')
    require_name(revision, 'rule revision')
    selected = selection_keys(day, batches)
    books = DaybookStore(db)
    storage = VolumeStorage(db)
    with transaction(db, write=True):
        existing = books.get(number)
        if existing is not None:
            fixed = existing.manifest
            if (fixed.day, fixed.revision, fixed.batches) != (day, revision, selected):
                raise ConflictError('daybook number already has a different request')
            return existing

        report = build_day_report(db, day, selected, revision)
        entries = []
        for batch in report.batches:
            if not batch.rows:
                continue
            rows = tuple(FrozenRow(line.row.value, line.classification,
                                   make_row_origin(batch.identity, line.row.position))
                         for line in batch.rows)
            ref = ensure_volume(storage, encode_volume_body(rows))
            entries.append(VolumeEntry(batch.identity, ref, len(rows),
                                       rows[0].origin.position, rows[-1].origin.position))

        manifest = build_manifest(report.day, report.rule.revision, selected, entries)
        book = PublishedDaybook(manifest, report.totals,
                                make_publication_receipt(number, manifest))
        books.insert(book)
        return book
