from lake import DataIntegrityError, require_name, transaction
from lake.custody.inspection import InspectionRecord, verify_cold_copy
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import VolumeRef, read_online_volume
from lake.publication.daybook_store import DaybookStore


def archive_volume(db, volume_id: str, target: str) -> InspectionRecord:
    require_name(volume_id, 'volume identity')
    require_name(target, 'cold target')
    storage = VolumeStorage(db)
    with transaction(db, write=True):
        meta = storage.get_volume(volume_id)
        if meta is None or not DaybookStore(db).references_volume(volume_id):
            raise KeyError(volume_id)
        ref = VolumeRef(*meta)
        body = read_online_volume(storage, ref)
        if body is None:
            raise DataIntegrityError('volume has no verified online copy source')
        storage.put_cold_bytes(volume_id, target, body)
        record = verify_cold_copy(storage, ref, target)
        storage.add_inspection(record.volume_id, record.target, record.byte_length, record.digest)
        return record
