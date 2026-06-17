from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import connect
from .utils import utc_now


DEFAULT_ORG_CODE = "default_org"
DEFAULT_USER_UID = "local_user"
DEFAULT_TEAM_UID = "core_team"
ORG_WIDE_READ_ROLES = {"system_admin", "org_admin", "auditor"}

PERMISSIONS: dict[str, str] = {
    "project.read": "查看项目数据",
    "project.manage": "管理项目设置与项目成员",
    "requirement.write": "创建和更新需求",
    "artifact.write": "创建和更新工程草稿与实现材料",
    "quality.check": "执行质量检查",
    "review.request": "发起复核",
    "review.decide": "确认或驳回复核结论",
    "release.approve": "批准受控发布",
    "knowledge.read": "查看知识库文档",
    "knowledge.write": "维护知识库文档",
    "assistant.run": "运行受控单助手任务",
    "code_change.approve": "批准代码变更落地",
    "audit.read": "查看审计与证据记录",
}

ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "system_admin": {"scope": "organization", "name": "系统管理员", "description": "平台最高权限，可管理组织、项目、成员、权限、发布控制、知识库、助手任务与审计。", "permissions": list(PERMISSIONS)},
    "org_admin": {"scope": "organization", "name": "组织管理员", "description": "组织级管理角色，可管理组织范围内的项目、成员、权限和过程资产。", "permissions": list(PERMISSIONS)},
    "auditor": {"scope": "organization", "name": "组织审计员", "description": "组织级只读审计角色，可查看项目、知识库和审计证据。", "permissions": ["project.read", "knowledge.read", "audit.read"]},
    "project_admin": {"scope": "project", "name": "项目管理员", "description": "项目内最高权限，可维护项目成员、角色、责任矩阵、复核流程、助手任务和代码变更落地。", "permissions": list(PERMISSIONS)},
    "process_owner": {"scope": "project", "name": "过程负责人", "description": "负责项目过程执行和交付闭环，可维护材料、运行质量检查、发起复核并查看知识与审计记录。", "permissions": ["project.read", "artifact.write", "quality.check", "review.request", "knowledge.read", "audit.read"]},
    "requirement_owner": {"scope": "project", "name": "需求负责人", "description": "负责需求维护与复核发起，可创建和更新需求并查看审计记录。", "permissions": ["project.read", "requirement.write", "review.request", "audit.read"]},
    "developer": {"scope": "project", "name": "开发人员", "description": "负责实现材料与代码修改，可维护材料、运行受控单助手任务并查看审计记录。", "permissions": ["project.read", "artifact.write", "assistant.run", "audit.read"]},
    "tester": {"scope": "project", "name": "测试人员", "description": "负责测试与质量检查，可维护测试相关材料、运行质量检查并查看审计记录。", "permissions": ["project.read", "quality.check", "artifact.write", "audit.read"]},
    "reviewer": {"scope": "project", "name": "复核人员", "description": "负责复核结论，可查看项目并确认或驳回复核。", "permissions": ["project.read", "review.decide", "audit.read"]},
    "approver": {"scope": "project", "name": "批准人员", "description": "负责正式批准，可确认复核结论、受控发布和代码变更落地。", "permissions": ["project.read", "review.decide", "release.approve", "code_change.approve", "audit.read"]},
    "readonly_auditor": {"scope": "project", "name": "项目只读审计员", "description": "项目级只读审计角色，可查看项目、知识库和审计证据。", "permissions": ["project.read", "knowledge.read", "audit.read"]},
}


