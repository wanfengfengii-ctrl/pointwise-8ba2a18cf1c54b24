from lake import require_name, transaction
from lake.custody.inspection import InspectionRecord, validate_inspection
from lake.custody.storage import VolumeStorage
from lake.custody.volumes import VolumeRef


def retire_volume(db, volume_id: str, record: InspectionRecord) -> VolumeRef:
    require_name(volume_id, 'volume identity')
    storage = VolumeStorage(db)
    with transaction(db, write=True):
        meta = storage.get_volume(volume_id)
        if meta is None:
            raise KeyError(volume_id)
        ref = VolumeRef(*meta)
        validate_inspection(storage, ref, record)
        storage.delete_hot(volume_id)
        return ref
