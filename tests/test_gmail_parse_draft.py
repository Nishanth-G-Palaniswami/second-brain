"""Regression: `_parse_local_draft` must tolerate a UTF-8 BOM.

PowerShell's `Out-File -Encoding utf8` and Notepad's "Save As UTF-8" both
prepend a BOM (EF BB BF). The frontmatter regex is anchored to `^---`, so a
leading BOM would silently fail the match and the draft would never be
promoted or expired. The smoke test on 2026-04-19 reproduced this.

Run:
    .venv\\Scripts\\python.exe tests\\test_gmail_parse_draft.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / ".claude" / "scripts"))

from integrations.gmail import _parse_local_draft  # noqa: E402


DRAFT_BODY = """---
type: email
source_id: bom-test-thread-id-0000
recipient: nobody@example.invalid
subject: bom regression test
created: 2026-04-18T09:40:48+00:00
status: active
---

## Original Message

hello

## Draft Reply

hi back
"""


def _write(path: Path, text: str, *, bom: bool) -> None:
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def _check(parsed: dict, case: str) -> None:
    fm = parsed["frontmatter"]
    sections = parsed["sections"]
    assert fm.get("type") == "email", f"{case}: type={fm.get('type')!r}"
    assert fm.get("status") == "active", f"{case}: status={fm.get('status')!r}"
    assert fm.get("source_id") == "bom-test-thread-id-0000", f"{case}: source_id={fm.get('source_id')!r}"
    assert "Original Message" in sections, f"{case}: missing Original Message"
    assert "Draft Reply" in sections, f"{case}: missing Draft Reply"
    assert sections["Draft Reply"].strip() == "hi back", f"{case}: body={sections['Draft Reply']!r}"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)

        no_bom = tdp / "no_bom.md"
        _write(no_bom, DRAFT_BODY, bom=False)
        assert no_bom.read_bytes()[:3] == b"---", "precondition: no_bom shouldn't have BOM"
        _check(_parse_local_draft(no_bom), "no_bom")
        print("PASS  no_bom parses")

        with_bom = tdp / "with_bom.md"
        _write(with_bom, DRAFT_BODY, bom=True)
        assert with_bom.read_bytes()[:3] == b"\xef\xbb\xbf", "precondition: with_bom should have BOM"
        _check(_parse_local_draft(with_bom), "with_bom")
        print("PASS  with_bom parses (BOM stripped)")

    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
