from pds_harness.domain.states import WorkflowState
from pds_harness.storage.sqlite_store import SQLiteWorkflowStore


def test_persists_and_transitions_run(tmp_path):
    store = SQLiteWorkflowStore(tmp_path / "harness.db")
    created = store.create_run(program_id=81202, program_code="STS1260318")
    updated = store.transition(created.run_id, WorkflowState.PLAN_DRAFT_CREATING)
    assert updated.state is WorkflowState.PLAN_DRAFT_CREATING
    assert store.find_resumable(81202)[0].run_id == created.run_id
