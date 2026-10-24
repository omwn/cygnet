"""File conventions of the Step 2 output directory (bin/cygnets_presynth).

Step 2 (2_batch_convert_lmfs.py) writes one Cygnet XML file per lexicon, a
``{stem}_log.json`` conversion log beside each, and a manifest of the files it
planned.  Step 6 and the reporting scripts read them back.
"""

from __future__ import annotations

from pathlib import Path

# Lists the outputs Step 2 planned, one per line; anything else in the
# directory is left over from an earlier build.
MANIFEST_NAME = "manifest.txt"


def log_path(xml_path: Path | str) -> Path:
    """The conversion log written beside a pre-synth XML file.

    Args:
        xml_path: A pre-synth XML file, e.g. ``wnja-2.1.xml``.

    Returns:
        Its log, e.g. ``wnja-2.1_log.json``.
    """
    xml_path = Path(xml_path)
    return xml_path.with_name(xml_path.stem + "_log.json")


def stem_resource(stem: str, codes: list[str] | tuple[str, ...]) -> str | None:
    """The resource code a pre-synth file stem belongs to.

    A stem is the resource code plus a version ('UzWordnet-uz-1.0').

    Args:
        stem: The file stem.
        codes: Candidate resource codes; the first match wins, so list longer
            codes first when one code is a prefix of another.

    Returns:
        The matching code, or None.
    """
    for code in codes:
        if stem == code or stem.startswith(code + "-"):
            return code
    return None


def write_manifest(presynth: Path, names: list[str]) -> None:
    """Record the files Step 2 planned.

    Args:
        presynth: The pre-synth directory.
        names: The planned XML file names.
    """
    (presynth / MANIFEST_NAME).write_text(
        "".join(f"{name}\n" for name in names), encoding="utf-8"
    )


def read_manifest(presynth: Path) -> list[str] | None:
    """The files Step 2 planned, or None if it wrote no manifest.

    Args:
        presynth: The pre-synth directory.

    Returns:
        The planned XML file names, in the order written.
    """
    manifest = presynth / MANIFEST_NAME
    if not manifest.exists():
        return None
    return manifest.read_text(encoding="utf-8").split()
