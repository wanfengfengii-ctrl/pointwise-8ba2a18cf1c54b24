from dataclasses import dataclass
from hashlib import sha256
import json

from lake.publication.manifests import DayManifest


@dataclass(frozen=True)
class PublicationReceipt:
    number: str
    row_count: int
    code: str


def make_publication_receipt(number: str, manifest: DayManifest) -> PublicationReceipt:
    rows = sum(entry.row_count for entry in manifest.volumes)
    volumes = [(entry.volume.volume_id, entry.first_position, entry.last_position)
               for entry in manifest.volumes]
    facts = ('daybook', number, manifest.day, manifest.revision,
             [batch.key() for batch in manifest.batches], volumes)
    payload = json.dumps(facts, separators=(',', ':')).encode('ascii')
    return PublicationReceipt(number, rows, sha256(payload).hexdigest())