def ensure_collaboration_seed(db_path: Path) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        org_id = _ensure_organization(conn, now)
        user_id = _ensure_user(conn, now)
        team_id = _ensure_team(conn, org_id, now)
        conn.execute(
            """
            INSERT OR IGNORE INTO team_members(team_id, user_id, status, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?)
            """,
            (team_id, user_id, now, now),
        )
        _ensure_roles_and_permissions(conn)
        _ensure_binding(conn, org_id, None, user_id, None, "system_admin", "organization", user_id, now)
        _ensure_binding(conn, org_id, None, user_id, None, "org_admin", "organization", user_id, now)
        projects = conn.execute("SELECT id FROM projects ORDER BY id").fetchall()
        for project in projects:
            project_id = int(project["id"])
            conn.execute(
                """
                UPDATE projects
                SET organization_id=COALESCE(organization_id, ?),
                    owner_id=COALESCE(owner_id, ?),
                    created_by=COALESCE(created_by, ?),
                    updated_by=COALESCE(updated_by, ?)
                WHERE id=?
                """,
                (org_id, user_id, user_id, user_id, project_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO project_members(project_id, user_id, role_code, status, created_at, updated_at)
                VALUES (?, ?, 'project_admin', 'active', ?, ?)
                """,
                (project_id, user_id, now, now),
            )
            _ensure_binding(conn, org_id, project_id, user_id, None, "project_admin", "project", user_id, now)
        return {"organization_id": org_id, "user_id": user_id, "team_id": team_id, "project_count": len(projects)}


def build_collaboration_summary(db_path: Path, user_uid: str = DEFAULT_USER_UID, project_id: int | None = None) -> dict[str, Any]:
    ensure_collaboration_seed(db_path)
    with connect(db_path) as conn:
        user = _select_user(conn, user_uid)
        if not user:
            user = _select_user(conn, DEFAULT_USER_UID)
        resolved_project_id = _resolve_project_id_for_user(conn, int(user["id"]), project_id)
        return {
            "current_user": _user_payload(conn, int(user["id"]), resolved_project_id),
            "organization": _default_organization(conn),
            "project_id": resolved_project_id,
            "members": _project_members(conn, resolved_project_id) if resolved_project_id else [],
            "roles": _roles(conn),
            "permissions": list(PERMISSIONS.keys()),
        }


def check_permission(db_path: Path, user_uid: str, permission: str, project_id: int | None = None) -> dict[str, Any]:
    ensure_collaboration_seed(db_path)
    with connect(db_path) as conn:
        user = _select_user(conn, user_uid)
        if not user:
            return {
                "allowed": False,
                "permission": permission,
                "project_id": _resolve_project_id(conn, project_id),
                "user": None,
                "reason": "unknown_or_inactive_user",
            }
        resolved_project_id = _resolve_project_id_for_user(conn, int(user["id"]), project_id)
        if project_id and resolved_project_id is None:
            return {
                "allowed": False,
                "permission": permission,
                "project_id": project_id,
                "user": _user_payload(conn, int(user["id"]), None),
                "reason": "project_not_accessible",
            }
        user_payload = _user_payload(conn, int(user["id"]), resolved_project_id)
        allowed = permission in set(user_payload["permissions"])
        return {
            "allowed": allowed,
            "permission": permission,
            "project_id": resolved_project_id,
            "user": user_payload,
            "reason": "role_permission_granted" if allowed else "permission_not_granted",
        }


def list_project_members(db_path: Path, project_id: int | None = None) -> list[dict[str, Any]]:
    ensure_collaboration_seed(db_path)
    with connect(db_path) as conn:
        return _project_members(conn, _resolve_project_id(conn, project_id))


def list_visible_projects(db_path: Path, user_uid: str) -> list[dict[str, Any]]:
    ensure_collaboration_seed(db_path)
    with connect(db_path) as conn:
        user = _select_user(conn, user_uid) or _select_user(conn, DEFAULT_USER_UID)
        if not user:
            return []
        ids = _visible_project_ids(conn, int(user["id"]))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT * FROM projects WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()
        return [dict(row) for row in rows]


def _ensure_organization(conn, now: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO organizations(code, name, status, created_at, updated_at)
        VALUES (?, 'Default Organization', 'active', ?, ?)
        """,
        (DEFAULT_ORG_CODE, now, now),
    )
    return int(conn.execute("SELECT id FROM organizations WHERE code=?", (DEFAULT_ORG_CODE,)).fetchone()["id"])


def _ensure_user(conn, now: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO users(user_uid, display_name, email, status, created_at, updated_at)
        VALUES (?, 'Local User', 'local@example.invalid', 'active', ?, ?)
        """,
        (DEFAULT_USER_UID, now, now),
    )
    return int(conn.execute("SELECT id FROM users WHERE user_uid=?", (DEFAULT_USER_UID,)).fetchone()["id"])


def _ensure_team(conn, organization_id: int, now: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO teams(organization_id, team_uid, name, status, created_at, updated_at)
        VALUES (?, ?, 'Core Engineering Team', 'active', ?, ?)
        """,
        (organization_id, DEFAULT_TEAM_UID, now, now),
    )
    return int(conn.execute("SELECT id FROM teams WHERE team_uid=?", (DEFAULT_TEAM_UID,)).fetchone()["id"])


def _ensure_roles_and_permissions(conn) -> None:
    for permission_code, name in PERMISSIONS.items():
        conn.execute(
            "INSERT OR IGNORE INTO permissions(permission_code, name, description) VALUES (?, ?, ?)",
            (permission_code, name, name),
        )
        conn.execute(
            "UPDATE permissions SET name=?, description=? WHERE permission_code=?",
            (name, name, permission_code),
        )
    for role_code, role in ROLE_DEFINITIONS.items():
        description = role.get("description", role["name"])
        conn.execute(
            "INSERT OR IGNORE INTO roles(role_code, scope, name, description) VALUES (?, ?, ?, ?)",
            (role_code, role["scope"], role["name"], description),
        )
        conn.execute(
            "UPDATE roles SET scope=?, name=?, description=? WHERE role_code=?",
            (role["scope"], role["name"], description, role_code),
        )
        for permission_code in role["permissions"]:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions(role_code, permission_code) VALUES (?, ?)",
                (role_code, permission_code),
            )


