"""Outline wiki API client for pushing lecture notes."""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

OUTLINE_BASE_URL = os.environ.get("OUTLINE_URL", "https://outline.hanan.nz")
OUTLINE_API_KEY = os.environ.get("OUTLINE_API_KEY", "")
OUTLINE_COLLECTION = os.environ.get("OUTLINE_COLLECTION", "UC")


def _api_url(endpoint: str) -> str:
    return f"{OUTLINE_BASE_URL}/api/{endpoint}"


def _headers() -> dict:
    if not OUTLINE_API_KEY:
        raise ValueError("OUTLINE_API_KEY environment variable is not set")
    return {
        "Authorization": f"Bearer {OUTLINE_API_KEY}",
        "Content-Type": "application/json",
    }


def _handle_response(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Outline API error: {body}")
    return body.get("data", {})


# ── Collections ──────────────────────────────────────────────


def list_collections() -> list[dict]:
    """List all accessible collections."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url("collections.list"), headers=_headers(), json={})
        return _handle_response(resp)


def get_collection(collection_id: str) -> dict:
    """Get a collection by ID."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            _api_url("collections.info"),
            headers=_headers(),
            json={"id": collection_id},
        )
        return _handle_response(resp)


def create_collection(name: str, *, description: str = "", icon: str = "", color: str = "") -> dict:
    """Create a new collection. Returns the collection data including its ID."""
    payload: dict = {"name": name}
    if description:
        payload["description"] = description
    if icon:
        payload["icon"] = icon
    if color:
        payload["color"] = color
    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url("collections.create"), headers=_headers(), json=payload)
        return _handle_response(resp)


def find_or_create_collection(name: str, **kwargs) -> dict:
    """Find a collection by name, or create it if it doesn't exist."""
    collections = list_collections()
    for col in collections:
        if col.get("name") == name:
            return col
    return create_collection(name, **kwargs)


# ── Documents ────────────────────────────────────────────────


def create_document(
    title: str,
    text: str,
    collection_id: str,
    *,
    parent_document_id: str | None = None,
    publish: bool = True,
) -> dict:
    """Create a new document in a collection. Returns the document data."""
    payload: dict = {
        "title": title,
        "text": text,
        "collectionId": collection_id,
        "publish": publish,
    }
    if parent_document_id:
        payload["parentDocumentId"] = parent_document_id
    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url("documents.create"), headers=_headers(), json=payload)
        return _handle_response(resp)


def update_document(document_id: str, *, title: str | None = None, text: str | None = None) -> dict:
    """Update an existing document's title and/or content."""
    payload: dict = {"id": document_id}
    if title is not None:
        payload["title"] = title
    if text is not None:
        payload["text"] = text
    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url("documents.update"), headers=_headers(), json=payload)
        return _handle_response(resp)


def get_document(document_id: str) -> dict:
    """Get a document by ID."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            _api_url("documents.info"),
            headers=_headers(),
            json={"id": document_id},
        )
        return _handle_response(resp)


def search_documents(query: str, *, collection_id: str | None = None, limit: int = 25) -> list[dict]:
    """Search documents by keyword. Returns list of {ranking, context, document} dicts."""
    payload: dict = {"query": query, "limit": limit}
    if collection_id:
        payload["collectionId"] = collection_id
    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url("documents.search"), headers=_headers(), json=payload)
        return _handle_response(resp)


def upsert_document(
    title: str,
    text: str,
    collection_id: str,
    *,
    parent_document_id: str | None = None,
    publish: bool = True,
) -> dict:
    """Create or update a document. Searches by exact title within the collection.

    If a document with the same title exists in the collection, it is updated.
    Otherwise a new document is created.
    """
    results = search_documents(title, collection_id=collection_id, limit=10)
    for result in results:
        doc = result.get("document", {})
        if doc.get("title") == title and doc.get("collectionId") == collection_id:
            logger.info("Updating existing Outline document %s: %s", doc["id"], title)
            return update_document(doc["id"], text=text)

    logger.info("Creating new Outline document: %s", title)
    return create_document(
        title, text, collection_id,
        parent_document_id=parent_document_id,
        publish=publish,
    )


def list_documents(collection_id: str, *, parent_document_id: str | None = None) -> list[dict]:
    """List documents in a collection, optionally under a specific parent.

    When parent_document_id is None, returns top-level documents only.
    """
    payload: dict = {"collectionId": collection_id}
    if parent_document_id:
        payload["parentDocumentId"] = parent_document_id
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            _api_url("documents.list"),
            headers=_headers(),
            json=payload,
        )
        return _handle_response(resp)


def find_or_create_document(
    title: str,
    collection_id: str,
    *,
    parent_document_id: str | None = None,
    text: str = "",
    publish: bool = True,
) -> dict:
    """Find a document by title among siblings, or create if missing.

    Uses documents.list (not search) to avoid indexing-delay duplicates.
    For top-level docs (no parent), filters results to only parentless docs
    since the Outline API returns all docs when no parentDocumentId is given.
    """
    docs = list_documents(collection_id, parent_document_id=parent_document_id)
    for doc in docs:
        if doc.get("title") != title:
            continue
        # When looking for top-level docs, skip any that have a parent
        if parent_document_id is None and doc.get("parentDocumentId") is not None:
            continue
        return doc

    return create_document(
        title, text, collection_id,
        parent_document_id=parent_document_id,
        publish=publish,
    )


# ── Auth check ───────────────────────────────────────────────


def verify_connection() -> dict:
    """Verify the API key works. Returns auth info."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(_api_url("auth.info"), headers=_headers(), json={})
        return _handle_response(resp)
