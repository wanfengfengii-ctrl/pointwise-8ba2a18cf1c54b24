from dataclasses import dataclass

from lake import DataIntegrityError
from lake.publication.body import decode_volume_body
from lake.publication.manifests import DayManifest
from lake.publication.provenance import RowOrigin
from lake.quality.classify import Classification
from lake.reading.coverage import VolumeCoverage


@dataclass(frozen=True)
class ReadDetail:
    volume_id: str
    value: int | None
    classification: Classification
    origin: RowOrigin


def assemble_details(manifest: DayManifest, coverage: VolumeCoverage) -> tuple[ReadDetail, ...]:
    if coverage.missing_volume_ids or len(coverage.bodies) != len(manifest.volumes):
        raise DataIntegrityError('whole manifest coverage is required to assemble details')
    details = []
    for entry, body in zip(manifest.volumes, coverage.bodies, strict=True):
        rows = decode_volume_body(body)
        if len(rows) != entry.row_count:
            raise DataIntegrityError('volume row count differs from the fixed manifest')
        details.extend(ReadDetail(entry.volume.volume_id, row.value, row.classification, row.origin)
                       for row in rows)
    return tuple(details)
