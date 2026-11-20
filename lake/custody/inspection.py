from dataclasses import dataclass

from lake import DataIntegrityError
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import VolumeRef, matches_volume


@dataclass(frozen=True)
class InspectionRecord:
    volume_id: str
    target: str
    byte_length: int
    digest: str


def verify_cold_copy(storage: VolumeStorage, ref: VolumeRef, target: str) -> InspectionRecord:
    body = storage.get_cold_bytes(ref.volume_id, target)
    if not matches_volume(ref, body):
        raise DataIntegrityError('cold target has missing or mismatched bytes')
    return InspectionRecord(ref.volume_id, target, ref.byte_length, ref.digest)


def validate_inspection(storage: VolumeStorage, ref: VolumeRef, record: InspectionRecord):
    if not isinstance(record, InspectionRecord) or record.volume_id != ref.volume_id:
        raise DataIntegrityError('inspection belongs to a different volume')
    saved = storage.get_inspection(ref.volume_id, record.target)
    if saved is None or InspectionRecord(*saved) != record:
        raise DataIntegrityError('inspection record does not match the stored record')
    if verify_cold_copy(storage, ref, record.target) != record:
        raise DataIntegrityError('inspection differs from current verified evidence')
