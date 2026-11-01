"""Discover unlisted documents without recording their individual names in code."""

from pathlib import Path
import re


PRIVATE_NAME = re.compile(r"research-statement-([0-9a-f]{24,64})\.pdf")


def find_private_documents(root):
    """Return private PDFs directly under this content root's documents folder."""
    directory = Path(root) / "documents"
    return sorted(file for file in directory.glob("research-statement-*.pdf")
                  if file.is_file() and PRIVATE_NAME.fullmatch(file.name))


def redact_private_text(text):
    """Hide private filenames, and any bare tokens learned from those filenames."""
    text = str(text)
    tokens = {match.group(1) for match in PRIVATE_NAME.finditer(text)}
    text = PRIVATE_NAME.sub("[unlisted-document]", text)
    for token in tokens:
        text = text.replace(token, "[unlisted-token]")
    return text
