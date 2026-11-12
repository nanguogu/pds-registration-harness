from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pds_harness.domain.states import WorkflowState, ensure_transition


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    program_id: int
    program_code: str
    state: WorkflowState
    context: dict[str, Any]
    last_error: str | None
    created_at: str
    updated_at: str


class SQLiteWorkflowStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    program_id INTEGER NOT NULL,
                    program_code TEXT NOT NULL,
                    state TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_program "
                "ON workflow_runs(program_id, updated_at DESC)"
            )

    def create_run(
        self,
        *,
        program_id: int,
        program_code: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        now = datetime.now(UTC).isoformat()
        run = WorkflowRun(
            run_id=str(uuid.uuid4()),
            program_id=program_id,
            program_code=program_code,
            state=WorkflowState.DISCOVERED,
            context=context or {},
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.program_id,
                    run.program_code,
                    run.state.value,
                    json.dumps(run.context, ensure_ascii=False),
                    run.last_error,
                    run.created_at,
                    run.updated_at,
                ),
            )
        return run

    def get_run(self, run_id: str) -> WorkflowRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._from_row(row)

    def transition(
        self,
        run_id: str,
        target: WorkflowState,
        *,
        context: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> WorkflowRun:
        current = self.get_run(run_id)
        ensure_transition(current.state, target)
        now = datetime.now(UTC).isoformat()
        next_context = current.context if context is None else context
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_runs
                SET state = ?, context_json = ?, last_error = ?, updated_at = ?
                WHERE run_id = ? AND state = ?
                """,
                (
                    target.value,
                    json.dumps(next_context, ensure_ascii=False),
                    last_error,
                    now,
                    run_id,
                    current.state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Workflow run changed concurrently")
        return self.get_run(run_id)

    def find_resumable(self, program_id: int) -> list[WorkflowRun]:
        terminal = (WorkflowState.SUBMITTED.value,)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_runs
                WHERE program_id = ? AND state NOT IN (?)
                ORDER BY updated_at DESC
                """,
                (program_id, *terminal),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            run_id=row["run_id"],
            program_id=row["program_id"],
            program_code=row["program_code"],
            state=WorkflowState(row["state"]),
            context=json.loads(row["context_json"]),
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

