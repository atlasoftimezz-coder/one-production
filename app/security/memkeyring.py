"""A keyring that lives only in this process's memory - for tests.

Never chosen on its own (priority -1). The test suite points PYTHON_KEYRING_BACKEND at it
(tests/conftest.py), so every One Production server a test starts as a subprocess inherits it
and can never read or write the owner's real Windows Credential Manager (e.g. the Pexels key).
It lives in app/ so the packaged Python (P8 tests) can load it too.
"""
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    priority = -1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")
