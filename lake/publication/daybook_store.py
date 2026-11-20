from dataclasses import dataclass
import json

from lake import decode_integer, encode_integer
from lake.custody.volumes import VolumeRef
from lake.intake.identity import BatchIdentity
from lake.publication.manifests import DayManifest, VolumeEntry, build_manifest
from lake.publication.receipts import PublicationReceipt
from lake.quality.station_totals import DayTotals, StationTotal


@dataclass(frozen=True)
class PublishedDaybook:
    manifest: DayManifest
    totals: DayTotals
    receipt: PublicationReceipt


def _encode_totals(totals: DayTotals) -> str:
    return json.dumps({
        'stations': [(line.station, encode_integer(line.liters), line.missing,
                      line.out_of_range) for line in totals.stations],
        'liters': encode_integer(totals.liters),
        'missing': totals.missing,
        'out_of_range': totals.out_of_range,
    }, separators=(',', ':'))


def _decode_totals(text: str) -> DayTotals:
    data = json.loads(text)
    stations = tuple(StationTotal(name, decode_integer(liters), missing, outside)
                     for name, liters, missing, outside in data['stations'])
    return DayTotals(stations, decode_integer(data['liters']), data['missing'],
                     data['out_of_range'])


class DaybookStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS daybooks (
                number TEXT PRIMARY KEY NOT NULL,
                day TEXT NOT NULL,
                revision TEXT NOT NULL REFERENCES rules(revision),
                totals TEXT NOT NULL,
                receipt_rows INTEGER NOT NULL,
                receipt_code TEXT NOT NULL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS daybook_selection (
                book TEXT NOT NULL REFERENCES daybooks(number),
                ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                station TEXT NOT NULL,
                day TEXT NOT NULL,
                batch_number TEXT NOT NULL,
                volume_id TEXT REFERENCES volumes(volume_id),
                rows INTEGER NOT NULL CHECK (rows >= 0),
                first_position INTEGER,
                last_position INTEGER,
                PRIMARY KEY (book, ordinal),
                UNIQUE (book, station, day, batch_number),
                FOREIGN KEY (station, day, batch_number) REFERENCES batches,
                CHECK ((volume_id IS NULL AND rows = 0 AND first_position IS NULL
                        AND last_position IS NULL)
                    OR (volume_id IS NOT NULL AND rows > 0 AND first_position IS NOT NULL
                        AND last_position IS NOT NULL))
            )
        ''')
        self.db.execute('''
            CREATE INDEX IF NOT EXISTS daybook_volume_refs ON daybook_selection (volume_id)
        ''')

    def get(self, number: str) -> PublishedDaybook | None:
        header = self.db.execute('''
            SELECT day, revision, totals, receipt_rows, receipt_code
            FROM daybooks WHERE number = ?
        ''', (number,)).fetchone()
        if header is None:
            return None
        day, revision, encoded_totals, receipt_rows, receipt_code = header
        rows = self.db.execute('''
            SELECT s.station, s.day, s.batch_number, s.volume_id, s.rows,
                   s.first_position, s.last_position, v.byte_length, v.digest
            FROM daybook_selection AS s
            LEFT JOIN volumes AS v ON v.volume_id = s.volume_id
            WHERE s.book = ? ORDER BY s.ordinal
        ''', (number,))
        selected = []
        entries = []
        for station, source_day, batch_number, volume_id, count, first, last, size, digest in rows:
            batch = BatchIdentity(station, source_day, batch_number)
            selected.append(batch)
            if volume_id is not None:
                entries.append(VolumeEntry(batch, VolumeRef(volume_id, size, digest),
                                           count, first, last))
        manifest = build_manifest(day, revision, selected, entries)
        return PublishedDaybook(manifest, _decode_totals(encoded_totals),
                                PublicationReceipt(number, receipt_rows, receipt_code))

    def insert(self, book: PublishedDaybook):
        manifest = book.manifest
        receipt = book.receipt
        self.db.execute('''
            INSERT INTO daybooks (number, day, revision, totals, receipt_rows, receipt_code)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (receipt.number, manifest.day, manifest.revision, _encode_totals(book.totals),
              receipt.row_count, receipt.code))
        by_batch = {entry.batch: entry for entry in manifest.volumes}
        selection_rows = []
        for ordinal, batch in enumerate(manifest.batches, start=1):
            entry = by_batch.get(batch)
            volume_fields = (None, 0, None, None)
            if entry is not None:
                volume_fields = (entry.volume.volume_id, entry.row_count,
                                 entry.first_position, entry.last_position)
            selection_rows.append((receipt.number, ordinal, *batch.key(), *volume_fields))
        self.db.executemany('''
            INSERT INTO daybook_selection
                (book, ordinal, station, day, batch_number, volume_id, rows,
                 first_position, last_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', selection_rows)

    def references_volume(self, volume_id: str) -> bool:
        row = self.db.execute('''
            SELECT 1 FROM daybook_selection WHERE volume_id = ? LIMIT 1
        ''', (volume_id,)).fetchone()
        return row is not None
