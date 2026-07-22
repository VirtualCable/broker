import dataclasses


@dataclasses.dataclass
class TunnelMaterial:
    key_payload: bytes  # 32 bytes
    key_send: bytes  # 32 bytes
    key_receive: bytes  # 32 bytes
    nonce_payload: bytes  # 12 bytes
