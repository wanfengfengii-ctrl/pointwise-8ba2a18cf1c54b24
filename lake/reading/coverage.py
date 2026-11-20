from dataclasses import dataclass

from lake.custody.storage import VolumeStorage
from lake.custody.volumes import read_online_volume
from lake.publication.manifests import DayManifest


@dataclass(frozen=True)
class VolumeCoverage:
    bodies: tuple[bytes, ...]
    missing_volume_ids: tuple[str, ...]


def collect_coverage(db, manifest: DayManifest) -> VolumeCoverage:
    """Collect verified bytes within the reader's transaction snapshot."""
    storage = VolumeStorage(db)
    missing = {}
    bodies = []
    for entry in manifest.volumes:
        body = read_online_volume(storage, entry.volume)
        if body is None:
            missing[entry.volume.volume_id] = None
        else:
            bodies.append(body)
    if missing:
        return VolumeCoverage((), tuple(missing))
    return VolumeCoverage(tuple(bodies), ())
