from dataclasses import dataclass
from hashlib import sha256

from lake import DataIntegrityError
from lake.custody.storage import VolumeStorage


@dataclass(frozen=True)
class VolumeRef:
    volume_id: str
    byte_length: int
    digest: str


def fingerprint_volume(body: bytes) -> VolumeRef:
    digest = sha256(body).hexdigest()
    return VolumeRef(f'sha256:{digest}', len(body), digest)


def matches_volume(ref: VolumeRef, body: bytes | None) -> bool:
    return (isinstance(body, bytes) and len(body) == ref.byte_length
            and sha256(body).hexdigest() == ref.digest)


def ensure_volume(storage: VolumeStorage, body: bytes) -> VolumeRef:
    ref = fingerprint_volume(body)
    volume_id = ref.volume_id
    stored = storage.get_volume(volume_id)
    if stored is None:
        storage.insert_online(volume_id, ref.byte_length, ref.digest, body)
        return ref
    if VolumeRef(*stored) != ref:
        raise DataIntegrityError('existing volume metadata differs from the prepared body')
    if storage.get_hot_bytes(volume_id) == body:
        return ref
    for target in storage.list_verified_targets(volume_id):
        if storage.get_cold_bytes(volume_id, target) == body:
            return ref
    raise DataIntegrityError('existing volume has no complete matching body')


def read_online_volume(storage: VolumeStorage, ref: VolumeRef) -> bytes | None:
    body = storage.get_hot_bytes(ref.volume_id)
    return body if matches_volume(ref, body) else None
