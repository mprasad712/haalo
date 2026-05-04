from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
from io import BytesIO
import logging
import os
import re
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from packaging import version as pkg_version
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlmodel import select

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.auth.permissions import get_permissions_for_role
from agentcore.services.database.models.package.model import Package
from agentcore.services.database.models.product_release.model import ProductRelease
from agentcore.services.database.models.release_detail.model import ReleaseDetail
from agentcore.services.database.models.release_package_snapshot.model import ReleasePackageSnapshot
from agentcore.services.deps import get_settings_service
from agentcore.services.release_documents import (
    DOCX_CONTENT_TYPE,
    build_release_document_office_viewer_url,
    get_release_document,
    render_release_document_preview_html,
    sanitize_release_document_name,
    save_release_document,
)

router = APIRouter(prefix="/releases", tags=["Release Management"])

ACTIVE_END_DATE = date(9999, 12, 31)
logger = logging.getLogger(__name__)
_REGION_CODE = os.getenv("REGION_CODE", "")
_REGION_GATEWAY_URL = os.getenv("REGION_GATEWAY_URL", "").strip()


async def _maybe_proxy_release_json(
    request: Request,
    current_user: CurrentActiveUser,
    release_path: str = "",
    *,
    query_params: dict[str, str] | None = None,
) -> Any | None:
    region_code = request.headers.get("X-Region-Code", "").strip()
    if not region_code:
        return None

    if region_code.upper() == _REGION_CODE.upper():
        return None

    role = str(getattr(current_user, "role", "")).lower()
    if role != "root":
        raise HTTPException(status_code=403, detail="Cross-region access requires root role")

    if not _REGION_GATEWAY_URL:
        raise HTTPException(status_code=400, detail="Cross-region proxy not configured on this deployment")

    release_path = release_path.strip("/")
    gateway_url = f"{_REGION_GATEWAY_URL}/api/regions/{region_code}/releases"
    if release_path:
        gateway_url = f"{gateway_url}/{release_path}"

    forwarded_query_params = dict(query_params or {})
    forwarded_query_params["caller"] = str(current_user.id)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(gateway_url, params=forwarded_query_params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("Region gateway returned %d for release region %s: %s", e.response.status_code, region_code, e)
        raise HTTPException(status_code=e.response.status_code, detail=f"Region '{region_code}' error")
    except Exception as e:
        logger.error("Region gateway error for release region %s: %s", region_code, e)
        raise HTTPException(status_code=502, detail=f"Cannot reach region '{region_code}'")


async def _maybe_proxy_release_download(
    request: Request,
    current_user: CurrentActiveUser,
    release_path: str,
) -> Response | None:
    region_code = request.headers.get("X-Region-Code", "").strip()
    if not region_code:
        return None

    if region_code.upper() == _REGION_CODE.upper():
        return None

    role = str(getattr(current_user, "role", "")).lower()
    if role != "root":
        raise HTTPException(status_code=403, detail="Cross-region access requires root role")

    if not _REGION_GATEWAY_URL:
        raise HTTPException(status_code=400, detail="Cross-region proxy not configured on this deployment")

    gateway_url = f"{_REGION_GATEWAY_URL}/api/regions/{region_code}/releases/{release_path.strip('/')}"
    query_params = {"caller": str(current_user.id)}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(gateway_url, params=query_params)
            resp.raise_for_status()
            headers: dict[str, str] = {}
            for header_name in ("content-disposition", "cache-control", "pragma", "expires"):
                header_value = resp.headers.get(header_name)
                if header_value:
                    headers[header_name] = header_value
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type") or "application/octet-stream",
                headers=headers,
            )
    except httpx.HTTPStatusError as e:
        logger.error(
            "Region gateway returned %d for release download in region %s: %s",
            e.response.status_code,
            region_code,
            e,
        )
        raise HTTPException(status_code=e.response.status_code, detail=f"Region '{region_code}' error")
    except Exception as e:
        logger.error("Region gateway error for release download in region %s: %s", region_code, e)
        raise HTTPException(status_code=502, detail=f"Cannot reach region '{region_code}'")


