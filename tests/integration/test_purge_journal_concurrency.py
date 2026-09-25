import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kernel.purge.journal import IndependentPurgeJournal


def _append_from_process(path, data_root, suffix, start_event, result_queue):
    try:
        start_event.wait(timeout=10)
        row = IndependentPurgeJournal(path, data_root).append(
            action="BARRIER_INSTALLED",
            barrier_id="process-barrier-" + suffix,
            plan_id="process-plan-" + suffix,
            plan_hash=("a" if suffix == "a" else "b") * 64,
            lineage_revision=1,
            protected_refs=["process-object-" + suffix],
        )
        result_queue.put(("ok", row["sequence"]))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        result_queue.put(("error", type(exc).__name__, str(exc)))


class IndependentPurgeJournalConcurrencyTests(unittest.TestCase):
    def test_distinct_instances_serialize_append_and_preserve_hash_chain(self):
        with tempfile.TemporaryDirectory(prefix="nexus-purge-journal-lock-") as temporary:
            root = Path(temporary)
            path = root / "independent" / "purge.jsonl"
            data_root = root / "data"
            journal_a = IndependentPurgeJournal(path, data_root)
            journal_b = IndependentPurgeJournal(path, data_root)

            for round_no in range(12):
                gate = threading.Barrier(3)

                def append(journal, suffix):
                    gate.wait(timeout=5)
                    return journal.append(
                        action="BARRIER_INSTALLED",
                        barrier_id=f"barrier-{round_no}-{suffix}",
                        plan_id=f"plan-{round_no}-{suffix}",
                        plan_hash=(f"{round_no:064x}" if suffix == "a" else f"{round_no + 100:064x}"),
                        lineage_revision=round_no,
                        protected_refs=[f"object-{round_no}-{suffix}"],
                    )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(append, journal_a, "a"), pool.submit(append, journal_b, "b")]
                    gate.wait(timeout=5)
                    [future.result(timeout=10) for future in futures]

                records = IndependentPurgeJournal(path, data_root).read()
                self.assertEqual([row["sequence"] for row in records], list(range(1, 2 * (round_no + 1) + 1)))
                for previous, current in zip(records, records[1:]):
                    self.assertEqual(current["previous_hash"], previous["record_hash"])
                self.assertEqual(records[-1]["sequence"], 2 * (round_no + 1))

            reopened = IndependentPurgeJournal(path, data_root)
            records = reopened.read()
            self.assertEqual(len(records), 24)
            self.assertEqual(records[-1]["sequence"], 24)
            self.assertEqual(records[-1]["previous_hash"], records[-2]["record_hash"])

    def test_separate_processes_serialize_same_path_append(self):
        with tempfile.TemporaryDirectory(prefix="nexus-purge-journal-process-lock-") as temporary:
            root = Path(temporary)
            path = root / "independent" / "purge.jsonl"
            data_root = root / "data"
            context = multiprocessing.get_context("spawn")
            start_event = context.Event()
            result_queue = context.Queue()
            workers = [context.Process(target=_append_from_process, args=(str(path), str(data_root), suffix, start_event, result_queue)) for suffix in ("a", "b")]
            for worker in workers:
                worker.start()
            start_event.set()
            results = [result_queue.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(timeout=20)
                self.assertFalse(worker.is_alive(), "journal append process did not terminate")
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual([row[0] for row in results], ["ok", "ok"])
            records = IndependentPurgeJournal(path, data_root).read()
            self.assertEqual([row["sequence"] for row in records], [1, 2])
            self.assertEqual(records[1]["previous_hash"], records[0]["record_hash"])


if __name__ == "__main__":
    unittest.main()
