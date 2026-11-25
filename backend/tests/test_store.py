import multiprocessing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.services import store


def append_incident_in_process(data_file, incident_id):
    with patch.object(store, "DATA_FILE", Path(data_file)):
        with store.state_transaction() as state:
            state["incidents"].append({"id": incident_id})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temporary_directory.name) / "incidents.json"
        self.data_file_patch = patch.object(store, "DATA_FILE", self.data_file)
        self.data_file_patch.start()

    def tearDown(self):
        self.data_file_patch.stop()
        self.temporary_directory.cleanup()

    def test_missing_and_empty_files_return_empty_state(self):
        self.assertEqual(store.load_state(), store.empty_state())
        self.data_file.write_text(" \n\t", encoding="utf-8")
        self.assertEqual(store.load_state(), store.empty_state())

    def test_corrupt_json_is_preserved_and_quarantined(self):
        original = b'{"incidents": ['
        self.data_file.write_bytes(original)

        with self.assertRaises(store.StoreCorruptionError):
            store.load_state()
        with self.assertRaises(store.StoreCorruptionError):
            store.load_state()

        self.assertEqual(self.data_file.read_bytes(), original)
        quarantine_files = list(self.data_file.parent.glob("incidents.json.corrupt-*"))
        self.assertEqual(len(quarantine_files), 1)
        self.assertEqual(quarantine_files[0].read_bytes(), original)

    def test_invalid_utf8_and_invalid_schema_are_not_discarded(self):
        for contents in (b"\xff", b"[]", b'{"incidents": "not-an-array"}'):
            with self.subTest(contents=contents):
                self.data_file.write_bytes(contents)
                with self.assertRaises(store.StoreCorruptionError):
                    store.load_state()
                self.assertEqual(self.data_file.read_bytes(), contents)
        self.assertEqual(len(list(self.data_file.parent.glob("incidents.json.corrupt-*"))), 3)

    def test_invalid_state_is_rejected_without_overwriting_existing_data(self):
        original = b'{"version": 1, "incidents": [], "recoveryActivity": []}\n'
        self.data_file.write_bytes(original)
        invalid_state = {
            "version": 1,
            "incidents": [{"id": "duplicate"}, {"id": "duplicate"}],
            "recoveryActivity": [],
        }

        with self.assertRaises(store.StoreValidationError):
            store.save_state(invalid_state)

        self.assertEqual(self.data_file.read_bytes(), original)
        self.assertEqual(list(self.data_file.parent.glob("incidents-*.json")), [])

    def test_concurrent_thread_and_process_transactions_keep_all_updates(self):
        def append_thread_entries(worker):
            for index in range(6):
                with store.state_transaction() as state:
                    state["incidents"].append({"id": f"thread-{worker}-{index}"})

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(append_thread_entries, range(6)))

        process_context = multiprocessing.get_context("spawn")
        processes = [
            process_context.Process(
                target=append_incident_in_process,
                args=(str(self.data_file), f"process-{index}"),
            )
            for index in range(4)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            self.assertFalse(process.is_alive(), "A writer process did not finish.")
            self.assertEqual(process.exitcode, 0)

        saved = store.load_state()
        self.assertEqual(len(saved["incidents"]), 40)
        self.assertEqual(len({item["id"] for item in saved["incidents"]}), 40)

    def test_failed_transaction_does_not_commit_partial_state(self):
        with self.assertRaisesRegex(RuntimeError, "abort transaction"):
            with store.state_transaction() as state:
                state["incidents"].append({"id": "not-committed"})
                raise RuntimeError("abort transaction")

        self.assertEqual(store.load_state(), store.empty_state())


if __name__ == "__main__":
    unittest.main()