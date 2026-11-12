from __future__ import annotations

from pathlib import Path


WD_FORMAT_DOCUMENT_DEFAULT = 16
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


def convert_legacy_doc_to_docx(
    source: Path, *, target_dir: Path | None = None
) -> Path:
    """Convert a legacy Word .doc without enabling document macros.

    The converted file is a derived artifact and may be placed in a separate
    directory so it cannot be mistaken for a second downloaded original.
    """

    source = source.resolve()
    if source.suffix.lower() != ".doc":
        raise ValueError("Legacy Word conversion requires a .doc source")
    destination = source.parent if target_dir is None else target_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{source.stem}-converted.docx"
    if target.exists() and target.stat().st_size > 0:
        return target

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("Microsoft Word conversion support is unavailable") from exc

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        document = word.Documents.Open(
            str(source),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Revert=False,
            NoEncodingDialog=True,
            OpenAndRepair=True,
        )
        document.SaveAs2(
            str(target), FileFormat=WD_FORMAT_DOCUMENT_DEFAULT, AddToRecentFiles=False
        )
    except Exception as exc:
        if target.exists():
            target.unlink()
        raise RuntimeError(
            f"Legacy Word conversion failed ({type(exc).__name__})"
        ) from exc
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=False)
            except Exception:
                pass
        pythoncom.CoUninitialize()

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Legacy Word conversion produced no DOCX file")
    return target
