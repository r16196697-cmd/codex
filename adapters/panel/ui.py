"""Tkinter-native ALPHA companion window. Kept separate from Nexus Core."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk


_MODES = ("ACTIVE", "OBSERVE", "BYPASS")
_TABS = ("OVERVIEW", "TASKS", "MEMORY", "CONTEXT", "SKILLS", "VALUE")


def launch_panel(view_model) -> None:
    root = tk.Tk()
    root.title("Nexus")
    root.minsize(780, 500)

    header = ttk.Frame(root, padding=(12, 10))
    header.pack(fill="x")
    ttk.Label(header, text="Nexus", font=("Segoe UI", 16, "bold")).pack(side="left")
    ttk.Label(header, text="Participation:").pack(side="left", padx=(22, 5))
    mode = tk.StringVar(value="UNKNOWN")
    selector = ttk.Combobox(header, state="readonly", values=_MODES, width=12, textvariable=mode)
    selector.pack(side="left")
    runtime_text = tk.StringVar(value="Runtime: UNKNOWN")
    ttk.Label(header, textvariable=runtime_text).pack(side="left", padx=18)
    scope_text = tk.StringVar(value="Scope: current Nexus data root")
    ttk.Label(root, textvariable=scope_text, padding=(14, 0)).pack(anchor="w")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=12, pady=10)
    pages = {}
    for name in _TABS:
        page = ttk.Frame(notebook, padding=12)
        notebook.add(page, text=name)
        pages[name] = page

    def fill_tree(parent, columns, rows):
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=16)
        for column in columns:
            tree.heading(column, text=column.replace("_", " ").title())
            tree.column(column, width=150, anchor="w")
        for row in rows:
            tree.insert("", "end", values=tuple(row.get(column, "UNKNOWN") for column in columns))
        tree.pack(fill="both", expand=True)
        return tree

    def render():
        snapshot = view_model.snapshot()
        mode.set(snapshot["participation_mode"])
        runtime_text.set("Runtime: " + snapshot["runtime_mode"])
        overview = snapshot["overview"]
        overview_text = (
            f"Participation: {snapshot['participation_mode']}\n"
            f"Runtime: {snapshot['runtime_mode']}\n"
            f"Tasks: {overview['task_count'] if overview['task_count'] is not None else 'UNAVAILABLE'}  |  Unfinished: {overview['unfinished_task_count'] if overview['unfinished_task_count'] is not None else 'UNAVAILABLE'}  |  Waiting: {overview['blocked_task_count'] if overview['blocked_task_count'] is not None else 'UNAVAILABLE'}\n"
            f"Active Runs: {overview['active_run_count'] if overview['active_run_count'] is not None else 'UNAVAILABLE'}  |  Pending Effects: {overview['pending_effect_count'] if overview['pending_effect_count'] is not None else 'UNAVAILABLE'}\n"
            f"Projection status: {overview['status']}\n"
            f"Scope: {overview['scope_note']}"
        )
        for child in pages["OVERVIEW"].winfo_children():
            child.destroy()
        ttk.Label(pages["OVERVIEW"], text=overview_text, justify="left").pack(anchor="w", pady=(0, 14))
        ttk.Label(pages["OVERVIEW"], text="Recent Runs").pack(anchor="w")
        fill_tree(pages["OVERVIEW"], ("run_id", "task_id", "executor_kind", "status", "created_at"), overview["recent_runs"])

        for child in pages["TASKS"].winfo_children():
            child.destroy()
        task_rows = []
        for task in snapshot["tasks"]:
            if not task["runs"] and not task["attempts"]:
                task_rows.append({"task_id": task["task_id"], "status": task["status"], "relationship": "—"})
            for run in task["runs"]:
                task_rows.append({"task_id": task["task_id"], "status": task["status"],
                                  "relationship": f"Run {run['run_id']} · {run['status']}"})
            for attempt in task["attempts"]:
                task_rows.append({"task_id": task["task_id"], "status": task["status"],
                                  "relationship": f"Attempt {attempt['attempt_id']} · {attempt['outcome']}"})
        if snapshot["query_status"] != "OBSERVED":
            ttk.Label(pages["TASKS"], text="Task projection unavailable in the current Runtime mode.").pack(anchor="w")
        fill_tree(pages["TASKS"], ("task_id", "status", "relationship"), task_rows)

        memory = snapshot["memory"]
        for child in pages["MEMORY"].winfo_children():
            child.destroy()
        ttk.Label(pages["MEMORY"], text=(
            f"Memory summary: {memory['status']}\n"
            f"Raw history rows: {memory['raw_history_count'] if memory['raw_history_count'] is not None else 'UNAVAILABLE'}\n"
            f"Admitted memory rows: {memory['admitted_count'] if memory['admitted_count'] is not None else 'UNAVAILABLE'}\n"
            "Candidate state counts (payloads hidden):"
        ), justify="left").pack(anchor="w", pady=(0, 10))
        fill_tree(pages["MEMORY"], ("status", "truth_state", "count"), memory["candidate_states"])

        for tab in ("CONTEXT", "SKILLS"):
            content = snapshot["context_status"] if tab == "CONTEXT" else snapshot["skill_status"]
            for child in pages[tab].winfo_children():
                child.destroy()
            ttk.Label(pages[tab], text=content["status"], font=("Segoe UI", 14, "bold")).pack(anchor="w")
            ttk.Label(pages[tab], text=content["detail"], wraplength=650).pack(anchor="w", pady=8)
            if tab == "CONTEXT" and content.get("pack_id"):
                counts = ", ".join(f"{key}: {value}" for key, value in sorted(content.get("selected_source_counts", {}).items())) or "none"
                summary = (
                    f"Pack: {content['pack_id']}\nTask: {content.get('task_id', 'UNAVAILABLE')}  |  Run: {content.get('run_id', 'UNAVAILABLE')}\n"
                    f"Sources: {counts} ({content.get('selected_ref_count', 0)} refs)\n"
                    f"Exact serialized size: {content.get('serialized_byte_size', 'UNAVAILABLE')} bytes\n"
                    f"Content hash: {content.get('content_hash', 'UNAVAILABLE')}\n"
                    f"Compilation: {content.get('status')} at {content.get('compiled_at', 'UNAVAILABLE')}\n"
                    f"Host delivery: {content.get('host_delivery_status', 'UNAVAILABLE')}\n"
                    f"Actual delivery observed: {content.get('delivery_observed', False)} ({content.get('actual_host_delivery', 'UNKNOWN')})\n"
                    f"Delivery Run: {content.get('delivery_run_id', 'UNAVAILABLE')}\n"
                    f"Model-visible exposure: {content.get('model_visible_exposure', 'UNKNOWN')}\n"
                    f"Provenance: {content.get('provenance', 'UNAVAILABLE')}"
                )
                ttk.Label(pages[tab], text=summary, justify="left", wraplength=700).pack(anchor="w", pady=8)

        for child in pages["VALUE"].winfo_children():
            child.destroy()
        ttk.Label(pages["VALUE"], text="Measured Nexus value only; missing Host telemetry is not zero.").pack(anchor="w", pady=(0, 10))
        value_rows = []
        for key, item in snapshot["value_metrics"].items():
            value = item["value"] if item["value"] is not None else item["provenance"]
            provenance = item["provenance"]
            if item.get("estimate_status") == "ESTIMATED":
                provenance += " / ESTIMATED"
            value_rows.append({"metric": key, "value": value, "provenance": provenance})
        fill_tree(pages["VALUE"], ("metric", "value", "provenance"), value_rows)

    def change_mode(_event=None):
        snapshot = view_model.snapshot()
        old_mode = snapshot["participation_mode"]
        new_mode = mode.get()
        if new_mode == old_mode:
            return
        if old_mode == "ACTIVE" and new_mode != "ACTIVE":
            description = {
                "OBSERVE": "Nexus will not alter Host context or select capabilities. Only permitted comparison telemetry may be recorded.",
                "BYPASS": "Use Host natively. Nexus will not influence execution or automatically ingest task/result state.",
            }[new_mode]
            if not messagebox.askyesno("Turn Nexus off for this scope?", description + "\n\nOFF does not purge persisted Nexus state.", parent=root):
                render()
                return
        try:
            view_model.set_participation_mode(new_mode, expected_mode=old_mode)
            render()
        except Exception as exc:
            reason = exc.args[0] if getattr(exc, "args", None) else type(exc).__name__
            messagebox.showerror("Participation mode unchanged", str(reason), parent=root)
            render()

    selector.bind("<<ComboboxSelected>>", change_mode)
    ttk.Button(header, text="Refresh", command=render).pack(side="right")
    render()
    root.mainloop()