async def _maybe_proxy_release_create(
    request: Request,
    current_user: CurrentActiveUser,
    *,
    bump_type: str,
    release_notes: str | None,
    document_file_name: str,
    document_content: bytes,
    document_content_type: str | None,
) -> dict[str, Any] | None:
    region_code = request.headers.get("X-Region-Code", "").strip()
    if not region_code:
        return None

    if region_code.upper() == _REGION_CODE.upper():
        return None

    role = str(getattr(current_user, "role", "")).lower()
    if role != "root":
        raise HTTPException(status_code=403, detail="Cross-region access requires root role")

    if not _REGION_GATEWAY_URL:
        raise HTTPException(status_code=400, detail="Cross-region proxy not configured on this deployment")

    gateway_url = f"{_REGION_GATEWAY_URL}/api/regions/{region_code}/releases/bump-with-document"
    form_data = {"bump_type": bump_type}
    if release_notes and release_notes.strip():
        form_data["release_notes"] = release_notes.strip()
    files = {
        "document_file": (
            document_file_name,
            document_content,
            document_content_type or DOCX_CONTENT_TYPE,
        )
    }
    query_params = {"caller": str(current_user.id)}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(gateway_url, params=query_params, data=form_data, files=files)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("Region gateway returned %d for release create in region %s: %s", e.response.status_code, region_code, e)
        try:
            detail = e.response.json().get("detail")
        except Exception:
            detail = None
        raise HTTPException(status_code=e.response.status_code, detail=detail or f"Region '{region_code}' error")
    except Exception as e:
        logger.error("Region gateway error for release create in region %s: %s", region_code, e)
        raise HTTPException(status_code=502, detail=f"Cannot reach region '{region_code}'")


async def _require_release_permission(current_user: CurrentActiveUser, permission: str) -> None:
    if str(getattr(current_user, "role", "")).strip().lower() == "root":
        return
    user_permissions = await get_permissions_for_role(str(current_user.role))
    if permission not in user_permissions:
        raise HTTPException(status_code=403, detail="Missing required permissions.")


class BumpType(str, Enum):
    major = "major"
    minor = "minor"
    patch = "patch"


