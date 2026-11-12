from __future__ import annotations

import argparse
import json
from pathlib import Path

from pds_harness.config import load_settings
from pds_harness.adapters.cloud_model import (
    CloudModelConfig,
    OpenAICompatibleModelClient,
)
from pds_harness.domain.models import FunctionCatalog
from pds_harness.services.catalog_validation import CatalogValidator
from pds_harness.services.document_parser import parse_document
from pds_harness.services.ai_generation import generate_product_summary_artifact
from pds_harness.services.function_list_import import generate_direct_import_artifacts


def _validate(path: Path) -> int:
    catalog = FunctionCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    issues = CatalogValidator().validate(catalog)
    print(json.dumps([issue.__dict__ for issue in issues], ensure_ascii=False, indent=2))
    return 1 if any(issue.severity == "error" for issue in issues) else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pds-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("catalog", type=Path)
    config_parser = subparsers.add_parser("check-config")
    config_parser.add_argument("config", type=Path)
    inspect_parser = subparsers.add_parser("inspect-document")
    inspect_parser.add_argument("document", type=Path)
    import_parser = subparsers.add_parser("import-function-list")
    import_parser.add_argument("parsed_artifact", type=Path)
    import_parser.add_argument("--archive-root", required=True, type=Path)
    summary_parser = subparsers.add_parser("generate-summary")
    summary_parser.add_argument("parsed_artifact", type=Path)
    summary_parser.add_argument("--archive-root", required=True, type=Path)
    summary_parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        return _validate(args.catalog)
    if args.command == "check-config":
        settings = load_settings(args.config)
        print(settings.model_dump_json(indent=2))
        return 0
    if args.command == "inspect-document":
        parsed = parse_document(args.document)
        print(parsed.model_dump_json(indent=2))
        return 0
    if args.command == "import-function-list":
        print(
            json.dumps(
                generate_direct_import_artifacts(
                    args.parsed_artifact, archive_root=args.archive_root
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "generate-summary":
        client = OpenAICompatibleModelClient(
            CloudModelConfig.from_env_file(args.env_file)
        )
        try:
            result = generate_product_summary_artifact(
                args.parsed_artifact,
                model=client,
                archive_root=args.archive_root,
            )
        finally:
            client.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

