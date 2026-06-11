"""
GhostCFO Encryption Layer -- Per-user Fernet encryption via HKDF.

Design:
  - Master key stored in env (GHOSTCFO_ENCRYPTION_MASTER_KEY)
  - Per-user key derived via HKDF(master_key, salt=user_id)
  - Compromise of one user's key does not expose others
  - Encrypted fields: description, counterparty, cleaned_description, raw_source_text
  - Stored as base64 in PostgreSQL, decrypted only in application layer
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from loguru import logger

from ghostcfo.config import get_settings

# Fields that must be encrypted at rest
ENCRYPTED_FIELDS = frozenset({
    "description",
    "counterparty",
    "cleaned_description",
    "raw_source_text",
})


def _derive_user_key(user_id: str) -> bytes:
    """
    Derive a per-user Fernet key from the master key + user_id.

    Uses HKDF (HMAC-based Key Derivation Function) with SHA-256.
    The user_id acts as the salt, ensuring each user gets a unique key.
    """
    settings = get_settings()
    master_key = settings.ghostcfo_encryption_master_key

    if not master_key:
        raise ValueError(
            "GHOSTCFO_ENCRYPTION_MASTER_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    # Decode master key from base64 (Fernet keys are base64-encoded)
    try:
        master_bytes = base64.urlsafe_b64decode(master_key)
    except Exception:
        master_bytes = master_key.encode()

    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=user_id.encode("utf-8"),
        info=b"ghostcfo-transaction-encryption",
    )
    derived = hkdf.derive(master_bytes)
    # Fernet requires base64url-encoded 32-byte key
    return base64.urlsafe_b64encode(derived)


@lru_cache(maxsize=128)
def _get_fernet(user_id: str) -> Fernet:
    """Get cached Fernet instance for a user."""
    key = _derive_user_key(user_id)
    return Fernet(key)


def encrypt_field(user_id: str, plaintext: str) -> str:
    """
    Encrypt a single field value.

    Returns base64-encoded ciphertext suitable for PostgreSQL TEXT column.
    """
    if not plaintext:
        return ""
    f = _get_fernet(user_id)
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")  # Already base64


def decrypt_field(user_id: str, ciphertext: str) -> str:
    """
    Decrypt a single field value.

    Returns plaintext string. On decryption failure, returns "[DECRYPTION_ERROR]".
    """
    if not ciphertext:
        return ""
    try:
        f = _get_fernet(user_id)
        plaintext = f.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken:
        logger.error("Decryption failed for user={} (invalid token or wrong key)", user_id)
        return "[DECRYPTION_ERROR]"
    except Exception as exc:
        logger.error("Decryption error for user={}: {}", user_id, exc)
        return "[DECRYPTION_ERROR]"


def encrypt_transaction(user_id: str, data: dict) -> dict:
    """
    Encrypt sensitive fields in a transaction dict.

    Modifies in-place and returns the dict with encrypted values.
    Sets is_encrypted = True.
    """
    for field_name in ENCRYPTED_FIELDS:
        if field_name in data and data[field_name]:
            data[field_name] = encrypt_field(user_id, str(data[field_name]))
    data["is_encrypted"] = True
    return data


def decrypt_transaction(user_id: str, data: dict) -> dict:
    """
    Decrypt sensitive fields in a transaction dict.

    Modifies in-place and returns the dict with plaintext values.
    Sets is_encrypted = False.
    """
    if not data.get("is_encrypted", False):
        return data

    for field_name in ENCRYPTED_FIELDS:
        if field_name in data and data[field_name]:
            data[field_name] = decrypt_field(user_id, data[field_name])
    data["is_encrypted"] = False
    return data


def batch_decrypt_transactions(user_id: str, transactions: list[dict]) -> list[dict]:
    """
    Decrypt a batch of transaction dicts efficiently.

    Reuses the same Fernet instance (cached per user_id).
    """
    # Warm cache
    _get_fernet(user_id)
    return [decrypt_transaction(user_id, t) for t in transactions]


def rotate_master_key(
    old_master_key: str,
    new_master_key: str,
    user_id: str,
    encrypted_data: list[dict],
) -> list[dict]:
    """
    Re-encrypt data when the master key is rotated.

    Decrypt with old key, re-encrypt with new key.
    """
    # Clear cache for this user
    _get_fernet.cache_clear()

    # Temporarily override settings is complex; instead, manually derive
    old_hkdf = HKDF(
        algorithm=SHA256(), length=32,
        salt=user_id.encode(), info=b"ghostcfo-transaction-encryption",
    )
    try:
        old_master_bytes = base64.urlsafe_b64decode(old_master_key)
    except Exception:
        old_master_bytes = old_master_key.encode()
    old_derived = base64.urlsafe_b64encode(old_hkdf.derive(old_master_bytes))
    old_fernet = Fernet(old_derived)

    new_hkdf = HKDF(
        algorithm=SHA256(), length=32,
        salt=user_id.encode(), info=b"ghostcfo-transaction-encryption",
    )
    try:
        new_master_bytes = base64.urlsafe_b64decode(new_master_key)
    except Exception:
        new_master_bytes = new_master_key.encode()
    new_derived = base64.urlsafe_b64encode(new_hkdf.derive(new_master_bytes))
    new_fernet = Fernet(new_derived)

    re_encrypted = []
    for data in encrypted_data:
        for field_name in ENCRYPTED_FIELDS:
            if field_name in data and data[field_name]:
                try:
                    plaintext = old_fernet.decrypt(data[field_name].encode())
                    data[field_name] = new_fernet.encrypt(plaintext).decode()
                except Exception as exc:
                    logger.error("Key rotation failed for field {}: {}", field_name, exc)
        re_encrypted.append(data)

    return re_encrypted
