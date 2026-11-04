"""Admin users: username + password, and the 90-day JWT a login hands back.

The `users` table holds the admins (jerry and feysal to start with, password `admin123`).
Passwords are stored as PBKDF2 hashes. To change one, run `deals passwd <user>` on the server,
or simply write the new password into `users.password` with sqlite3: a value that is not a hash
is taken as typed, and replaced by its hash at the next successful login.

The token is a standard HS256 JWT signed with a random key kept in `<data_dir>/jwt.key`.
It carries a fingerprint of the user's password hash, so changing the password (or deleting
the user) logs out every device that user is logged in on.
"""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from deals.db import iso

TOKEN_DAYS = 90
FIRST_ADMINS = ("jerry", "feysal")
FIRST_PASSWORD = "admin123"
_PREFIX = "pbkdf2_sha256"
_ROUNDS = 200_000  # about 0.2 s on the droplet: slow for guessing, fine for a login


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ROUNDS).hex()
    return f"{_PREFIX}${_ROUNDS}${salt}${digest}"


def check_password(stored: str, password: str) -> bool:
    if not stored.startswith(_PREFIX + "$"):  # typed into the database by hand
        return hmac.compare_digest(stored.encode(), password.encode())
    _, rounds, salt, digest = stored.split("$")
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(got, digest)


def fingerprint(stored: str) -> str:
    return hashlib.sha256(stored.encode()).hexdigest()[:16]


def ensure_users(conn: sqlite3.Connection, now: datetime) -> None:
    """The first start with the users table creates the two admins."""
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    with conn:
        for name in FIRST_ADMINS:
            conn.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                         (name, hash_password(FIRST_PASSWORD), iso(now)))


def set_password(conn: sqlite3.Connection, username: str, password: str, now: datetime) -> bool:
    """Set a user's password, adding the user if new. True if the user was new."""
    new = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None
    with conn:
        conn.execute("""INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET password = excluded.password""",
                     (username, hash_password(password), iso(now)))
    return new


def login(conn: sqlite3.Connection, username: str, password: str, now: datetime) -> tuple[str, str] | None:
    """(username, stored password hash) if username and password are right, else None."""
    row = conn.execute("SELECT username, password FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if row is None or not check_password(row["password"], password):
        return None
    name, stored = row["username"], row["password"]
    with conn:
        if not stored.startswith(_PREFIX + "$"):  # a password set by hand: keep only its hash from now on
            stored = hash_password(password)
            conn.execute("UPDATE users SET password = ? WHERE username = ?", (stored, name))
        conn.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (iso(now), name))
    return name, stored


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Tokens:
    """Signs and checks the admins' JWTs. Checking looks the user up again (cached for
    a few seconds), so a changed password or a deleted user stops old tokens."""

    CACHE_SECONDS = 10

    def __init__(self, data_dir: Path, db_path: Path):
        key_file = data_dir / "jwt.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(48))
            key_file.chmod(0o600)
        self.key = key_file.read_text().strip().encode()
        self.db_path = db_path
        self._users: dict[str, str] = {}
        self._loaded = 0.0
        self._lock = threading.Lock()

    def issue(self, username: str, stored_password: str, now: datetime) -> tuple[str, datetime]:
        expires = now + timedelta(days=TOKEN_DAYS)
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        claims = _b64(json.dumps({"sub": username, "pwd": fingerprint(stored_password),
                                  "iat": int(now.timestamp()), "exp": int(expires.timestamp())}).encode())
        signature = _b64(hmac.new(self.key, f"{header}.{claims}".encode(), hashlib.sha256).digest())
        self.forget()
        return f"{header}.{claims}.{signature}", expires

    def user(self, token: str, now: datetime) -> str | None:
        """The username a valid, unexpired token belongs to, else None."""
        try:
            header, claims, signature = token.split(".")
            want = _b64(hmac.new(self.key, f"{header}.{claims}".encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(want, signature):
                return None
            data = json.loads(_unb64(claims))
        except ValueError:
            return None
        if data.get("exp", 0) < now.timestamp():
            return None
        name = data.get("sub")
        return name if self._fingerprints().get(name) == data.get("pwd") else None

    def forget(self) -> None:
        self._loaded = 0.0

    def _fingerprints(self) -> dict[str, str]:
        with self._lock:
            if time.monotonic() - self._loaded > self.CACHE_SECONDS:
                c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
                try:
                    self._users = {name: fingerprint(pw) for name, pw in c.execute("SELECT username, password FROM users")}
                finally:
                    c.close()
                self._loaded = time.monotonic()
            return self._users
