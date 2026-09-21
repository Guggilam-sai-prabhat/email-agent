"""Email content sanitization for the analysis pipeline.

Every email is untrusted input, potentially adversarial. This module never
decides that an email is "safe" — it only normalizes and delimits content so
that a downstream LLM call can treat it as inert user data, never as
instructions. Callers MUST interpolate the resulting text into a user-data
field, never into a system/developer prompt.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

# Tags whose content is never visible text and must be dropped entirely,
# including tracking pixels (img has no text content, but href/src never
# fetched anyway — listed here for tags that can carry executable or
# invisible payloads).
_DROP_CONTENT_TAGS = {"script", "style", "head", "title", "noscript"}

# Lines a quoted-reply / prior-history block conventionally starts with.
_QUOTE_HEADER_RE = re.compile(
    r"^\s*(on .+ wrote:|-{2,}\s*original message\s*-{2,}|from:\s.*)\s*$",
    re.IGNORECASE,
)
_QUOTE_PREFIX_RE = re.compile(r"^\s*>")

_WHITESPACE_RUN_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINE_RUN_RE = re.compile(r"\n{3,}")


class _SanitizingHTMLExtractor(HTMLParser):
    """Strips tags down to visible text only.

    Drops script/style/head/title/noscript content outright, and skips any
    element hidden via style="display:none" / "visibility:hidden" or the
    `hidden` attribute — a common prompt-injection and phishing trick to hide
    text from human readers while an LLM (or screen reader) still ingests it.
    No network access is ever performed for any tag (img, link, etc. are
    parsed for their text only; their src/href is never fetched).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._drop_depth = 0
        self._hidden_depth = 0

    @staticmethod
    def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if "hidden" in attr_map:
            return True
        style = attr_map.get("style", "").lower()
        if "display:none" in style.replace(" ", "") or "display: none" in style:
            return True
        if "visibility:hidden" in style.replace(" ", ""):
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
        elif self._is_hidden(attrs):
            self._hidden_depth += 1
        elif tag in ("br", "p", "div", "tr"):
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br",):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT_TAGS and self._drop_depth > 0:
            self._drop_depth -= 1
        elif tag in ("p", "div", "tr") :
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth == 0 and self._hidden_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """Safely convert HTML to plain text. Never fetches any external resource;
    drops scripts, styles, metadata, and content hidden from human readers."""
    parser = _SanitizingHTMLExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def normalize_whitespace(text: str) -> str:
    """Collapse runs of horizontal whitespace and excess blank lines, and
    trim trailing whitespace per line. Preserves paragraph structure."""
    lines = [_WHITESPACE_RUN_RE.sub(" ", line).strip() for line in text.splitlines()]
    collapsed = "\n".join(lines)
    collapsed = _BLANK_LINE_RUN_RE.sub("\n\n", collapsed)
    return collapsed.strip()


def split_quoted_history(text: str) -> tuple[str, str]:
    """Split a message body into (current_message, quoted_history).

    Detects the first line that looks like the start of a quoted reply chain
    ("On ... wrote:", "----- Original Message -----", a "From:" header line,
    or a run of "> " quoted lines) and treats everything from there on as
    quoted history. If no such marker is found, the whole body is the
    current message and quoted_history is empty.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _QUOTE_HEADER_RE.match(line) or _QUOTE_PREFIX_RE.match(line):
            current = "\n".join(lines[:index]).strip()
            quoted = "\n".join(lines[index:]).strip()
            return current, quoted
    return text.strip(), ""


def sha256_hash(text: str) -> str:
    """Content hash for idempotency. Callers must never log the body itself,
    only this hash."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class SanitizedEmail:
    """Fully sanitized, delimited email content ready to pass to an LLM as an
    opaque user-data block. `render()` produces the only text that should
    ever be interpolated into a prompt, and only into a user/data field —
    never into the system or developer prompt."""

    headers_text: str
    current_message: str
    quoted_history: str
    truncated: bool
    content_hash: str

    def render(self) -> str:
        """Render as a clearly delimited block. The delimiters exist so a
        downstream prompt can instruct the model that everything inside is
        untrusted data, not instructions, regardless of what it claims to be."""
        sections = [
            "<email_headers>",
            self.headers_text,
            "</email_headers>",
            "<email_current_message>",
            self.current_message or "(empty)",
            "</email_current_message>",
        ]
        if self.quoted_history:
            sections += [
                "<email_quoted_history>",
                self.quoted_history,
                "</email_quoted_history>",
            ]
        return "\n".join(sections)


def sanitize_email(
    *,
    headers_text: str,
    plain_body: str | None,
    html_body: str | None,
    max_body_characters: int,
) -> SanitizedEmail:
    """Run the full sanitization pipeline on a message's headers and body.

    Prefers plain_body when present; otherwise safely converts html_body to
    text. Normalizes whitespace, splits off quoted reply history, enforces
    max_body_characters (applied to the current message only — quoted
    history for a truncated message is dropped, since it cannot be trusted
    to be complete anyway), and computes a content hash without ever logging
    the body.
    """
    body = plain_body if plain_body else html_to_text(html_body or "")
    body = normalize_whitespace(body)
    current, quoted = split_quoted_history(body)

    truncated = len(current) > max_body_characters
    if truncated:
        current = current[:max_body_characters]
        quoted = ""

    content_hash = sha256_hash(f"{headers_text}\n{current}\n{quoted}")

    return SanitizedEmail(
        headers_text=headers_text,
        current_message=current,
        quoted_history=quoted,
        truncated=truncated,
        content_hash=content_hash,
    )
