"""
GhostCFO Basic Tests.
"""

import base64

import pytest
from cryptography.fernet import Fernet

from ghostcfo.security.encryption import (
    batch_decrypt_transactions,
    decrypt_field,
    decrypt_transaction,
    encrypt_field,
    encrypt_transaction,
)


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set a dummy master key for tests."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GHOSTCFO_ENCRYPTION_MASTER_KEY", key)


def test_field_encryption():
    user_id = "user_123"
    plaintext = "Swiggy Order #99"
    
    ciphertext = encrypt_field(user_id, plaintext)
    assert ciphertext != plaintext
    assert isinstance(ciphertext, str)
    
    decrypted = decrypt_field(user_id, ciphertext)
    assert decrypted == plaintext


def test_transaction_encryption():
    user_id = "user_456"
    txn = {
        "amount": 500,
        "description": "UPI/123/ACME",
        "counterparty": "Acme Corp",
        "category": "food",
        "is_encrypted": False
    }
    
    encrypted = encrypt_transaction(user_id, dict(txn))
    assert encrypted["is_encrypted"] is True
    assert encrypted["description"] != "UPI/123/ACME"
    assert encrypted["counterparty"] != "Acme Corp"
    assert encrypted["amount"] == 500 # Not encrypted
    
    decrypted = decrypt_transaction(user_id, encrypted)
    assert decrypted["is_encrypted"] is False
    assert decrypted["description"] == "UPI/123/ACME"
    assert decrypted["counterparty"] == "Acme Corp"


def test_cross_user_isolation():
    """Ensure user A cannot decrypt user B's data."""
    ciphertext = encrypt_field("user_A", "Secret Salary")
    
    # User B tries to decrypt
    decrypted = decrypt_field("user_B", ciphertext)
    assert decrypted == "[DECRYPTION_ERROR]"
