from __future__ import annotations

import base64
import binascii
import hashlib
import os

import bcrypt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_CIPHER_PREFIX = "v1:"
_NONCE_BYTES = 12


def _resolve_aes256_key(master_key: str | bytes | None = None) -> bytes:
    raw = master_key if master_key is not None else get_settings().master_encryption_key
    if isinstance(raw, bytes):
        if len(raw) == 32:
            return raw
        return hashlib.sha256(raw).digest()
    if not raw:
        raise ValueError("master_encryption_key 不能为空")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str, master_key: str | bytes | None = None) -> str:
    if plaintext == "":
        return ""
    key = _resolve_aes256_key(master_key)
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    encoded = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return f"{_CIPHER_PREFIX}{encoded}"


def decrypt_secret(ciphertext: str, master_key: str | bytes | None = None) -> str:
    if ciphertext == "":
        return ""
    if not ciphertext.startswith(_CIPHER_PREFIX):
        raise ValueError("密文格式非法")
    b64_part = ciphertext[len(_CIPHER_PREFIX) :]
    try:
        blob = base64.urlsafe_b64decode(b64_part.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("密文 Base64 解码失败") from exc
    if len(blob) <= _NONCE_BYTES + 16:
        raise ValueError("密文长度不合法")
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    key = _resolve_aes256_key(master_key)
    try:
        pt = AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise ValueError("密钥不匹配或密文已被篡改") from exc
    return pt.decode("utf-8")


def mask_secret(secret: str | None) -> str | None:
    if secret is None:
        return None
    if secret == "":
        return ""
    n = len(secret)
    if n <= 4:
        return "*" * n
    if n <= 8:
        return f"{secret[:1]}****{secret[-2:]}"
    return f"{secret[:3]}****{secret[-4:]}"


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False
