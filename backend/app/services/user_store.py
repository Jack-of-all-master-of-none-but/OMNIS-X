import copy
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


USERS_FILE = Path(
    os.getenv(
        "OMNIS_USERS_FILE",
        Path(__file__).resolve().parents[1] / "data" / "users.json",
    )
)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class UserStoreError(Exception):
    pass


class DuplicateUserError(UserStoreError):
    pass


class InvalidUserDataError(UserStoreError):
    pass


def empty_user_state():
    return {"version": 1, "users": []}


def normalize_email(email):
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def _thread_lock(lock_path):
    key = str(lock_path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock():
    lock_path = USERS_FILE.with_suffix(USERS_FILE.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(lock_path)
    with thread_lock, lock_path.open("a+b") as lock_handle:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def validate_user_state(state):
    if not isinstance(state, dict):
        raise InvalidUserDataError("User store must contain a JSON object.")
    if state.get("version", 1) != 1:
        raise InvalidUserDataError("Unsupported user store version.")
    users = state.get("users")
    if not isinstance(users, list):
        raise InvalidUserDataError("User records must be an array.")

    user_ids = set()
    emails = set()
    for user in users:
        if not isinstance(user, dict):
            raise InvalidUserDataError("Every user must be an object.")
        user_id = user.get("id")
        email = normalize_email(user.get("email"))
        password_hash = user.get("passwordHash")
        role = user.get("role")
        created_at = user.get("createdAt")
        if not isinstance(user_id, str) or not user_id:
            raise InvalidUserDataError("Every user must have a valid ID.")
        if user_id in user_ids:
            raise InvalidUserDataError("Duplicate user IDs are not allowed.")
        user_ids.add(user_id)
        if not email:
            raise InvalidUserDataError("Every user must have a valid email.")
        if email in emails:
            raise InvalidUserDataError("Duplicate normalized emails are not allowed.")
        emails.add(email)
        if not isinstance(password_hash, str) or not password_hash:
            raise InvalidUserDataError("Every user must have a password hash.")
        if role not in ("user", "admin"):
            raise InvalidUserDataError("Every user must have a valid role.")
        if not isinstance(created_at, str) or not created_at:
            raise InvalidUserDataError("Every user must have a creation timestamp.")
    return {"version": 1, "users": users}


def _quarantine_corrupt_file():
    if not USERS_FILE.exists():
        raise InvalidUserDataError("User data file is missing.")
    digest = __import__("hashlib").sha256(USERS_FILE.read_bytes()).hexdigest()[:16]
    quarantine_path = USERS_FILE.with_name(f"{USERS_FILE.name}.corrupt-{digest}")
    if not quarantine_path.exists():
        shutil.copy2(USERS_FILE, quarantine_path)
    raise InvalidUserDataError("Invalid user data was preserved for recovery.")


def _read_state_unlocked():
    if not USERS_FILE.exists():
        return empty_user_state()
    try:
        raw = USERS_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise UserStoreError("Unable to read user storage.") from error
    if not raw.strip():
        return empty_user_state()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _quarantine_corrupt_file()
    try:
        return validate_user_state(parsed)
    except InvalidUserDataError:
        _quarantine_corrupt_file()


def load_users():
    with _file_lock():
        return _read_state_unlocked()


def _write_state_unlocked(state):
    validated = validate_user_state(state)
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_path = tempfile.mkstemp(prefix="users-", suffix=".json", dir=USERS_FILE.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as target:
            json.dump(validated, target, indent=2, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, USERS_FILE)
        if os.name != "nt":
            directory_fd = os.open(USERS_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def save_users(state):
    with _file_lock():
        _write_state_unlocked(state)


@contextmanager
def user_transaction():
    with _file_lock():
        state = _read_state_unlocked()
        original = copy.deepcopy(state)
        yield state
        validated = validate_user_state(state)
        if validated != original:
            _write_state_unlocked(validated)


def find_user_by_email(email):
    normalized = normalize_email(email)
    if not normalized:
        return None
    state = load_users()
    return next(
        (user for user in state["users"] if normalize_email(user.get("email")) == normalized),
        None,
    )


def find_user_by_id(user_id):
    if not isinstance(user_id, str):
        return None
    state = load_users()
    return next((user for user in state["users"] if user.get("id") == user_id), None)


def create_user(email, password, role="user"):
    normalized = normalize_email(email)
    if role not in ("user", "admin"):
        raise InvalidUserDataError("Invalid user role.")
    if not normalized:
        raise InvalidUserDataError("A valid email is required.")
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        raise InvalidUserDataError("Password must contain 8 to 128 characters.")

    from datetime import datetime, timezone
    from uuid import uuid4

    with user_transaction() as state:
        if any(normalize_email(user.get("email")) == normalized for user in state["users"]):
            raise DuplicateUserError("An account already exists for that email.")
        user = {
            "id": f"user-{uuid4().hex}",
            "email": normalized,
            "passwordHash": generate_password_hash(password, method="scrypt"),
            "role": role,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        state["users"].append(user)
    return public_user(user)


def public_user(user):
    if not isinstance(user, dict):
        return None
    return {
        "id": user.get("id"),
        "email": normalize_email(user.get("email")),
        "role": user.get("role", "user"),
        "createdAt": user.get("createdAt"),
    }


def verify_password(user, password):
    if not isinstance(user, dict) or not isinstance(password, str):
        return False
    password_hash = user.get("passwordHash")
    if not isinstance(password_hash, str):
        return False
    return check_password_hash(password_hash, password)
