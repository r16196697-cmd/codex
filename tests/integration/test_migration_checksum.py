from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.storage.migration_checksum import classify_migration_checksum, normalization_is_safe
from kernel.object.errors import MigrationError
from tests.support.test_store import open_test_store


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class MigrationChecksumTests(unittest.TestCase):
    def test_exact_and_transport_matrix(self):
        lf = b"-- comment\nCREATE TABLE example(id INTEGER);\n"
        crlf = lf.replace(b"\n", b"\r\n")
        for runtime, recorded, expected in (
            (lf, lf, "EXACT_RAW"), (crlf, crlf, "EXACT_RAW"),
            (lf, crlf, "LINE_ENDING_EQUIVALENT"), (crlf, lf, "LINE_ENDING_EQUIVALENT"),
        ):
            with self.subTest(runtime=runtime, recorded=recorded):
                self.assertEqual(classify_migration_checksum(runtime, digest(recorded)), expected)

    def test_content_and_whitespace_changes_are_not_transport(self):
        raw = b"CREATE TABLE example(id INTEGER);\n"
        for changed in (raw.replace(b"INTEGER", b"TEXT"), raw.replace(b"TABLE ", b"TABLE  "),
                        raw.replace(b" ", b"\t", 1), raw.rstrip(b"\n")):
            self.assertEqual(classify_migration_checksum(changed, digest(raw)), "MISMATCH")

    def test_unsafe_variants_fail_closed_but_exact_still_works(self):
        for raw in (
            b"SELECT 'one\ntwo';\n", b'SELECT "one\ntwo";\n',
            b"SELECT `one\ntwo`;\n", b"SELECT [one\ntwo];\n",
            b"SELECT 'escaped''one\ntwo';\n", b'SELECT "escaped""one\ntwo";\n',
            b"SELECT `escaped``one\ntwo`;\n", b"SELECT 1;\r\n-- bare\rcomment\n",
            b"SELECT '\xff';\n", b"\xef\xbb\xbfSELECT 1;\n", b"SELECT '\x00';\n",
            b"SELECT 'unterminated\n", b"/* unterminated\n",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(normalization_is_safe(raw))
                self.assertEqual(classify_migration_checksum(raw, digest(raw)), "EXACT_RAW")
                self.assertEqual(classify_migration_checksum(raw, digest(raw.replace(b"\n", b"\r\n"))), "MISMATCH")

    def test_comments_and_escaped_single_line_quotes(self):
        raw = b"/* ' \" ` [\ncomment */\n-- ' \" ` [\nSELECT 'it''s', \"a\"\"b\", `a``b`, [a];\n"
        self.assertTrue(normalization_is_safe(raw))
        self.assertEqual(classify_migration_checksum(raw, digest(raw.replace(b"\n", b"\r\n"))), "LINE_ENDING_EQUIVALENT")

    def test_all_current_migrations_safe(self):
        paths = sorted((Path(__file__).resolve().parents[2] / "migrations").glob("*.sql"))
        self.assertEqual(len(paths), 28)
        for path in paths:
            with self.subTest(migration=path.name):
                self.assertTrue(normalization_is_safe(path.read_bytes()))


class MigrationStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-migration-checksum-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "data"
        self.journal = self.base / "independent.jsonl"
        store = open_test_store(self.root, independent_purge_journal_path=self.journal)
        store.close()
        self.policy = json.loads((self.base / ".data.test-policy.json").read_bytes())
        self.migrations = self.base / "migrations"
        self.migrations.mkdir()
        sources = sorted((Path(__file__).resolve().parents[2] / "migrations").glob("*.sql"))
        # Only disposable test history is adjusted to represent mixed bootstrap transport.
        with closing(sqlite3.connect(self.root / "nexus.sqlite")) as conn:
            for version, path in enumerate(sources, 1):
                lf = path.read_bytes().replace(b"\r\n", b"\n")
                (self.migrations / path.name).write_bytes(lf)
                historical = lf.replace(b"\n", b"\r\n") if version <= 22 else lf
                conn.execute("UPDATE schema_migrations SET checksum=? WHERE version=?", (digest(historical), version))
            conn.commit()

    def history(self):
        with closing(sqlite3.connect((self.root / "nexus.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            return conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()

    def snapshot(self):
        return {p.relative_to(self.base).as_posix(): (digest(p.read_bytes()), p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.base.rglob("*") if p.is_file()}

    def open(self, read_only):
        return ObjectStore(self.root, policy=self.policy, migrations_dir=self.migrations,
                           independent_purge_journal_path=self.journal, read_only=read_only)

    def test_mixed_history_read_only_and_writer_preserve_rows(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                for p in self.migrations.glob("*.sql"):
                    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                history = self.history()
                before = self.snapshot()
                store = self.open(True)
                store.close()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.history(), history)
                store = self.open(False)
                store.close()
                self.assertEqual(self.history(), history)

    def test_true_drift_rejected_by_both_startup_paths(self):
        path = self.migrations / "0001_initial.sql"
        path.write_bytes(path.read_bytes().replace(b"CREATE TABLE", b"CREATE  TABLE", 1))
        history = self.history()
        for read_only in (True, False):
            with self.subTest(read_only=read_only):
                with self.assertRaisesRegex(MigrationError, "^MIGRATION_CHECKSUM_MISMATCH$"):
                    self.open(read_only)
                self.assertEqual(self.history(), history)


if __name__ == "__main__":
    unittest.main()