def _parse_semver(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semantic version '{version}'. Expected format: X.Y.Z")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _bump(major: int, minor: int, patch: int, bump_type: BumpType) -> tuple[int, int, int]:
    if bump_type == BumpType.major:
        return major + 1, 0, 0
    if bump_type == BumpType.minor:
        return major, minor + 1, 0
    return major, minor, patch + 1


def _release_to_payload(release: ProductRelease, package_count: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(release.id),
        "version": release.version,
        "major": release.major,
        "minor": release.minor,
        "patch": release.patch,
        "release_notes": release.release_notes or "",
        "start_date": release.start_date.isoformat(),
        "end_date": release.end_date.isoformat(),
        "created_by": str(release.created_by) if release.created_by else None,
        "created_at": release.created_at.isoformat(),
        "updated_at": release.updated_at.isoformat(),
        "is_active": release.end_date == ACTIVE_END_DATE,
        "has_document": bool(release.document_storage_path),
        "document_file_name": release.document_file_name,
        "document_hash": release.document_hash,
        "document_content_type": release.document_content_type,
        "document_size": release.document_size,
        "document_uploaded_by": str(release.document_uploaded_by) if release.document_uploaded_by else None,
        "document_uploaded_at": release.document_uploaded_at.isoformat() if release.document_uploaded_at else None,
    }
    if package_count is not None:
        payload["package_count"] = package_count
    return payload


def _detail_to_payload(detail: ReleaseDetail) -> dict[str, Any]:
    return {
        "id": str(detail.id),
        "release_id": str(detail.release_id),
        "section_no": detail.section_no,
        "section_title": detail.section_title,
        "module": detail.module,
        "sub_module": detail.sub_module,
        "feature_capability": detail.feature_capability,
        "description_details": detail.description_details,
        "sort_order": detail.sort_order,
        "created_at": detail.created_at.isoformat(),
    }


def _release_package_to_payload(snapshot: ReleasePackageSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "release_id": str(snapshot.release_id),
        "service_name": snapshot.service_name,
        "name": snapshot.name,
        "version": snapshot.version,
        "version_spec": snapshot.version_spec,
        "package_type": snapshot.package_type,
        "required_by": snapshot.required_by or [],
        "source": snapshot.source or {},
        "captured_at": snapshot.captured_at.isoformat(),
    }


def _compare_package_versions(released_version: str | None, current_version: str | None) -> str:
    if released_version and not current_version:
        return "removed"
    if current_version and not released_version:
        return "new"
    if not released_version and not current_version:
        return "unchanged"
    if (released_version or "") == (current_version or ""):
        return "unchanged"
    try:
        released_parsed = pkg_version.parse(released_version or "")
        current_parsed = pkg_version.parse(current_version or "")
        if current_parsed > released_parsed:
            return "upgraded"
        if current_parsed < released_parsed:
            return "downgraded"
    except Exception:
        pass
    return "changed"


def _normalize_pkg(name: str) -> str:
    return "-".join(filter(None, re.split(r"[-_.]+", name.strip().lower())))


def _extract_parent_name(raw_parent: str) -> str:
    parent = (raw_parent or "").strip()
    if not parent:
        return ""
    if ":" in parent:
        parent = parent.split(":", 1)[0].strip()
    return parent


def _build_release_parent_map(snapshots: list[ReleasePackageSnapshot]) -> dict[str, set[str]]:
    parents_by_child: dict[str, set[str]] = {}
    for row in snapshots:
        if row.package_type != "transitive":
            continue
        child = _normalize_pkg(row.name)
        parents = {
            _normalize_pkg(parent_name)
            for parent_name in (_extract_parent_name(parent) for parent in (row.required_by or []))
            if parent_name
        }
        if parents:
            parents_by_child.setdefault(child, set()).update(parents)
    return parents_by_child


def _compute_reachable_transitives_from_packages(
    managed_rows: list[Package],
    transitive_rows: list[Package],
) -> set[str]:
    managed_names = {_normalize_pkg(row.name) for row in managed_rows}
    children_by_parent: dict[str, set[str]] = {}

    for row in transitive_rows:
        child = _normalize_pkg(row.name)
        parent_names: set[str] = set()
        for detail in row.required_by_details or []:
            parent = (detail.get("name") or "").strip()
            if parent:
                parent_names.add(_normalize_pkg(parent))
        for parent_name in (_extract_parent_name(parent) for parent in (row.required_by or [])):
            if parent_name:
                parent_names.add(_normalize_pkg(parent_name))
        for parent in parent_names:
            children_by_parent.setdefault(parent, set()).add(child)

    reachable: set[str] = set()
    queue = deque(managed_names)
    seen: set[str] = set(managed_names)
    while queue:
        parent = queue.popleft()
        for child in children_by_parent.get(parent, set()):
            if child in reachable:
                continue
            reachable.add(child)
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return reachable


def _compute_reachable_transitives_from_snapshot(
    managed_rows: list[ReleasePackageSnapshot],
    transitive_rows: list[ReleasePackageSnapshot],
) -> set[str]:
    managed_names = {_normalize_pkg(row.name) for row in managed_rows}
    children_by_parent: dict[str, set[str]] = {}

    for row in transitive_rows:
        child = _normalize_pkg(row.name)
        parent_names: set[str] = set()
        for parent_name in (_extract_parent_name(parent) for parent in (row.required_by or [])):
            if parent_name:
                parent_names.add(_normalize_pkg(parent_name))
        for parent in parent_names:
            children_by_parent.setdefault(parent, set()).add(child)

    reachable: set[str] = set()
    queue = deque(managed_names)
    seen: set[str] = set(managed_names)
    while queue:
        parent = queue.popleft()
        for child in children_by_parent.get(parent, set()):
            if child in reachable:
                continue
            reachable.add(child)
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return reachable


def _compute_managed_root_data_for_snapshot(
    package_name: str,
    parents_by_child: dict[str, set[str]],
    managed_names: set[str],
    display_names: dict[str, str],
) -> tuple[list[str], list[str]]:
    child = _normalize_pkg(package_name)
    roots: set[str] = set()
    paths: list[str] = []
    max_paths = 25

    stack: list[tuple[str, list[str]]] = [(child, [child])]
    seen_states: set[tuple[str, tuple[str, ...]]] = set()

    while stack and len(paths) < max_paths:
        node, upward_path = stack.pop()
        state = (node, tuple(upward_path))
        if state in seen_states:
            continue
        seen_states.add(state)

        for parent in parents_by_child.get(node, set()):
            if parent in upward_path:
                continue
            next_path = upward_path + [parent]
            if parent in managed_names:
                roots.add(parent)
                root_to_child = list(reversed(next_path))
                paths.append(" -> ".join(display_names.get(n, n) for n in root_to_child))
                continue
            stack.append((parent, next_path))

    root_list = sorted(display_names.get(name, name) for name in roots)
    unique_paths = list(dict.fromkeys(paths))
    return root_list, unique_paths


async def _capture_package_snapshot(*, session: DbSession, release_id: UUID, now: datetime) -> int:
    try:
        current_packages = (
            await session.exec(
                select(Package)
                .where(Package.end_date == ACTIVE_END_DATE)
                .order_by(Package.name.asc(), Package.package_type.asc(), Package.synced_at.desc())
            )
        ).all()
    except ProgrammingError as exc:
        if 'relation "package" does not exist' in str(exc):
            current_packages = []
        else:
            raise

    seen_keys: set[tuple[str, str, str]] = set()
    packages: list[Package] = []
    for pkg in current_packages:
        key = (pkg.service_name, pkg.name.lower(), pkg.package_type)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        packages.append(pkg)

    packages_by_service: dict[str, list[Package]] = defaultdict(list)
    for pkg in packages:
        packages_by_service[pkg.service_name].append(pkg)

    filtered_packages: list[Package] = []
    for service_packages in packages_by_service.values():
        managed_rows = [pkg for pkg in service_packages if pkg.package_type == "managed"]
        transitive_rows = [pkg for pkg in service_packages if pkg.package_type == "transitive"]
        reachable_transitives = _compute_reachable_transitives_from_packages(
            managed_rows=managed_rows,
            transitive_rows=transitive_rows,
        )
        filtered_packages.extend(managed_rows)
        filtered_packages.extend(
            [pkg for pkg in transitive_rows if _normalize_pkg(pkg.name) in reachable_transitives]
        )

    for pkg in filtered_packages:
        session.add(
            ReleasePackageSnapshot(
                release_id=release_id,
                service_name=pkg.service_name,
                name=pkg.name,
                version=pkg.version,
                version_spec=pkg.version_spec,
                package_type=pkg.package_type,
                required_by=pkg.required_by,
                source=pkg.source,
                captured_at=now,
            )
        )
        pkg.release_id = release_id

    return len(filtered_packages)


async def _get_release_package_counts(session: DbSession) -> dict[UUID, int]:
    rows = (
        await session.exec(
            select(
                ReleasePackageSnapshot.release_id,
                func.count(ReleasePackageSnapshot.id),
            ).group_by(ReleasePackageSnapshot.release_id)
        )
    ).all()
    return {release_id: count for release_id, count in rows}


async def _get_single_release_package_count(session: DbSession, release_id: UUID) -> int:
    count = (
        await session.exec(
            select(func.count(ReleasePackageSnapshot.id)).where(ReleasePackageSnapshot.release_id == release_id)
        )
    ).one()
    return int(count or 0)


async def _get_next_release(
    *,
    session: DbSession,
    release: ProductRelease,
) -> ProductRelease | None:
    return (
        await session.exec(
            select(ProductRelease)
            .where(
                (ProductRelease.start_date > release.start_date)
                | (
                    (ProductRelease.start_date == release.start_date)
                    & (ProductRelease.created_at > release.created_at)
                )
            )
            .order_by(ProductRelease.start_date.asc(), ProductRelease.created_at.asc())
        )
    ).first()


async def _create_release(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    bump_type: BumpType,
    release_notes: str | None,
    document_file_name: str,
    document_content: bytes,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = now.date()

    active_release = (
        await session.exec(
            select(ProductRelease)
            .where(ProductRelease.end_date == ACTIVE_END_DATE)
            .order_by(ProductRelease.start_date.desc(), ProductRelease.created_at.desc())
        )
    ).first()

    if active_release:
        try:
            major, minor, patch = _parse_semver(active_release.version)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        major, minor, patch = 1, 0, 0

    next_major, next_minor, next_patch = _bump(major, minor, patch, bump_type)
    next_version = f"{next_major}.{next_minor}.{next_patch}"
    document_hash = hashlib.sha256(document_content).hexdigest()

    existing = (await session.exec(select(ProductRelease).where(ProductRelease.version == next_version))).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Release version '{next_version}' already exists.")
    duplicate_document = (
        await session.exec(
            select(ProductRelease)
            .where(ProductRelease.document_hash == document_hash)
            .order_by(ProductRelease.created_at.desc())
        )
    ).first()
    if duplicate_document is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This release document has already been uploaded for version "
                f"'{duplicate_document.version}'."
            ),
        )

    if active_release is not None:
        active_release.end_date = today
        active_release.updated_at = now

    safe_file_name = sanitize_release_document_name(document_file_name)
    new_release = ProductRelease(
        version=next_version,
        major=next_major,
        minor=next_minor,
        patch=next_patch,
        release_notes=(release_notes or "").strip() or None,
        document_hash=document_hash,
        document_file_name=safe_file_name,
        document_content_type=DOCX_CONTENT_TYPE,
        document_size=len(document_content),
        document_uploaded_by=current_user.id,
        document_uploaded_at=now,
        start_date=today,
        end_date=ACTIVE_END_DATE,
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(new_release)
    await session.flush()

    settings_service = get_settings_service()
    try:
        new_release.document_storage_path = await save_release_document(
            settings_service=settings_service,
            release_id=new_release.id,
            file_name=safe_file_name,
            content=document_content,
        )
        package_count = await _capture_package_snapshot(session=session, release_id=new_release.id, now=now)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    await session.refresh(new_release)
    return _release_to_payload(new_release, package_count=package_count)


@router.get("")
@router.get("/")
async def get_releases(
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> list[dict[str, Any]]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(request, current_user)
    if proxied is not None:
        return proxied
    package_counts = await _get_release_package_counts(session)
    releases = (
        await session.exec(
            select(ProductRelease).order_by(ProductRelease.start_date.desc(), ProductRelease.created_at.desc())
        )
    ).all()
    return [_release_to_payload(release, package_count=package_counts.get(release.id, 0)) for release in releases]


@router.get("/current")
async def get_current_release(
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, Any] | None:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(request, current_user, "current")
    if proxied is not None:
        return proxied
    release = (
        await session.exec(
            select(ProductRelease)
            .where(ProductRelease.end_date == ACTIVE_END_DATE)
            .order_by(ProductRelease.start_date.desc(), ProductRelease.created_at.desc())
        )
    ).first()
    if release is None:
        return None
    return _release_to_payload(release, package_count=await _get_single_release_package_count(session, release.id))


@router.get("/current-version")
async def get_current_release_version(
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, str]:
    """Return only the current release version string. Accessible to all authenticated users."""
    release = (
        await session.exec(
            select(ProductRelease)
            .where(ProductRelease.end_date == ACTIVE_END_DATE)
            .order_by(ProductRelease.start_date.desc(), ProductRelease.created_at.desc())
        )
    ).first()
    return {"version": release.version if release else ""}


@router.get("/{release_id}")
async def get_release(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, Any]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(request, current_user, str(release_id))
    if proxied is not None:
        return proxied
    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return _release_to_payload(release, package_count=await _get_single_release_package_count(session, release.id))


@router.get("/{release_id}/details")
async def get_release_details(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> list[dict[str, Any]]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(request, current_user, f"{release_id}/details")
    if proxied is not None:
        return proxied
    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    details = (
        await session.exec(
            select(ReleaseDetail)
            .where(ReleaseDetail.release_id == release_id)
            .order_by(ReleaseDetail.sort_order.asc(), ReleaseDetail.created_at.asc())
        )
    ).all()
    return [_detail_to_payload(item) for item in details]


@router.get("/{release_id}/document/preview")
async def get_release_document_preview(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, Any]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(request, current_user, f"{release_id}/document/preview")
    if proxied is not None:
        return proxied
    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    if not release.document_storage_path:
        return {
            "html": "",
            "file_name": release.document_file_name,
            "document_uploaded_at": release.document_uploaded_at.isoformat() if release.document_uploaded_at else None,
            "document_size": release.document_size,
            "has_document": False,
        }

    try:
        document_bytes = await get_release_document(
            settings_service=get_settings_service(),
            storage_path=release.document_storage_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release document not found") from exc

    return {
        "html": render_release_document_preview_html(document_bytes),
        "file_name": release.document_file_name,
        "document_uploaded_at": release.document_uploaded_at.isoformat() if release.document_uploaded_at else None,
        "document_size": release.document_size,
        "has_document": True,
        "office_viewer_url": await build_release_document_office_viewer_url(
            settings_service=get_settings_service(),
            storage_path=release.document_storage_path,
        ),
    }


@router.get("/{release_id}/document/download")
async def download_release_document(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
):
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_download(request, current_user, f"{release_id}/document/download")
    if proxied is not None:
        return proxied
    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    if not release.document_storage_path or not release.document_file_name:
        raise HTTPException(status_code=404, detail="Release document not found")

    try:
        document_bytes = await get_release_document(
            settings_service=get_settings_service(),
            storage_path=release.document_storage_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release document not found") from exc

    headers = {
        "Content-Disposition": f'attachment; filename="{release.document_file_name}"',
    }
    return StreamingResponse(BytesIO(document_bytes), media_type=release.document_content_type or DOCX_CONTENT_TYPE, headers=headers)


@router.get("/{release_id}/packages")
async def get_release_packages(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    service: str = Query(default="all"),
) -> list[dict[str, Any]]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(
        request,
        current_user,
        f"{release_id}/packages",
        query_params={"service": service},
    )
    if proxied is not None:
        return proxied
    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    normalized_service = service.strip().lower()
    snapshots = (
        await session.exec(
            select(ReleasePackageSnapshot)
            .where(
                ReleasePackageSnapshot.release_id == release_id,
                *(() if normalized_service == "all" else (ReleasePackageSnapshot.service_name == normalized_service,)),
            )
            .order_by(
                ReleasePackageSnapshot.service_name.asc(),
                ReleasePackageSnapshot.package_type.asc(),
                ReleasePackageSnapshot.name.asc(),
            )
        )
    ).all()

    payloads: list[dict[str, Any]] = []
    snapshots_by_service: dict[str, list[ReleasePackageSnapshot]] = defaultdict(list)
    for row in snapshots:
        snapshots_by_service[row.service_name].append(row)

    for service_rows in snapshots_by_service.values():
        managed_rows = [row for row in service_rows if row.package_type == "managed"]
        transitive_rows = [row for row in service_rows if row.package_type == "transitive"]
        reachable_transitives = _compute_reachable_transitives_from_snapshot(
            managed_rows=managed_rows,
            transitive_rows=transitive_rows,
        )
        managed_names = {_normalize_pkg(row.name) for row in managed_rows}
        managed_by_norm = {_normalize_pkg(row.name): row for row in managed_rows}
        display_names = {_normalize_pkg(row.name): row.name for row in service_rows}
        parents_by_child = _build_release_parent_map(service_rows)

        for item in service_rows:
            payload = _release_package_to_payload(item)
            if item.package_type == "transitive":
                normalized_name = _normalize_pkg(item.name)
                if normalized_name not in reachable_transitives:
                    continue
                managed_roots, dependency_paths = _compute_managed_root_data_for_snapshot(
                    package_name=item.name,
                    parents_by_child=parents_by_child,
                    managed_names=managed_names,
                    display_names=display_names,
                )
                root_order = {root: idx for idx, root in enumerate(managed_roots)}
                dependency_paths = sorted(
                    dependency_paths,
                    key=lambda path: (root_order.get(path.split(" -> ", 1)[0], 10_000), path),
                )
                managed_root_details = []
                for root_name in managed_roots:
                    root_row = managed_by_norm.get(_normalize_pkg(root_name))
                    if root_row is None:
                        continue
                    managed_root_details.append({"name": root_row.name, "version": root_row.version})
                payload["managed_roots"] = managed_roots
                payload["managed_root_details"] = managed_root_details
                payload["dependency_paths"] = dependency_paths
            else:
                payload["managed_roots"] = []
                payload["managed_root_details"] = []
                payload["dependency_paths"] = []
            payloads.append(payload)
    return payloads


@router.get("/{release_id}/package-comparison")
async def get_release_package_comparison(
    release_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    service: str = Query(default="all"),
) -> list[dict[str, Any]]:
    await _require_release_permission(current_user, "view_release_management_page")
    proxied = await _maybe_proxy_release_json(
        request,
        current_user,
        f"{release_id}/package-comparison",
        query_params={"service": service},
    )
    if proxied is not None:
        return proxied

    release = await session.get(ProductRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    normalized_service = service.strip().lower()
    service_filter = () if normalized_service == "all" else (ReleasePackageSnapshot.service_name == normalized_service,)
    snapshots = (
        await session.exec(
            select(ReleasePackageSnapshot)
            .where(ReleasePackageSnapshot.release_id == release_id, *service_filter)
            .order_by(
                ReleasePackageSnapshot.service_name.asc(),
                ReleasePackageSnapshot.package_type.asc(),
                ReleasePackageSnapshot.name.asc(),
            )
        )
    ).all()

    comparison_rows: list[dict[str, Any]] = []

    if release.end_date == ACTIVE_END_DATE:
        current_package_filter = () if normalized_service == "all" else (Package.service_name == normalized_service,)
        current_packages = (
            await session.exec(
                select(Package)
                .where(Package.end_date == ACTIVE_END_DATE, *current_package_filter)
                .order_by(Package.service_name.asc(), Package.package_type.asc(), Package.name.asc())
            )
        ).all()

        snapshot_by_service: dict[str, list[ReleasePackageSnapshot]] = defaultdict(list)
        current_by_service: dict[str, list[Package]] = defaultdict(list)
        for row in snapshots:
            snapshot_by_service[row.service_name].append(row)
        for row in current_packages:
            current_by_service[row.service_name].append(row)

        all_services = sorted(set(snapshot_by_service.keys()) | set(current_by_service.keys()))

        for service_name in all_services:
            service_snapshots = snapshot_by_service.get(service_name, [])
            service_current = current_by_service.get(service_name, [])

            snapshot_managed = [row for row in service_snapshots if row.package_type == "managed"]
            snapshot_transitive = [row for row in service_snapshots if row.package_type == "transitive"]
            current_managed = [row for row in service_current if row.package_type == "managed"]
            current_transitive = [row for row in service_current if row.package_type == "transitive"]

            reachable_snapshot_transitives = _compute_reachable_transitives_from_snapshot(
                managed_rows=snapshot_managed,
                transitive_rows=snapshot_transitive,
            )
            reachable_current_transitives = _compute_reachable_transitives_from_packages(
                managed_rows=current_managed,
                transitive_rows=current_transitive,
            )

            filtered_snapshots = [
                row
                for row in service_snapshots
                if row.package_type != "transitive" or _normalize_pkg(row.name) in reachable_snapshot_transitives
            ]
            filtered_current = [
                row
                for row in service_current
                if row.package_type != "transitive" or _normalize_pkg(row.name) in reachable_current_transitives
            ]

            snapshot_lookup = {
                (row.package_type, _normalize_pkg(row.name)): row
                for row in filtered_snapshots
            }
            current_lookup = {
                (row.package_type, _normalize_pkg(row.name)): row
                for row in filtered_current
            }

            comparison_keys = sorted(
                set(snapshot_lookup.keys()) | set(current_lookup.keys()),
                key=lambda item: (item[0], item[1]),
            )

            for package_type, normalized_name in comparison_keys:
                released_row = snapshot_lookup.get((package_type, normalized_name))
                current_row = current_lookup.get((package_type, normalized_name))
                display_name = (
                    released_row.name
                    if released_row is not None
                    else (current_row.name if current_row is not None else normalized_name)
                )

                comparison_rows.append(
                    {
                        "release_id": str(release_id),
                        "service_name": service_name,
                        "package_type": package_type,
                        "name": display_name,
                        "released_version": released_row.version if released_row is not None else None,
                        "released_version_spec": released_row.version_spec if released_row is not None else None,
                        "current_version": current_row.version if current_row is not None else None,
                        "current_version_spec": current_row.version_spec if current_row is not None else None,
                        "status": _compare_package_versions(
                            released_row.version if released_row is not None else None,
                            current_row.version if current_row is not None else None,
                        ),
                    }
                )
        return comparison_rows

    next_release = await _get_next_release(session=session, release=release)
    if next_release is None:
        return []

    next_release_filter = () if normalized_service == "all" else (ReleasePackageSnapshot.service_name == normalized_service,)
    next_snapshots = (
        await session.exec(
            select(ReleasePackageSnapshot)
            .where(ReleasePackageSnapshot.release_id == next_release.id, *next_release_filter)
            .order_by(
                ReleasePackageSnapshot.service_name.asc(),
                ReleasePackageSnapshot.package_type.asc(),
                ReleasePackageSnapshot.name.asc(),
            )
        )
    ).all()

    snapshot_by_service: dict[str, list[ReleasePackageSnapshot]] = defaultdict(list)
    next_by_service: dict[str, list[ReleasePackageSnapshot]] = defaultdict(list)
    for row in snapshots:
        snapshot_by_service[row.service_name].append(row)
    for row in next_snapshots:
        next_by_service[row.service_name].append(row)

    all_services = sorted(set(snapshot_by_service.keys()) | set(next_by_service.keys()))

    for service_name in all_services:
        service_snapshots = snapshot_by_service.get(service_name, [])
        service_next = next_by_service.get(service_name, [])

        snapshot_managed = [row for row in service_snapshots if row.package_type == "managed"]
        snapshot_transitive = [row for row in service_snapshots if row.package_type == "transitive"]
        next_managed = [row for row in service_next if row.package_type == "managed"]
        next_transitive = [row for row in service_next if row.package_type == "transitive"]

        reachable_snapshot_transitives = _compute_reachable_transitives_from_snapshot(
            managed_rows=snapshot_managed,
            transitive_rows=snapshot_transitive,
        )
        reachable_next_transitives = _compute_reachable_transitives_from_snapshot(
            managed_rows=next_managed,
            transitive_rows=next_transitive,
        )

        filtered_snapshots = [
            row
            for row in service_snapshots
            if row.package_type != "transitive" or _normalize_pkg(row.name) in reachable_snapshot_transitives
        ]
        filtered_next = [
            row
            for row in service_next
            if row.package_type != "transitive" or _normalize_pkg(row.name) in reachable_next_transitives
        ]

        snapshot_lookup = {
            (row.package_type, _normalize_pkg(row.name)): row
            for row in filtered_snapshots
        }
        next_lookup = {
            (row.package_type, _normalize_pkg(row.name)): row
            for row in filtered_next
        }

        comparison_keys = sorted(
            set(snapshot_lookup.keys()) | set(next_lookup.keys()),
            key=lambda item: (item[0], item[1]),
        )

        for package_type, normalized_name in comparison_keys:
            released_row = snapshot_lookup.get((package_type, normalized_name))
            next_row = next_lookup.get((package_type, normalized_name))
            display_name = (
                released_row.name
                if released_row is not None
                else (next_row.name if next_row is not None else normalized_name)
            )

            comparison_rows.append(
                {
                    "release_id": str(release_id),
                    "service_name": service_name,
                    "package_type": package_type,
                    "name": display_name,
                    "released_version": released_row.version if released_row is not None else None,
                    "released_version_spec": released_row.version_spec if released_row is not None else None,
                    "current_version": next_row.version if next_row is not None else None,
                    "current_version_spec": next_row.version_spec if next_row is not None else None,
                    "status": _compare_package_versions(
                        released_row.version if released_row is not None else None,
                        next_row.version if next_row is not None else None,
                    ),
                }
            )

    return comparison_rows


@router.post("/bump")
async def bump_release(
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, Any]:
    await _require_release_permission(current_user, "publish_release")
    raise HTTPException(status_code=400, detail="Release document upload is required. Use /releases/bump-with-document.")


@router.post("/bump-with-details")
async def bump_release_with_details(
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, Any]:
    await _require_release_permission(current_user, "publish_release")
    raise HTTPException(status_code=400, detail="Sheet/manual release details are no longer supported. Upload a release document instead.")


@router.post("/bump-with-document")
async def bump_release_with_document(
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
    bump_type: BumpType = Form(...),
    release_notes: str | None = Form(default=None),
    document_file: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    await _require_release_permission(current_user, "publish_release")

    if document_file is None:
        raise HTTPException(status_code=400, detail="Release document is required.")

    filename = (document_file.filename or "").strip()
    if not filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx release documents are supported.")

    content = await document_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded release document is empty.")

    proxied = await _maybe_proxy_release_create(
        request,
        current_user,
        bump_type=bump_type.value,
        release_notes=release_notes,
        document_file_name=filename,
        document_content=content,
        document_content_type=document_file.content_type,
    )
    if proxied is not None:
        return proxied

    return await _create_release(
        session=session,
        current_user=current_user,
        bump_type=bump_type,
        release_notes=release_notes,
        document_file_name=filename,
        document_content=content,
    )
