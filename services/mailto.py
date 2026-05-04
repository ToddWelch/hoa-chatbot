"""Safe mailto: URI construction (binding-scope item 15).

Brief lines 99 to 103 specify:
- URL-encode all values via urllib.parse.quote(safe='')
- strip every CR and LF from inputs BEFORE encoding (header-injection defense)
- subject capped at 78 characters
- body capped at 1800 characters; append truncation suffix when cut

The CR/LF strip is the critical bit: if a malicious crafted string with a
%0A%0A could open a new header, an unsuspecting mail client could be tricked
into sending Bcc: someone-else. Stripping pre-encoding closes that path.

The recipient is read from config.hoa_contact_email; routes pass the
already-resolved address in. No DB access in this module (pure helper).
"""

from __future__ import annotations

from urllib.parse import quote

SUBJECT_MAX = 78
BODY_MAX = 1800
BODY_TRUNCATION_SUFFIX = " (message truncated, please paste from chat)"


def _strip_crlf(s: str) -> str:
    """Remove every CR and LF byte. Defense against mailto header injection."""
    return s.replace("\r", "").replace("\n", "")


def _truncate(s: str, limit: int, suffix: str = "") -> str:
    """Return s truncated to `limit` chars; if cut, replace tail with suffix.

    Subject's limit is exact: cut at limit and drop the tail. Body's limit
    accommodates the suffix, so the final length is <= limit.
    """
    if len(s) <= limit:
        return s
    if not suffix:
        return s[:limit]
    keep = limit - len(suffix)
    if keep <= 0:
        return suffix[:limit]
    return s[:keep] + suffix


def build_mailto(to: str, subject: str, body: str) -> str:
    """Build a safe `mailto:` URI.

    All inputs are stripped of CR/LF before encoding. Subject is capped
    at 78 chars, body at 1800 (with truncation suffix when cut). The
    `safe=""` arg to urllib.parse.quote ensures every byte is encoded,
    including '@', '/', '+', etc., which keeps the URI shape predictable.
    """
    to_clean = _strip_crlf(to or "").strip()
    subj_clean = _strip_crlf(subject or "")
    body_clean = _strip_crlf(body or "")

    subj_capped = _truncate(subj_clean, SUBJECT_MAX)
    body_capped = _truncate(body_clean, BODY_MAX, BODY_TRUNCATION_SUFFIX)

    to_enc = quote(to_clean, safe="")
    subj_enc = quote(subj_capped, safe="")
    body_enc = quote(body_capped, safe="")

    return f"mailto:{to_enc}?subject={subj_enc}&body={body_enc}"
