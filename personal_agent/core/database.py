from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    organization_id INTEGER,
    owner_id INTEGER,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    input_key TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, input_key),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    requirement_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, requirement_id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    requirement_id TEXT NOT NULL DEFAULT '',
    artifact_type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    process_area_code TEXT NOT NULL DEFAULT '',
    source_agent_run_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, source_agent_run_id, name),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifact_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text/plain',
    sha256 TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(artifact_id, file_name),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    requirement_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    source_agent_run_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, requirement_id, link_type, target_ref, source_agent_run_id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_events_project_created
ON audit_events(project_id, created_at);

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uid TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    password_hash TEXT NOT NULL DEFAULT '',
    password_updated_at TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uid TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    team_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(team_id, user_id),
    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_code TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    permission_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_code TEXT NOT NULL,
    permission_code TEXT NOT NULL,
    UNIQUE(role_code, permission_code),
    FOREIGN KEY(role_code) REFERENCES roles(role_code) ON DELETE CASCADE,
    FOREIGN KEY(permission_code) REFERENCES permissions(permission_code) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, user_id, role_code),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(role_code) REFERENCES roles(role_code)
);

CREATE TABLE IF NOT EXISTS role_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER,
    project_id INTEGER,
    user_id INTEGER,
    team_id INTEGER,
    role_code TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, project_id, user_id, team_id, role_code, scope),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY(role_code) REFERENCES roles(role_code),
    FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    item_uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.7,
    use_count INTEGER NOT NULL DEFAULT 0,
    helpful_count INTEGER NOT NULL DEFAULT 0,
    unhelpful_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    policy_effect_scope TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    doc_uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    source_uri TEXT NOT NULL DEFAULT '',
    trust_level TEXT NOT NULL DEFAULT 'reference',
    import_batch_id TEXT NOT NULL DEFAULT '',
    source_owner TEXT NOT NULL DEFAULT '',
    source_trust_level TEXT NOT NULL DEFAULT 'internal',
    source_version TEXT NOT NULL DEFAULT '',
    applicable_project TEXT NOT NULL DEFAULT '',
    applicable_process_json TEXT NOT NULL DEFAULT '[]',
    applicable_domain TEXT NOT NULL DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'approved',
    expires_at TEXT NOT NULL DEFAULT '',
    supersedes TEXT NOT NULL DEFAULT '',
    superseded_by TEXT NOT NULL DEFAULT '',
    material_type TEXT NOT NULL DEFAULT 'reference_document',
    code_refs_json TEXT NOT NULL DEFAULT '[]',
    duplicate_of TEXT NOT NULL DEFAULT '',
    conflict_set_json TEXT NOT NULL DEFAULT '[]',
    approved_by TEXT NOT NULL DEFAULT '',
    approved_at TEXT NOT NULL DEFAULT '',
    deprecated_by TEXT NOT NULL DEFAULT '',
    deprecated_at TEXT NOT NULL DEFAULT '',
    rollback_of TEXT NOT NULL DEFAULT '',
    process_codes_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    token_hint INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(document_id, chunk_index),
    FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_search_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    source_kind TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    document_id INTEGER,
    item_uid TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    heading TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    process_codes_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    content_hash TEXT NOT NULL DEFAULT '',
    content_preview TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(source_kind, source_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_project_status
ON knowledge_search_entries(project_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_source
ON knowledge_search_entries(source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_document
ON knowledge_search_entries(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_item_uid
ON knowledge_search_entries(source_kind, item_uid);

CREATE TABLE IF NOT EXISTS knowledge_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER,
    source_type TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    source_owner TEXT NOT NULL DEFAULT '',
    source_trust_level TEXT NOT NULL DEFAULT 'internal',
    source_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'imported',
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_document_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conflict_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER,
    document_id INTEGER NOT NULL,
    conflicting_document_id INTEGER NOT NULL,
    conflict_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    FOREIGN KEY(conflicting_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS code_style_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    profile_json TEXT NOT NULL DEFAULT '{}',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    sample_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS code_repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    root_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_indexed_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, root_path),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_repositories_project
ON code_repositories(project_id, status);

CREATE TABLE IF NOT EXISTS code_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    file_type TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL DEFAULT '',
    line_count INTEGER NOT NULL DEFAULT 0,
    source_preview TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    last_indexed_at TEXT NOT NULL DEFAULT '',
    parser TEXT NOT NULL DEFAULT 'regex',
    parser_confidence REAL NOT NULL DEFAULT 0,
    UNIQUE(repository_id, path),
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_files_repository_path
ON code_files(repository_id, path);
CREATE INDEX IF NOT EXISTS idx_code_files_repository_type
ON code_files(repository_id, file_type);

CREATE TABLE IF NOT EXISTS code_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    storage_class TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES code_files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_symbols_repository_name
ON code_symbols(repository_id, name);
CREATE INDEX IF NOT EXISTS idx_code_symbols_repository_kind
ON code_symbols(repository_id, kind);
CREATE INDEX IF NOT EXISTS idx_code_symbols_file
ON code_symbols(file_id);

CREATE TABLE IF NOT EXISTS code_includes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL,
    include_text TEXT NOT NULL,
    resolved_file_id INTEGER,
    include_kind TEXT NOT NULL DEFAULT 'local',
    line INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id) REFERENCES code_files(id) ON DELETE CASCADE,
    FOREIGN KEY(resolved_file_id) REFERENCES code_files(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_code_includes_repository_source
ON code_includes(repository_id, source_file_id);
CREATE INDEX IF NOT EXISTS idx_code_includes_repository_resolved
ON code_includes(repository_id, resolved_file_id);

CREATE TABLE IF NOT EXISTS code_call_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    caller_name TEXT NOT NULL,
    callee_name TEXT NOT NULL,
    line INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES code_files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_call_edges_caller
ON code_call_edges(repository_id, caller_name);
CREATE INDEX IF NOT EXISTS idx_code_call_edges_callee
ON code_call_edges(repository_id, callee_name);
CREATE INDEX IF NOT EXISTS idx_code_call_edges_file
ON code_call_edges(file_id);

CREATE TABLE IF NOT EXISTS code_conditional_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    directive TEXT NOT NULL,
    expression TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    macros_json TEXT NOT NULL DEFAULT '[]',
    variant_key TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES code_files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_conditional_blocks_repository
ON code_conditional_blocks(repository_id, variant_key);
CREATE INDEX IF NOT EXISTS idx_code_conditional_blocks_file
ON code_conditional_blocks(file_id);

CREATE TABLE IF NOT EXISTS code_variable_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    function_name TEXT NOT NULL DEFAULT '',
    variable_name TEXT NOT NULL,
    access_type TEXT NOT NULL DEFAULT 'read',
    line INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES code_files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_variable_references_variable
ON code_variable_references(repository_id, variable_name);
CREATE INDEX IF NOT EXISTS idx_code_variable_references_function
ON code_variable_references(repository_id, function_name);
CREATE INDEX IF NOT EXISTS idx_code_variable_references_file
ON code_variable_references(file_id);

CREATE TABLE IF NOT EXISTS code_index_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    file_count INTEGER NOT NULL DEFAULT 0,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    include_count INTEGER NOT NULL DEFAULT 0,
    changed_file_count INTEGER NOT NULL DEFAULT 0,
    reused_file_count INTEGER NOT NULL DEFAULT 0,
    skipped_unchanged_count INTEGER NOT NULL DEFAULT 0,
    deleted_file_count INTEGER NOT NULL DEFAULT 0,
    skipped_after_limit INTEGER NOT NULL DEFAULT 0,
    skipped_by_dir INTEGER NOT NULL DEFAULT 0,
    batch_size INTEGER NOT NULL DEFAULT 0,
    batch_count INTEGER NOT NULL DEFAULT 0,
    parser TEXT NOT NULL DEFAULT 'regex',
    parser_confidence REAL NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    limitations_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(repository_id) REFERENCES code_repositories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_index_runs_repository
ON code_index_runs(repository_id, id);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT NOT NULL,
    problem TEXT NOT NULL,
    lesson TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate',
    source_decision_uid TEXT NOT NULL DEFAULT '',
    lesson_type TEXT NOT NULL DEFAULT 'conversation_lesson',
    expected_behavior TEXT NOT NULL DEFAULT '',
    anti_behavior TEXT NOT NULL DEFAULT '',
    validation_query TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'project',
    failure_type TEXT NOT NULL DEFAULT '',
    applicability_json TEXT NOT NULL DEFAULT '{}',
    counterexamples_json TEXT NOT NULL DEFAULT '[]',
    regression_case_uid TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    superseded_by TEXT NOT NULL DEFAULT '',
    last_replay_status TEXT NOT NULL DEFAULT '',
    last_replay_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    task_uid TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    prompt_excerpt TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT '',
    parsed_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    duration_s REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    prompt_version_id TEXT NOT NULL DEFAULT '',
    policy_version_id TEXT NOT NULL DEFAULT '',
    contract_version_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personal_tool_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_uid TEXT NOT NULL UNIQUE,
    task_uid TEXT NOT NULL DEFAULT '',
    decision_uid TEXT NOT NULL DEFAULT '',
    project_id INTEGER,
    requirement_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    permission_snapshot_json TEXT NOT NULL DEFAULT '{}',
    side_effect_level TEXT NOT NULL DEFAULT 'read',
    status TEXT NOT NULL DEFAULT 'planned',
    error TEXT NOT NULL DEFAULT '',
    evidence_refs_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_tool_invocations_task_uid
ON personal_tool_invocations(task_uid);
CREATE INDEX IF NOT EXISTS idx_personal_tool_invocations_decision_uid
ON personal_tool_invocations(decision_uid);
CREATE INDEX IF NOT EXISTS idx_personal_tool_invocations_project_req
ON personal_tool_invocations(project_id, requirement_id);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    session_uid TEXT NOT NULL DEFAULT '',
    requirement_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    user_prompt TEXT NOT NULL DEFAULT '',
    normalized_intent_json TEXT NOT NULL DEFAULT '{}',
    constraints_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '[]',
    current_step TEXT NOT NULL DEFAULT '',
    source_run_id TEXT NOT NULL DEFAULT '',
    agent_run_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personal_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    active_source_uid TEXT NOT NULL DEFAULT '',
    active_draft_uid TEXT NOT NULL DEFAULT '',
    current_requirement_summary TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_personal_sessions_status_updated
ON personal_sessions(status, updated_at);

CREATE TABLE IF NOT EXISTS personal_session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_uid TEXT NOT NULL UNIQUE,
    session_uid TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_uid) REFERENCES personal_sessions(session_uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_session_messages_session
ON personal_session_messages(session_uid, id);

CREATE TABLE IF NOT EXISTS personal_session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid TEXT NOT NULL UNIQUE,
    session_uid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_uid) REFERENCES personal_sessions(session_uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_session_events_session
ON personal_session_events(session_uid, id);

CREATE TABLE IF NOT EXISTS personal_input_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    plain_text TEXT NOT NULL DEFAULT '',
    sections_json TEXT NOT NULL DEFAULT '[]',
    tables_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_input_sources_project_created
ON personal_input_sources(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_personal_input_sources_project_active
ON personal_input_sources(project_id, is_active, status);

CREATE TABLE IF NOT EXISTS personal_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    source_uid TEXT NOT NULL DEFAULT '',
    session_uid TEXT NOT NULL DEFAULT '',
    task_uid TEXT NOT NULL DEFAULT '',
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content_format TEXT NOT NULL DEFAULT 'markdown',
    current_revision INTEGER NOT NULL DEFAULT 1,
    derived_from_draft_uid TEXT NOT NULL DEFAULT '',
    lineage_stale INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    is_active INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_drafts_project_created
ON personal_drafts(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_personal_drafts_project_active
ON personal_drafts(project_id, is_active, status);

CREATE TABLE IF NOT EXISTS personal_draft_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_uid TEXT NOT NULL UNIQUE,
    draft_uid TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    revision_index INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(draft_uid, revision_index),
    FOREIGN KEY(draft_uid) REFERENCES personal_drafts(draft_uid) ON DELETE CASCADE,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_draft_revisions_draft
ON personal_draft_revisions(draft_uid, revision_index);

CREATE TABLE IF NOT EXISTS personal_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    skill_kind TEXT NOT NULL DEFAULT 'document',
    document_type TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    active_version_uid TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, name),
    UNIQUE(project_id, document_type),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_skills_project_status
ON personal_skills(project_id, status);

CREATE TABLE IF NOT EXISTS personal_skill_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_uid TEXT NOT NULL UNIQUE,
    skill_uid TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    version_index INTEGER NOT NULL,
    skill_markdown TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL,
    activated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(skill_uid, version_index),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(skill_uid) REFERENCES personal_skills(skill_uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_skill_versions_skill
ON personal_skill_versions(skill_uid, version_index);

CREATE TABLE IF NOT EXISTS personal_skill_eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_uid TEXT NOT NULL UNIQUE,
    skill_uid TEXT NOT NULL,
    version_uid TEXT NOT NULL DEFAULT '',
    project_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    score REAL NOT NULL DEFAULT 0,
    checks_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(skill_uid) REFERENCES personal_skills(skill_uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_skill_eval_runs_skill
ON personal_skill_eval_runs(skill_uid, id);

CREATE TABLE IF NOT EXISTS personal_skill_update_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    target_skill TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    proposed_change TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    evidence_refs_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate',
    source TEXT NOT NULL DEFAULT '',
    session_uid TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',
    review_comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_skill_update_candidates_project
ON personal_skill_update_candidates(project_id, status, id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_personal_views(conn)
        _ensure_compat_columns(conn)


def _ensure_personal_views(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE VIEW IF NOT EXISTS personal_artifact_drafts AS
        SELECT * FROM personal_drafts;

        CREATE VIEW IF NOT EXISTS personal_artifact_revisions AS
        SELECT * FROM personal_draft_revisions;
        """
    )


def _ensure_compat_columns(conn: sqlite3.Connection) -> None:
    _add_columns(
        conn,
        "projects",
        {
            "organization_id": "INTEGER",
            "owner_id": "INTEGER",
            "created_by": "INTEGER",
            "updated_by": "INTEGER",
        },
    )
    _add_columns(
        conn,
        "knowledge_items",
        {
            "use_count": "INTEGER NOT NULL DEFAULT 0",
            "helpful_count": "INTEGER NOT NULL DEFAULT 0",
            "unhelpful_count": "INTEGER NOT NULL DEFAULT 0",
            "last_used_at": "TEXT NOT NULL DEFAULT ''",
            "policy_effect_scope": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_columns(
        conn,
        "memory_candidates",
        {
            "source_decision_uid": "TEXT NOT NULL DEFAULT ''",
            "lesson_type": "TEXT NOT NULL DEFAULT 'conversation_lesson'",
            "expected_behavior": "TEXT NOT NULL DEFAULT ''",
            "anti_behavior": "TEXT NOT NULL DEFAULT ''",
            "validation_query": "TEXT NOT NULL DEFAULT ''",
            "scope": "TEXT NOT NULL DEFAULT 'project'",
            "failure_type": "TEXT NOT NULL DEFAULT ''",
            "applicability_json": "TEXT NOT NULL DEFAULT '{}'",
            "counterexamples_json": "TEXT NOT NULL DEFAULT '[]'",
            "regression_case_uid": "TEXT NOT NULL DEFAULT ''",
            "expires_at": "TEXT NOT NULL DEFAULT ''",
            "superseded_by": "TEXT NOT NULL DEFAULT ''",
            "last_replay_status": "TEXT NOT NULL DEFAULT ''",
            "last_replay_at": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_columns(
        conn,
        "llm_call_logs",
        {
            "prompt_version_id": "TEXT NOT NULL DEFAULT ''",
            "policy_version_id": "TEXT NOT NULL DEFAULT ''",
            "contract_version_id": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_columns(
        conn,
        "personal_drafts",
        {
            "session_uid": "TEXT NOT NULL DEFAULT ''",
            "task_uid": "TEXT NOT NULL DEFAULT ''",
            "derived_from_draft_uid": "TEXT NOT NULL DEFAULT ''",
            "lineage_stale": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _add_columns(
        conn,
        "agent_tasks",
        {
            "session_uid": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _backfill_personal_draft_sessions(conn)
    _add_columns(
        conn,
        "code_files",
        {
            "source_preview": "TEXT NOT NULL DEFAULT ''",
        },
    )
    skill_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personal_skills)").fetchall()}
    if "document_type" not in skill_columns:
        conn.execute("ALTER TABLE personal_skills ADD COLUMN document_type TEXT NOT NULL DEFAULT ''")
    if "artifact_type" in skill_columns:
        conn.execute("UPDATE personal_skills SET document_type=artifact_type WHERE document_type=''")
    _ensure_compat_indexes(conn)


def _backfill_personal_draft_sessions(conn: sqlite3.Connection) -> None:
    draft_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personal_drafts)").fetchall()}
    if "session_uid" not in draft_columns:
        return
    try:
        messages = conn.execute(
            """
            SELECT session_uid, metadata_json
            FROM personal_session_messages
            WHERE role='assistant' AND metadata_json LIKE '%draft_uid%'
            ORDER BY id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return
    for message in messages:
        try:
            metadata = json.loads(str(message["metadata_json"] or "{}"))
        except Exception:
            continue
        draft = metadata.get("draft") if isinstance(metadata, dict) else None
        if not isinstance(draft, dict):
            continue
        draft_uid = str(draft.get("draft_uid") or "").strip()
        session_uid = str(message["session_uid"] or "").strip()
        if not draft_uid or not session_uid:
            continue
        conn.execute(
            """
            UPDATE personal_drafts
            SET session_uid=CASE WHEN session_uid='' THEN ? ELSE session_uid END,
                updated_at=updated_at
            WHERE draft_uid=?
            """,
            (session_uid, draft_uid),
        )
    try:
        sessions = conn.execute(
            """
            SELECT session_uid
            FROM personal_sessions
            WHERE status='active' AND active_draft_uid=''
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return
    for session in sessions:
        session_uid = str(session["session_uid"] or "")
        draft = conn.execute(
            """
            SELECT draft_uid
            FROM personal_drafts
            WHERE session_uid=? AND is_active=1 AND status IN ('active', 'quality_failed')
            ORDER BY updated_at DESC, id DESC LIMIT 1
            """,
            (session_uid,),
        ).fetchone()
        if draft is None:
            continue
        conn.execute(
            "UPDATE personal_sessions SET active_draft_uid=? WHERE session_uid=? AND active_draft_uid=''",
            (str(draft["draft_uid"] or ""), session_uid),
        )


def _ensure_compat_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_item_uid "
        "ON knowledge_search_entries(source_kind, item_uid)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_project_session_status "
        "ON agent_tasks(project_id, session_uid, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_personal_drafts_project_task_type_status "
        "ON personal_drafts(project_id, task_uid, document_type, status)"
    )


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
