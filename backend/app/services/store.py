import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


DATA_FILE = Path(os.getenv("OMNIS_DATA_FILE", Path(__file__).resolve().parents[1] / "data" / "incidents.json"))
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class StoreError(Exception):
    """Base exception for persistent store failures."""


class StoreCorruptionError(StoreError):
    """Raised when existing JSON cannot safely be used as store state."""


class StoreValidationError(StoreError):
    """Raised when state does not match the supported JSON structure."""


def empty_state():
    return {"version": 1, "incidents": [], "recoveryActivity": []}


def _thread_lock(lock_path):
    key = str(lock_path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock():
    lock_path = DATA_FILE.with_suffix(DATA_FILE.suffix + ".lock")
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


def _quarantine_corrupt_file(reason):
    digest = hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()[:16]
    quarantine_path = DATA_FILE.with_name(
        f"{DATA_FILE.name}.corrupt-{digest}"
    )
    try:
        if not quarantine_path.exists():
            shutil.copy2(DATA_FILE, quarantine_path)
    except OSError as error:
        raise StoreCorruptionError(
            f"Store data is invalid and could not be quarantined: {error}"
        ) from error
    raise StoreCorruptionError(
        f"Store data is invalid ({reason}); preserved copy: {quarantine_path}"
    )


def validate_state(state):
    if not isinstance(state, dict):
        raise StoreValidationError("Store state must be a JSON object.")
    if state.get("version", 1) != 1:
        raise StoreValidationError("Unsupported store data version.")

    validated = empty_state()
    validated.update(state)
    validated["version"] = 1
    if not isinstance(validated.get("incidents"), list):
        raise StoreValidationError("Store incidents must be an array.")
    if not isinstance(validated.get("recoveryActivity"), list):
        raise StoreValidationError("Store recoveryActivity must be an array.")

    incident_ids = set()
    for incident in validated["incidents"]:
        if not isinstance(incident, dict):
            raise StoreValidationError("Every incident must be an object.")
        incident_id = incident.get("id")
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise StoreValidationError("Every incident must have a non-empty string ID.")
        if incident_id in incident_ids:
            raise StoreValidationError(f"Duplicate incident ID: {incident_id}.")
        incident_ids.add(incident_id)
        if "status" in incident and incident["status"] not in ("active", "resolved"):
            raise StoreValidationError(f"Incident {incident_id} has an invalid status.")
        if "simulationResult" in incident and incident["simulationResult"] is not None and not isinstance(incident["simulationResult"], dict):
            raise StoreValidationError(f"Incident {incident_id} has invalid simulation data.")
        if "recoveryState" in incident:
            recovery_state = incident["recoveryState"]
            if not isinstance(recovery_state, dict):
                raise StoreValidationError(f"Incident {incident_id} has invalid recovery state.")
            for key in ("allocations", "restoredOperationIds", "appliedPlanIds"):
                if key in recovery_state and not isinstance(recovery_state[key], list):
                    raise StoreValidationError(f"Incident {incident_id} has invalid {key}.")
        for key in ("recoveryPlans", "rejectedOptions", "unresolvedOperations", "unmodeledAffectedDepartmentIds"):
            if key in incident and not isinstance(incident[key], list):
                raise StoreValidationError(f"Incident {incident_id} has invalid {key}.")

    if any(not isinstance(event, dict) for event in validated["recoveryActivity"]):
        raise StoreValidationError("Every recovery activity record must be an object.")
    return validated


def _read_state_unlocked():
    if not DATA_FILE.exists():
        return empty_state()
    try:
        raw = DATA_FILE.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        _quarantine_corrupt_file(error)
    except OSError as error:
        raise StoreError(f"Unable to read store data: {error}") from error
    if not raw.strip():
        return empty_state()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        _quarantine_corrupt_file(error)
    try:
        return validate_state(parsed)
    except StoreValidationError as error:
        _quarantine_corrupt_file(error)


def load_state():
    with _file_lock():
        return _read_state_unlocked()


def _write_state_unlocked(state):
    validated = validate_state(state)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_path = tempfile.mkstemp(prefix="incidents-", suffix=".json", dir=DATA_FILE.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as target:
            json.dump(validated, target, indent=2, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, DATA_FILE)
        if os.name != "nt":
            directory_fd = os.open(DATA_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def save_state(state):
    with _file_lock():
        _write_state_unlocked(state)


@contextmanager
def state_transaction():
    """Lock, load, validate, and atomically save one complete state update."""
    with _file_lock():
        state = _read_state_unlocked()
        original = copy.deepcopy(state)
        yield state
        validated = validate_state(state)
        if validated != original:
            _write_state_unlocked(validated)