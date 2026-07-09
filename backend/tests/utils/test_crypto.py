"""AES-256-GCM encryption/decryption unit tests."""

import pytest
from cryptography.exceptions import InvalidTag

from utils.crypto import decrypt, decrypt_if_needed, encrypt, encrypt_if_needed


class TestEncryptDecrypt:
    SECRET = "test-secret-key-12345"

    def test_encrypt_roundtrip(self):
        original = "sk-my-api-key-abc123"
        encrypted = encrypt(original, self.SECRET)
        assert encrypted.startswith("ENC:") is False  # raw, no prefix
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == original

    def test_encrypt_produces_different_ciphertexts(self):
        """Each encryption uses random IV, so same input produces different output."""
        c1 = encrypt("same-text", self.SECRET)
        c2 = encrypt("same-text", self.SECRET)
        assert c1 != c2  # Random IV ensures different ciphertext

    def test_decrypt_with_wrong_secret_fails(self):
        encrypted = encrypt("sensitive-key", self.SECRET)
        with pytest.raises(InvalidTag):
            decrypt(encrypted, "wrong-secret")

    def test_decrypt_tampered_data_fails(self):
        encrypted = encrypt("some-key", self.SECRET)
        # Tamper with a byte in the middle
        raw = list(encrypted)
        raw[len(raw) // 2] = "A" if raw[len(raw) // 2] != "A" else "B"
        tampered = "".join(raw)
        with pytest.raises((InvalidTag, ValueError)):
            decrypt(tampered, self.SECRET)

    def test_encrypt_unicode_text(self):
        original = "密钥-测试-中文"
        encrypted = encrypt(original, self.SECRET)
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == original


class TestEncryptIfNeeded:
    SECRET = "my-secret"

    def test_empty_value_returns_empty(self):
        assert encrypt_if_needed("", self.SECRET) == ""

    def test_already_encrypted_passthrough(self):
        already = "ENC:abc123def456"
        result = encrypt_if_needed(already, self.SECRET)
        assert result == already

    def test_plaintext_gets_encrypted(self):
        result = encrypt_if_needed("my-api-key", self.SECRET)
        assert result.startswith("ENC:")
        assert len(result) > 4  # More than just prefix
        # Verify decryptable
        decrypted = decrypt_if_needed(result, self.SECRET)
        assert decrypted == "my-api-key"


class TestDecryptIfNeeded:
    SECRET = "my-secret"

    def test_non_enc_prefixed_passthrough(self):
        assert decrypt_if_needed("plain-key", self.SECRET) == "plain-key"

    def test_none_value(self):
        assert decrypt_if_needed("", self.SECRET) == ""

    def test_enc_prefixed_decrypts(self):
        encrypted = encrypt("api-key-123", self.SECRET)
        prefixed = f"ENC:{encrypted}"
        result = decrypt_if_needed(prefixed, self.SECRET)
        assert result == "api-key-123"

    def test_enc_prefixed_with_wrong_secret_fails(self):
        encrypted = encrypt("data", self.SECRET)
        prefixed = f"ENC:{encrypted}"
        with pytest.raises(InvalidTag):
            decrypt_if_needed(prefixed, "wrong-secret")
