from cryptography.fernet import Fernet


class SecretBox:
    """Symmetric encryption for secrets at rest (bot API keys)."""

    def __init__(self, fernet_key: str) -> None:
        self._fernet = Fernet(fernet_key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
