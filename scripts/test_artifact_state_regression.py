from pathlib import Path

import plamen_driver as D


def _substantial(name: str) -> str:
    return "# " + name + "\n\n" + ("substantial artifact content " * 30) + "\n"


def test_artifact_recovery_does_not_blame_phase_for_preexisting_future_files(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "rag_validation.md").write_text(_substantial("rag"), encoding="utf-8")
    # These are legitimate dirty-scratchpad artifacts from a later phase in a
    # prior run. Recovery must not treat presence alone as rag_sweep overreach.
    for name in (
        "hypotheses.md",
        "finding_mapping.md",
        "findings_inventory_deduped.md",
        "verification_queue.md",
    ):
        (scratchpad / name).write_text(_substantial(name), encoding="utf-8")

    offenders = D._existing_later_phase_artifacts(
        scratchpad,
        str(project),
        D.SC_PHASES,
        "rag_sweep",
        "sc",
    )

    assert offenders == []
    assert (scratchpad / "hypotheses.md").exists()
    assert (scratchpad / "verification_queue.md").exists()


def test_containment_still_detects_future_files_written_by_current_attempt(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    before = D._snapshot_file_state(scratchpad, str(project))

    (scratchpad / "rag_validation.md").write_text(_substantial("rag"), encoding="utf-8")
    (scratchpad / "hypotheses.md").write_text(_substantial("future"), encoding="utf-8")

    offenders = D._detect_foreign_phase_writes(
        scratchpad,
        str(project),
        D.SC_PHASES,
        "rag_sweep",
        "sc",
        before,
    )

    assert offenders == ["hypotheses.md"]


def test_phase_artifact_state_records_owner_and_quarantine_status(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "rag_validation.md").write_text(_substantial("rag"), encoding="utf-8")

    recorded = D._record_phase_artifact_state(
        scratchpad,
        str(project),
        D.SC_PHASES,
        "rag_sweep",
        "sc",
    )
    state = D._read_artifact_state(scratchpad)

    assert recorded == ["rag_validation.md"]
    assert state["artifacts"]["rag_validation.md"]["owner_phase"] == "rag_sweep"
    assert state["artifacts"]["rag_validation.md"]["status"] == "ACTIVE"
    assert state["artifacts"]["rag_validation.md"]["size"] > 100

    before = D._snapshot_file_state(scratchpad, str(project))
    (scratchpad / "hypotheses.md").write_text(_substantial("future"), encoding="utf-8")
    offenders = D._detect_foreign_phase_writes(
        scratchpad,
        str(project),
        D.SC_PHASES,
        "rag_sweep",
        "sc",
        before,
    )
    moved = D._quarantine_foreign_phase_writes(
        scratchpad,
        str(project),
        "rag_sweep",
        offenders,
    )
    state = D._read_artifact_state(scratchpad)

    assert moved == ["hypotheses.md"]
    assert state["artifacts"]["hypotheses.md"]["status"] == "QUARANTINED"
    assert state["artifacts"]["hypotheses.md"]["quarantined_by_phase"] == "rag_sweep"

