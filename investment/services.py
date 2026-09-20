"""Phase 2: document upload/management. Validation, checksum computation,
and duplicate detection live here rather than in admin.py, so a future
non-admin upload path (Phase 23's API, or a public intake form) reuses the
exact same rules — mirrors duediligence/services.py's role as the one
place that touches both file bytes and the DB row.
"""

import hashlib

from .models import PropertyDocument


def compute_checksum(file) -> str:
    """SHA-256 hex digest of the file's current contents. Leaves the
    file's read position at 0 afterwards so a caller can still read/save
    it normally afterwards (mirrors duediligence.services.extract_deed's
    care around not consuming a file object other code still needs)."""
    file.seek(0)
    digest = hashlib.sha256()
    chunks = file.chunks() if hasattr(file, "chunks") else iter(lambda: file.read(8192), b"")
    for chunk in chunks:
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def find_duplicate_document(property_listing, checksum: str, exclude_pk=None):
    """Same file content already uploaded for this property — surfaced as
    a warning, never auto-rejected or auto-deleted: a duplicate upload
    might be a genuine mistake worth flagging, but only a human should
    decide whether to remove it (same restraint this system's product
    principles require of data-quality checks generally)."""
    if not checksum:
        return None
    qs = PropertyDocument.objects.filter(property_listing=property_listing, checksum=checksum)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()
