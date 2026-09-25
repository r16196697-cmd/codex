import tempfile
import unittest
import os
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from kernel.purge.journal import IndependentPurgeJournal


class IndependentPurgeJournalDurabilityTests(unittest.TestCase):
    def _journal(self, root):
        return IndependentPurgeJournal(root / "outside" / "purge.jsonl", root / "data")

    def _append(self, journal, suffix):
        return journal.append(
            action="BARRIER_INSTALLED",
            barrier_id="barrier-" + suffix,
            plan_id="plan-" + suffix,
            plan_hash="a" * 64,
            lineage_revision=1,
            protected_refs=["synthetic-object-" + suffix],
        )

    def test_fresh_windows_journal_uses_write_through_namespace_creation(self):
        with tempfile.TemporaryDirectory(prefix="nexus-journal-win-durable-") as temporary:
            root = Path(temporary)
            journal = self._journal(root)
            row = self._append(journal, "first")
            self.assertTrue(journal.path.exists())
            self.assertEqual(journal.read(), [row])
            self.assertFalse(list(journal.path.parent.glob(journal.path.name + ".*.tmp")))

    def test_fresh_posix_append_fsyncs_parent_after_file_fsync(self):
        with tempfile.TemporaryDirectory(prefix="nexus-journal-posix-durable-") as temporary:
            journal = self._journal(Path(temporary))
            self.assertFalse(journal.path.exists())
            calls = []
            real_fsync = os.fsync

            def record_file_fsync(fd):
                calls.append("file")
                return real_fsync(fd)

            def record_directory_fsync():
                calls.append("directory")

            with patch("kernel.purge.journal.os.name", "posix"), \
                 patch.object(journal, "_exclusive_path_lock", nullcontext), \
                 patch("kernel.purge.journal.os.fsync", side_effect=record_file_fsync), \
                 patch.object(journal, "_fsync_posix_parent_directory", side_effect=record_directory_fsync):
                row = self._append(journal, "first")
            self.assertEqual(row["sequence"], 1)
            self.assertEqual(calls, ["file", "directory"])
            self.assertEqual(journal.read()[0]["record_hash"], row["record_hash"])

    def test_existing_posix_journal_does_not_repeat_directory_fsync(self):
        with tempfile.TemporaryDirectory(prefix="nexus-journal-existing-") as temporary:
            journal = self._journal(Path(temporary))
            first = self._append(journal, "first")
            with patch("kernel.purge.journal.os.name", "posix"), \
                 patch.object(journal, "_exclusive_path_lock", nullcontext), \
                 patch.object(journal, "_fsync_posix_parent_directory") as fsync_parent:
                second = self._append(journal, "second")
            fsync_parent.assert_not_called()
            self.assertEqual(journal.read(), [first, second])

    def test_parent_directory_fsync_failure_does_not_report_append_success(self):
        with tempfile.TemporaryDirectory(prefix="nexus-journal-fsync-failure-") as temporary:
            journal = self._journal(Path(temporary))
            self.assertFalse(journal.path.exists())
            with patch("kernel.purge.journal.os.name", "posix"), \
                 patch.object(journal, "_exclusive_path_lock", nullcontext), \
                 patch.object(journal, "_fsync_posix_parent_directory", side_effect=OSError("injected directory fsync failure")):
                with self.assertRaisesRegex(OSError, "injected directory fsync failure"):
                    self._append(journal, "first")


if __name__ == "__main__":
    unittest.main()
