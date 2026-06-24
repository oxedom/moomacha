from cryptography.fernet import Fernet

from control_plane.services.crypto import SecretBox


def test_encrypt_decrypt_round_trip():
    key = Fernet.generate_key().decode()
    box = SecretBox(key)

    token = box.encrypt("super-secret-api-key")

    assert token != "super-secret-api-key"
    assert box.decrypt(token) == "super-secret-api-key"
