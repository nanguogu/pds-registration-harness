from pds_harness.services.archive import ProjectArchive, safe_filename


def test_archives_content_by_hash(tmp_path):
    archive = ProjectArchive(tmp_path)
    first = archive.store_bytes(
        program_code="STS1260001",
        original_name="用户手册?.pdf",
        content=b"%PDF-example",
    )
    second = archive.store_bytes(
        program_code="STS1260001",
        original_name="用户手册?.pdf",
        content=b"%PDF-example",
    )
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert "?" not in first.path


def test_sanitizes_windows_reserved_names():
    assert safe_filename("CON") == "_CON"


def test_archives_parsed_json_by_original_manual_hash(tmp_path):
    archive = ProjectArchive(tmp_path)
    digest = "a" * 64
    path = archive.store_parsed_manual(
        program_code="STS1260001",
        manual_sha256=digest,
        payload={"schema_version": "1.0", "text": "中文"},
    )
    repeated = archive.store_parsed_manual(
        program_code="STS1260001",
        manual_sha256=digest,
        payload={"schema_version": "1.0", "text": "中文"},
    )

    assert path == repeated
    assert path.endswith(f"{digest}.parsed.json")
    assert "中文" in open(path, encoding="utf-8").read()