def _ensure_binding(
    conn,
    organization_id: int | None,
    project_id: int | None,
    user_id: int | None,
    team_id: int | None,
    role_code: str,
    scope: str,
    created_by: int,
    now: str,
) -> None:
    existing = conn.execute(
        """
        SELECT id FROM role_bindings
        WHERE COALESCE(organization_id, 0)=COALESCE(?, 0)
          AND COALESCE(project_id, 0)=COALESCE(?, 0)
          AND COALESCE(user_id, 0)=COALESCE(?, 0)
          AND COALESCE(team_id, 0)=COALESCE(?, 0)
          AND role_code=? AND scope=?
        """,
        (organization_id, project_id, user_id, team_id, role_code, scope),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO role_bindings(organization_id, project_id, user_id, team_id, role_code, scope, status, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (organization_id, project_id, user_id, team_id, role_code, scope, created_by, now),
    )


def _select_user(conn, user_uid: str):
    return conn.execute("SELECT * FROM users WHERE user_uid=? AND status='active'", (user_uid,)).fetchone()


def _resolve_project_id(conn, project_id: int | None) -> int | None:
    if project_id:
        return project_id
    row = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def _resolve_project_id_for_user(conn, user_id: int, project_id: int | None) -> int | None:
    visible_ids = _visible_project_ids(conn, user_id)
    if project_id:
        return project_id if project_id in visible_ids else None
    return visible_ids[0] if visible_ids else None


def _visible_project_ids(conn, user_id: int) -> list[int]:
    if _has_org_wide_project_read(conn, user_id):
        rows = conn.execute("SELECT id FROM projects ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT project_id AS id FROM project_members
            WHERE user_id=? AND status='active'
            UNION
            SELECT DISTINCT project_id AS id FROM role_bindings
            WHERE user_id=? AND status='active' AND scope='project' AND project_id IS NOT NULL
            ORDER BY id
            """,
            (user_id, user_id),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _has_org_wide_project_read(conn, user_id: int) -> bool:
    placeholders = ",".join("?" for _ in ORG_WIDE_READ_ROLES)
    row = conn.execute(
        f"""
        SELECT 1 FROM role_bindings
        WHERE user_id=? AND status='active' AND scope='organization' AND role_code IN ({placeholders})
        LIMIT 1
        """,
        (user_id, *sorted(ORG_WIDE_READ_ROLES)),
    ).fetchone()
    return bool(row)


def _default_organization(conn) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM organizations WHERE code=?", (DEFAULT_ORG_CODE,)).fetchone()
    return dict(row) if row else None


def _user_payload(conn, user_id: int, project_id: int | None) -> dict[str, Any]:
    user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    role_rows = conn.execute(
        """
        SELECT role_code, scope FROM role_bindings
        WHERE user_id=? AND status='active' AND (project_id IS NULL OR project_id=?)
        UNION
        SELECT role_code, 'project' AS scope FROM project_members
        WHERE user_id=? AND status='active' AND (? IS NULL OR project_id=?)
        ORDER BY role_code
        """,
        (user_id, project_id, user_id, project_id, project_id),
    ).fetchall()
    role_meta = {
        row["role_code"]: dict(row)
        for row in conn.execute("SELECT role_code, name, description FROM roles").fetchall()
    }
    roles = [
        {
            "role_code": row["role_code"],
            "scope": row["scope"],
            "name": role_meta.get(row["role_code"], {}).get("name", row["role_code"]),
            "description": role_meta.get(row["role_code"], {}).get("description", ""),
        }
        for row in role_rows
    ]
    permissions = sorted(
        {
            row["permission_code"]
            for row in conn.execute(
                """
                SELECT DISTINCT rp.permission_code
                FROM role_permissions rp
                JOIN (
                    SELECT role_code FROM role_bindings
                    WHERE user_id=? AND status='active' AND (project_id IS NULL OR project_id=?)
                    UNION
                    SELECT role_code FROM project_members
                    WHERE user_id=? AND status='active' AND (? IS NULL OR project_id=?)
                ) r ON r.role_code=rp.role_code
                ORDER BY rp.permission_code
                """,
                (user_id, project_id, user_id, project_id, project_id),
            ).fetchall()
        }
    )
    user["roles"] = roles
    user["permissions"] = permissions
    return user


def _project_members(conn, project_id: int | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT pm.id, pm.project_id, pm.role_code, pm.status, u.id AS user_id, u.user_uid, u.display_name, u.email
            FROM project_members pm
            JOIN users u ON u.id=pm.user_id
            WHERE pm.project_id=?
            ORDER BY u.display_name, pm.role_code
            """,
            (project_id,),
        ).fetchall()
    ]


def _roles(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM roles ORDER BY scope, role_code").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["permissions"] = [
            permission["permission_code"]
            for permission in conn.execute(
                "SELECT permission_code FROM role_permissions WHERE role_code=? ORDER BY permission_code",
                (row["role_code"],),
            ).fetchall()
        ]
        result.append(item)
    return result
