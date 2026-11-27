from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

SESSION_COOKIE_NAME = "nmdc_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
ROLES = frozenset({"admin", "viewer"})
_LICENSE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AuthStore:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        cookie_secure: bool | None = None,
        cookie_samesite: str | None = None,
    ) -> None:
        configured_dir = data_dir or os.getenv("HOOK_DETECTOR_DATA_DIR")
        self.data_dir = (
            Path(configured_dir).expanduser()
            if configured_dir
            else Path.home() / ".nmdc-hook-detector"
        ).resolve()
        self.path = self.data_dir / "auth.sqlite3"
        self.cookie_secure = (
            _environment_bool("NMDC_COOKIE_SECURE", default=True)
            if cookie_secure is None
            else cookie_secure
        )
        self.cookie_samesite = (
            os.getenv("NMDC_COOKIE_SAMESITE", "lax").strip().lower()
            if cookie_samesite is None
            else cookie_samesite.lower()
        )
        if self.cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("NMDC_COOKIE_SAMESITE must be lax, strict, or none.")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None cookies require NMDC_COOKIE_SECURE=1.")
        self.allowed_origins = tuple(
            origin.strip().rstrip("/")
            for origin in os.getenv("NMDC_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    license_id TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    last_login_at REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES accounts(license_id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                """
            )
        self._dummy_password_hash = self._hash_password(secrets.token_urlsafe(24))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            database = sqlite3.connect(self.path, timeout=5)
        except sqlite3.OperationalError as error:
            directory_exists = self.data_dir.is_dir()
            directory_accessible = directory_exists and os.access(
                self.data_dir,
                os.R_OK | os.W_OK | os.X_OK,
            )
            raise sqlite3.OperationalError(
                f"{error} (authentication database: {self.path}; "
                f"data directory exists: {directory_exists}; "
                f"data directory accessible: {directory_accessible})"
            ) from error
        try:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA foreign_keys = ON")
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def _normalize_license_id(license_id: Any) -> str:
        if not isinstance(license_id, str):
            raise ValueError("License ID must be text.")
        normalized = license_id.strip().lower()
        if not _LICENSE_ID_PATTERN.fullmatch(normalized):
            raise ValueError("License ID must be 3-64 letters, numbers, dots, underscores, or hyphens.")
        return normalized

    @staticmethod
    def _validate_password(password: Any) -> str:
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError("Password must be between 12 and 1024 characters.")
        return password

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
        )
        return "$".join((
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ))

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, salt_value, digest_value = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
            expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError, UnicodeError, MemoryError):
            return False

    @staticmethod
    def _public_account(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "license_id": row["license_id"],
            "role": row["role"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }

    def _insert_account(
        self,
        database: sqlite3.Connection,
        license_id: str,
        password: str,
        role: str,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_license_id(license_id)
        validated_password = self._validate_password(password)
        if role not in ROLES:
            raise ValueError("Role must be admin or viewer.")
        now = time.time()
        try:
            database.execute(
                "INSERT INTO accounts (license_id, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (normalized_id, self._hash_password(validated_password), role, now),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("That License ID already exists.") from error
        return {
            "license_id": normalized_id,
            "role": role,
            "active": True,
            "created_at": now,
            "last_login_at": None,
        }

    def create_initial_admin(self, license_id: str, password: str) -> dict[str, Any]:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            if database.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
                raise RuntimeError("An account already exists; initial Admin setup is already complete.")
            return self._insert_account(database, license_id, password, "admin")

    def create_account(self, license_id: str, password: str, role: str) -> dict[str, Any]:
        with self._connect() as database:
            return self._insert_account(database, license_id, password, role)

    def reset_password(self, license_id: str, password: str) -> bool:
        normalized_id = self._normalize_license_id(license_id)
        validated_password = self._validate_password(password)
        password_hash = self._hash_password(validated_password)
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            account = database.execute(
                "SELECT active FROM accounts WHERE license_id = ?",
                (normalized_id,),
            ).fetchone()
            if account is None:
                raise ValueError("Account not found.")
            if not account["active"]:
                raise ValueError("Account is deactivated; ask an Admin to reactivate it.")
            database.execute(
                "UPDATE accounts SET password_hash = ? WHERE license_id = ?",
                (password_hash, normalized_id),
            )
            database.execute("DELETE FROM sessions WHERE license_id = ?", (normalized_id,))
        return True

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT license_id, role, active, created_at, last_login_at FROM accounts ORDER BY license_id"
            ).fetchall()
        return [self._public_account(row) for row in rows]

    def deactivate_account(self, license_id: str) -> bool:
        normalized_id = self._normalize_license_id(license_id)
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            account = database.execute(
                "SELECT role, active FROM accounts WHERE license_id = ?",
                (normalized_id,),
            ).fetchone()
            if account is None or not account["active"]:
                return False
            if account["role"] == "admin":
                active_admins = database.execute(
                    "SELECT COUNT(*) FROM accounts WHERE role = 'admin' AND active = 1"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("Cannot deactivate the last active Admin.")
            result = database.execute(
                "UPDATE accounts SET active = 0 WHERE license_id = ? AND active = 1",
                (normalized_id,),
            )
            database.execute("DELETE FROM sessions WHERE license_id = ?", (normalized_id,))
        return result.rowcount > 0

    def login(self, license_id: Any, password: Any) -> tuple[dict[str, Any], str, str] | None:
        if not isinstance(password, str) or len(password) > 1024:
            password_to_check = ""
        else:
            password_to_check = password
        try:
            normalized_id = self._normalize_license_id(license_id)
        except ValueError:
            normalized_id = ""
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM accounts WHERE license_id = ?",
                (normalized_id,),
            ).fetchone()
        password_hash = row["password_hash"] if row else self._dummy_password_hash
        password_matches = self._verify_password(password_to_check, password_hash)
        if row is None or not row["active"] or not password_matches:
            return None

        now = time.time()
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._connect() as database:
            database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            database.execute(
                "UPDATE accounts SET last_login_at = ? WHERE license_id = ?",
                (now, normalized_id),
            )
            database.execute(
                "INSERT INTO sessions (token_hash, license_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    self._token_hash(session_token),
                    normalized_id,
                    csrf_token,
                    now,
                    now + SESSION_MAX_AGE_SECONDS,
                ),
            )
        return self._public_account(row), session_token, csrf_token

    @staticmethod
    def _token_hash(session_token: str) -> str:
        return hashlib.sha256(session_token.encode("utf-8")).hexdigest()

    def get_session(self, session_token: str | None) -> dict[str, str] | None:
        if not isinstance(session_token, str) or not session_token:
            return None
        with self._connect() as database:
            row = database.execute(
                """
                SELECT sessions.csrf_token, accounts.license_id, accounts.role
                FROM sessions
                JOIN accounts ON accounts.license_id = sessions.license_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND accounts.active = 1
                """,
                (self._token_hash(session_token), time.time()),
            ).fetchone()
            if row is None:
                database.execute(
                    "DELETE FROM sessions WHERE token_hash = ? AND expires_at <= ?",
                    (self._token_hash(session_token), time.time()),
                )
                return None
        return {
            "license_id": row["license_id"],
            "role": row["role"],
            "csrf_token": row["csrf_token"],
        }

    def logout(self, session_token: str | None) -> None:
        if isinstance(session_token, str) and session_token:
            with self._connect() as database:
                database.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (self._token_hash(session_token),),
                )

    @staticmethod
    def verify_csrf(session: dict[str, str], csrf_token: str | None) -> bool:
        return isinstance(csrf_token, str) and hmac.compare_digest(
            session["csrf_token"], csrf_token
        )


def _environment_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")