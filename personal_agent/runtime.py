from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now

from .artifact_generation import propose_personal_artifact, revise_personal_artifact
from .content_guard import assert_personal_content_clean, personal_forbidden_hits
from .context_builder import PersonalContextBuilder
from .intent_router import PersonalIntentRouter
from .input_documents import activate_input_sources
from .knowledge_recall import billable_memory_item_uids, record_recall_feedback, safe_recall_prompt_item
from .knowledge_learning import record_personal_feedback, review_latest_session_candidate
from .learning_reflector import PersonalLearningReflector
from .policy_guard import apply_personal_policy
from .skill_reflector import PersonalSkillReflector
from .skill_update_candidates import (
    create_skill_update_candidate,
    list_skill_update_candidates,
    record_skill_update_candidate_review,
)


class PersonalRuntimeError(RuntimeError):
    pass


class PersonalRuntime:
    def __init__(self, db_path: Path, workspace: Path, project_id: int):
        self.db_path = db_path
        self.workspace = workspace
        self.project_id = project_id
        self.context_builder = PersonalContextBuilder(db_path, project_id)
        self.intent_router = PersonalIntentRouter(db_path, project_id)
        self.learning_reflector = PersonalLearningReflector(db_path, project_id)
        self.skill_reflector = PersonalSkillReflector(db_path, project_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM personal_sessions
                WHERE status='active'
                ORDER BY id DESC
                """
            ).fetchall()
        return [self._session_payload(row, include_messages=False) for row in rows]

    def get_session(self, session_uid: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM personal_sessions WHERE session_uid=? AND status='active'",
                (session_uid,),
            ).fetchone()
            if row is None:
                raise PersonalRuntimeError("session not found")
            messages = conn.execute(
                "SELECT * FROM personal_session_messages WHERE session_uid=? ORDER BY id",
                (session_uid,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM personal_session_events WHERE session_uid=? ORDER BY id",
                (session_uid,),
            ).fetchall()
        payload = self._session_payload(row, include_messages=True)
        payload["messages"] = [self._message_payload(item) for item in messages]
        payload["events"] = [self._event_payload(item) for item in events]
        return payload

    def rename_session(self, session_uid: str, title: str) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise PersonalRuntimeError("title is required")
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE personal_sessions SET title=?, updated_at=? WHERE session_uid=? AND status='active'",
                (title, utc_now(), session_uid),
            )
            if cur.rowcount == 0:
                raise PersonalRuntimeError("session not found")
        return self.get_session(session_uid)

    def delete_session(self, session_uid: str) -> dict[str, Any]:
        now = utc_now()
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE personal_sessions SET status='deleted', archived_at=?, updated_at=? WHERE session_uid=? AND status='active'",
                (now, now, session_uid),
            )
            if cur.rowcount == 0:
                raise PersonalRuntimeError("session not found")
        return {"status": "deleted", "session_uid": session_uid}

    def turn(self, *, content: str, session_uid: str = "", source_uids: list[str] | None = None) -> dict[str, Any]:
        prompt = content.strip()
        if not prompt:
            raise PersonalRuntimeError("content is required")
        source_uids = list(dict.fromkeys(uid.strip() for uid in (source_uids or []) if uid.strip()))
        if len(source_uids) > 5:
            raise PersonalRuntimeError("at most 5 input files can be attached to one message")

        session = self._ensure_session(session_uid, prompt)
        session_uid = session["session_uid"]
        attachments: list[dict[str, Any]] = []
        if source_uids:
            attachments = _attachment_metadata(activate_input_sources(self.db_path, project_id=self.project_id, source_uids=source_uids))
        user_metadata = {"attachments": attachments} if attachments else {}
        self._append_message(session_uid, "user", prompt, user_metadata, utc_now())

        context = self.context_builder.build(session_uid=session_uid, prompt=prompt, source_uids=source_uids)
        route = apply_personal_policy(self.intent_router.route(context), context)
        skill_reflection = self.skill_reflector.reflect({**context, "prompt": prompt, "session_uid": session_uid})
        if skill_reflection.get("approval_intent") in {"approve_latest", "reject_latest"}:
            message = self._review_latest_skill_candidate_turn(session_uid=session_uid, prompt=prompt, reflection=skill_reflection, route=route)
            refreshed_context = self.context_builder.build(session_uid=session_uid, prompt=prompt)
            self._touch_session(session_uid, refreshed_context)
            return {"session": self.get_session(session_uid), "message": message}
        reflection = self.learning_reflector.reflect(context) if should_run_learning_reflector(context, route) else _skipped_learning_reflection(context)
        self._record_previous_memory_unhelpful_if_correction(session_uid, reflection)
        if reflection.get("approval_intent") in {"approve_latest", "reject_latest"}:
            message = self._review_latest_learning_turn(session_uid=session_uid, prompt=prompt, reflection=reflection, route=route)
        else:
            candidate = self._create_learning_candidate_if_needed(session_uid=session_uid, prompt=prompt, reflection=reflection)
            if candidate:
                reflection["candidate"] = candidate
            message = self._dispatch_turn(session_uid=session_uid, prompt=prompt, context=context, route=route, reflection=reflection)
            message = self._annotate_learning_reflection(message, reflection)
            skill_candidate = self._create_skill_candidate_if_needed(session_uid=session_uid, reflection=skill_reflection)
            if skill_candidate:
                message = self._annotate_skill_reflection(message, skill_reflection, skill_candidate)

        refreshed_context = self.context_builder.build(session_uid=session_uid, prompt=prompt)
        self._touch_session(session_uid, refreshed_context)
        return {"session": self.get_session(session_uid), "message": message}

    def _dispatch_turn(
        self,
        *,
        session_uid: str,
        prompt: str,
        context: dict[str, Any],
        route: dict[str, Any],
        reflection: dict[str, Any],
    ) -> dict[str, Any]:
        intent = str(route.get("intent") or "answer_only")
        if intent == "generate_document":
            return self._generate_document_turn(session_uid=session_uid, prompt=prompt, context=context, route=route, reflection=reflection)
        if intent == "revise_draft":
            return self._revise_draft_turn(session_uid=session_uid, prompt=prompt, context=context, route=route, reflection=reflection)
        if intent == "learn_feedback":
            return self._learn_feedback_turn(session_uid=session_uid, prompt=prompt, route=route, reflection=reflection)
        if intent in {"propose_code_patch", "run_validation"}:
            return self._tool_guidance_turn(session_uid=session_uid, prompt=prompt, route=route, reflection=reflection)
        mode = "input_source_analysis" if intent == "analyze_input_source" else str(route.get("answer_mode") or "general_chat")
        return self._answer_turn(session_uid=session_uid, prompt=prompt, context=context, route=route, mode=mode, reflection=reflection)

    def _ensure_session(self, session_uid: str, prompt: str) -> dict[str, Any]:
        if session_uid:
            try:
                return self.get_session(session_uid)
            except PersonalRuntimeError:
                pass
        now = utc_now()
        session_uid = f"session_{uuid4().hex}"
        title = _title_from_prompt(prompt)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO personal_sessions(session_uid, title, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, 'active', '{}', ?, ?)
                """,
                (session_uid, title, now, now),
            )
        return self.get_session(session_uid)

    def _append_message(
        self,
        session_uid: str,
        role: str,
        content: str,
        metadata: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        assert_personal_content_clean(content, label=f"{role} message")
        message_uid = f"msg_{uuid4().hex}"
        with connect(self.db_path) as conn:
            rowid = conn.execute(
                """
                INSERT INTO personal_session_messages(message_uid, session_uid, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_uid, session_uid, role, content, json_dumps(metadata), created_at or utc_now()),
            ).lastrowid
            row = conn.execute("SELECT * FROM personal_session_messages WHERE id=?", (rowid,)).fetchone()
        return self._message_payload(row)

    def _annotate_learning_reflection(self, message: dict[str, Any], reflection: dict[str, Any]) -> dict[str, Any]:
        candidate = reflection.get("candidate") if isinstance(reflection.get("candidate"), dict) else None
        if not reflection or (not candidate and not reflection.get("has_learning_signal")):
            return message
        metadata = dict(message.get("metadata") or {})
        metadata["learning_reflection"] = _learning_metadata(reflection, candidate)
        content = str(message.get("content") or "")
        if candidate and "已记录为待批准经验" not in content:
            content = content.rstrip() + "\n\n已记录为待批准经验，并会在当前会话先按它执行。"
        assert_personal_content_clean(content, label="assistant message")
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE personal_session_messages SET content=?, metadata_json=? WHERE message_uid=?",
                (content, json_dumps(metadata), message["message_uid"]),
            )
            row = conn.execute("SELECT * FROM personal_session_messages WHERE message_uid=?", (message["message_uid"],)).fetchone()
        return self._message_payload(row)

    def _annotate_skill_reflection(self, message: dict[str, Any], reflection: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(message.get("metadata") or {})
        metadata["skill_update_reflection"] = {
            "has_skill_update_signal": True,
            "candidate_id": candidate.get("id"),
            "status": candidate.get("status"),
            "target_skill": candidate.get("target_skill"),
            "confidence": reflection.get("confidence", 0.0),
            "llm": reflection.get("llm") or {},
        }
        content = str(message.get("content") or "").rstrip()
        content += f"\n\n已提出 Skill 修改候选：{candidate.get('target_skill')}。本会话会临时遵守该规则；只有用户批准后才会创建并激活新的 Skill 版本。"
        assert_personal_content_clean(content, label="assistant message")
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE personal_session_messages SET content=?, metadata_json=? WHERE message_uid=?",
                (content, json_dumps(metadata), message["message_uid"]),
            )
            row = conn.execute("SELECT * FROM personal_session_messages WHERE message_uid=?", (message["message_uid"],)).fetchone()
        return self._message_payload(row)

    def _answer_turn(
        self,
        *,
        session_uid: str,
        prompt: str,
        context: dict[str, Any],
        route: dict[str, Any],
        mode: str,
        reflection: dict[str, Any],
    ) -> dict[str, Any]:
        if route.get("router_source") == "fallback" or (route.get("policy") or {}).get("fallback"):
            answer = _safe_fallback_answer(context, route)
            return self._append_message(session_uid, "assistant", answer, {"context": "general", "intent_route": _intent_metadata(route), "fallback": True})

        llm = self._llm_answer(prompt=prompt, context=context, mode=mode)
        if llm:
            context_name = "input_source" if mode == "input_source_analysis" else "general"
            billable_memory_uids = llm["billable_memory_item_uids"]
            metadata = {
                "context": context_name,
                "llm": llm["llm"],
                "intent_route": _intent_metadata(route),
                "injected_knowledge_item_uids": llm["injected_knowledge_item_uids"],
                "injected_memory_item_uids": llm["injected_memory_item_uids"],
                "billable_memory_item_uids": billable_memory_uids,
                "memory_item_uids_used": llm["memory_item_uids_used"],
            }
            message = self._append_message(
                session_uid,
                "assistant",
                llm["answer"],
                metadata,
            )
            self._record_injected_recall_use(llm["injected_knowledge_item_uids"] + billable_memory_uids)
            return message
        answer = "LLM 回答调用不可用，本轮只保留安全回答路径；没有生成草稿、没有修改代码、没有执行工具。"
        return self._append_message(session_uid, "assistant", answer, {"context": "general", "intent_route": _intent_metadata(route), "fallback": True})

    def _generate_document_turn(
        self,
        *,
        session_uid: str,
        prompt: str,
        context: dict[str, Any],
        route: dict[str, Any],
        reflection: dict[str, Any],
    ) -> dict[str, Any]:
        document_type = str(route.get("target_document_type") or "requirement_analysis_report")
        draft = propose_personal_artifact(
            self.db_path,
            workspace=self.workspace,
            project_id=self.project_id,
            prompt=prompt,
            document_type=document_type,
            source_uids=context["active_source_uids"],
            session_task_uid=session_uid,
            make_active=True,
        )
        answer = _draft_generation_answer(draft)
        return self._append_message(session_uid, "assistant", answer, {"context": "document_generation", "draft": draft, "intent_route": _intent_metadata(route)})

    def _revise_draft_turn(
        self,
        *,
        session_uid: str,
        prompt: str,
        context: dict[str, Any],
        route: dict[str, Any],
        reflection: dict[str, Any],
    ) -> dict[str, Any]:
        draft_uid = str((context.get("active_draft") or {}).get("draft_uid") or "")
        draft = revise_personal_artifact(
            self.db_path,
            project_id=self.project_id,
            workspace=self.workspace,
            draft_uid=draft_uid,
            feedback=prompt,
            session_task_uid=session_uid,
            make_active=True,
        )
        answer = f"已根据反馈修订《{draft['title']}》草稿 v{draft['current_revision']}，draft_uid={draft['draft_uid']}。"
        return self._append_message(session_uid, "assistant", answer, {"context": "draft_revision", "draft": draft, "intent_route": _intent_metadata(route)})

    def _learn_feedback_turn(self, *, session_uid: str, prompt: str, route: dict[str, Any], reflection: dict[str, Any]) -> dict[str, Any]:
        candidate = reflection.get("candidate") if isinstance(reflection.get("candidate"), dict) else None
        if not candidate:
            candidate = record_personal_feedback(
                self.db_path,
                project_id=self.project_id,
                session_uid=session_uid,
                feedback=prompt,
                source="personal_intent_route",
                add_to_regression=False,
            )
        answer = f"已记录为待批准经验候选：{candidate.get('title') or candidate.get('id')}。批准后才会沉淀为长期规则。"
        return self._append_message(session_uid, "assistant", answer, {"context": "learning_feedback", "learning_candidate": candidate, "intent_route": _intent_metadata(route)})

    def _review_latest_skill_candidate_turn(self, *, session_uid: str, prompt: str, reflection: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        decision = "approve" if reflection.get("approval_intent") == "approve_latest" else "reject"
        candidates = list_skill_update_candidates(self.db_path, project_id=self.project_id, status="candidate")
        candidate = next((item for item in candidates if item.get("session_uid") == session_uid), candidates[0] if candidates else None)
        if not candidate:
            answer = "没有可审批的 Skill 修改候选；本轮不会修改任何 Skill。"
            return self._append_message(session_uid, "assistant", answer, {"context": "skill_update_review", "intent_route": _intent_metadata(route)})
        reviewed = record_skill_update_candidate_review(
            self.db_path,
            project_id=self.project_id,
            candidate_id=int(candidate["id"]),
            reviewer="personal_chat_user",
            decision=decision,
            comment=prompt,
        )
        if decision == "approve":
            answer = f"已批准 Skill 修改候选，并创建/激活新的 Skill 版本：{reviewed.get('target_skill')}。"
        else:
            answer = f"已驳回刚才的 Skill 修改候选：{reviewed.get('target_skill')}；不会修改 Skill 文件或长期版本。"
        return self._append_message(session_uid, "assistant", answer, {"context": "skill_update_review", "skill_update_candidate": reviewed, "intent_route": _intent_metadata(route)})

    def _tool_guidance_turn(self, *, session_uid: str, prompt: str, route: dict[str, Any], reflection: dict[str, Any]) -> dict[str, Any]:
        if route.get("intent") == "propose_code_patch":
            answer = "我已识别到这是代码修改/patch 候选意图。本阶段不会自动写项目文件；请先配置代码库并明确候选 patch 目标，我会只生成可评审候选。"
        else:
            answer = "我已识别到这是验证/测试意图。本阶段不会自动执行命令；请先在代码库配置中保存白名单命令，并在验证面板中确认执行。"
        return self._append_message(session_uid, "assistant", answer, {"context": "tool_guidance", "intent_route": _intent_metadata(route)})

    def _review_latest_learning_turn(self, *, session_uid: str, prompt: str, reflection: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        decision = "approve" if reflection.get("approval_intent") == "approve_latest" else "reject"
        reviewed = review_latest_session_candidate(
            self.db_path,
            project_id=self.project_id,
            session_uid=session_uid,
            decision=decision,
            reviewer="personal_chat_user",
            comment=prompt,
        )
        if decision == "approve":
            answer = f"已批准这条经验，并沉淀为长期记忆：{reviewed.get('lesson') or reviewed.get('title') or reviewed.get('id')}。"
        else:
            answer = f"已驳回刚才那条经验：{reviewed.get('lesson') or reviewed.get('title') or reviewed.get('id')}。"
        return self._append_message(
            session_uid,
            "assistant",
            answer,
            {"context": "learning_review", "learning_candidate": reviewed, "intent_route": _intent_metadata(route), "learning_reflection": _learning_metadata(reflection, reviewed)},
        )

    def _create_learning_candidate_if_needed(self, *, session_uid: str, prompt: str, reflection: dict[str, Any]) -> dict[str, Any] | None:
        if not reflection.get("has_learning_signal"):
            return None
        return record_personal_feedback(
            self.db_path,
            project_id=self.project_id,
            session_uid=session_uid,
            feedback=prompt,
            source="personal_learning_reflect",
            corrected_behavior=str(reflection.get("candidate_lesson") or ""),
            anti_behavior=str(reflection.get("anti_behavior") or ""),
            feedback_type=str(reflection.get("feedback_type") or "personal_behavior_feedback"),
            scope=str(reflection.get("scope") or "project"),
            add_to_regression=False,
        )

    def _create_skill_candidate_if_needed(self, *, session_uid: str, reflection: dict[str, Any]) -> dict[str, Any] | None:
        if not reflection.get("has_skill_update_signal"):
            return None
        target_skill = str(reflection.get("target_skill") or "").strip()
        proposed_change = str(reflection.get("proposed_change") or "").strip()
        if not target_skill or not proposed_change:
            return None
        return create_skill_update_candidate(
            self.db_path,
            project_id=self.project_id,
            target_skill=target_skill,
            reason=str(reflection.get("reason") or ""),
            proposed_change=proposed_change,
            risk=str(reflection.get("risk") or ""),
            evidence_refs={"reflection": {"confidence": reflection.get("confidence"), "change_type": reflection.get("change_type")}},
            session_uid=session_uid,
            source="personal_skill_reflect",
        )

    def _llm_answer(self, *, prompt: str, context: dict[str, Any], mode: str) -> dict[str, Any] | None:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        system_prompt = "\n".join(
            [
                "You are a personal natural-language development Agent.",
                "Answer in Chinese.",
                "Use the active input materials, recent conversation, knowledge refs, and code evidence if present.",
                "Apply the user's approved long-term lessons below; they override default behavior.",
                "Do not claim you generated a draft unless the caller explicitly generated one.",
                "Do not modify files, apply patches, or create release records in this answer path.",
                "Return strict JSON: {\"answer\": \"...\", \"used_sources\": [\"...\"], \"memory_item_uids_used\": [\"...\"], \"limitations\": [\"...\"]}.",
            ]
        )
        injected_knowledge = [safe_recall_prompt_item(item, forbidden_text_checker=personal_forbidden_hits) for item in (context.get("knowledge") or [])[:5]]
        injected_knowledge = [item for item in injected_knowledge if item]
        injected_memories = [safe_recall_prompt_item(item, forbidden_text_checker=personal_forbidden_hits) for item in (context.get("memories") or [])[:5]]
        injected_memories = [item for item in injected_memories if item]
        user_prompt = json_dumps(
            {
                "mode": mode,
                "user_message": prompt,
                "active_sources": [
                    {
                        "source_uid": item.get("source_uid"),
                        "title": item.get("title"),
                        "source_type": item.get("source_type"),
                        "plain_text_excerpt": str(item.get("plain_text") or "")[:4000],
                        "sections": item.get("sections") or [],
                        "tables": item.get("tables") or [],
                    }
                    for item in (context.get("sources") or [])[:3]
                ],
                "active_draft": context.get("active_draft") or {},
                "recent_messages": context.get("recent_messages") or [],
                "knowledge_refs": context.get("knowledge_refs") or [],
                "knowledge": injected_knowledge,
                "memory_refs": context.get("memory_refs") or [],
                "memories": injected_memories,
                "pending_memory_candidates": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "lesson": item.get("lesson"),
                        "expected_behavior": item.get("expected_behavior"),
                        "anti_behavior": item.get("anti_behavior"),
                        "status": item.get("status"),
                    }
                    for item in (context.get("pending_memory_candidates") or [])[:5]
                ],
                "code_evidence": context.get("code_evidence") or {},
                "requirement_summary": context.get("requirement_summary") or "",
                "boundary": {
                    "answer_only": True,
                    "creates_draft": False,
                    "writes_files": False,
                    "applies_patch": False,
                },
            }
        )
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_chat_answer",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                project_id=self.project_id,
                task_uid=str(context.get("session_uid") or ""),
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error"):
            return None
        answer = str(result.parsed.get("answer") or "").strip()
        if not answer:
            return None
        injected_memory_uids = [str(item.get("item_uid") or "") for item in injected_memories if str(item.get("item_uid") or "").strip()]
        billable_memory_uids = billable_memory_item_uids(injected_memories)
        memory_item_uids_used = _valid_used_uids(result.parsed.get("memory_item_uids_used"), billable_memory_uids)
        return {
            "answer": answer,
            "injected_knowledge_item_uids": [str(item.get("item_uid") or "") for item in injected_knowledge if str(item.get("item_uid") or "").strip()],
            "injected_memory_item_uids": injected_memory_uids,
            "injected_memories": injected_memories,
            "billable_memory_item_uids": billable_memory_uids,
            "memory_item_uids_used": memory_item_uids_used,
            "llm": {
                "call_id": result.call_id,
                "provider": result.provider,
                "model": result.model,
                "status": result.status,
                "purpose": "personal_chat_answer",
            },
        }

    def _record_injected_recall_use(self, item_uids: list[str]) -> None:
        for item_uid in dict.fromkeys(uid for uid in item_uids if uid):
            try:
                record_recall_feedback(self.db_path, item_uid=item_uid, event="use")
            except ValueError:
                continue

    def _record_previous_memory_unhelpful_if_correction(self, session_uid: str, reflection: dict[str, Any]) -> None:
        if not reflection.get("has_learning_signal") or reflection.get("feedback_type") != "correction":
            return
        if reflection.get("approval_intent") != "none":
            return
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT metadata_json
                FROM personal_session_messages
                WHERE session_uid=? AND role='assistant'
                ORDER BY id DESC LIMIT 1
                """,
                (session_uid,),
            ).fetchone()
        if row is None:
            return
        metadata = _loads_json(row["metadata_json"], {})
        item_uids = metadata.get("injected_memory_item_uids") if isinstance(metadata, dict) else []
        used_uids = metadata.get("memory_item_uids_used") if isinstance(metadata, dict) else []
        correction_uids = [str(uid) for uid in (used_uids or []) if str(uid).strip()]
        if not correction_uids:
            billable_uids = metadata.get("billable_memory_item_uids") if isinstance(metadata, dict) else []
            fallback_uids = billable_uids if isinstance(billable_uids, list) else item_uids
            correction_uids = [str(uid) for uid in (fallback_uids or []) if str(uid).strip()]
        for item_uid in dict.fromkeys(correction_uids):
            try:
                record_recall_feedback(self.db_path, item_uid=item_uid, event="unhelpful")
            except ValueError:
                continue

    def _touch_session(self, session_uid: str, context: dict[str, Any]) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE personal_sessions
                SET active_source_uid=?, active_draft_uid=?, current_requirement_summary=?, updated_at=?
                WHERE session_uid=?
                """,
                (
                    (context["active_source_uids"] or [""])[0],
                    str((context.get("active_draft") or {}).get("draft_uid") or ""),
                    context.get("requirement_summary") or "",
                    utc_now(),
                    session_uid,
                ),
            )

    def _session_payload(self, row: Any, *, include_messages: bool) -> dict[str, Any]:
        payload = {
            "session_uid": row["session_uid"],
            "title": row["title"],
            "status": row["status"],
            "active_source_uid": row["active_source_uid"],
            "active_draft_uid": row["active_draft_uid"],
            "current_requirement_summary": row["current_requirement_summary"],
            "metadata": _loads_json(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_messages:
            payload["messages"] = []
            payload["events"] = []
        return payload

    def _message_payload(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "message_uid": row["message_uid"],
            "session_uid": row["session_uid"],
            "role": row["role"],
            "content": row["content"],
            "metadata": _loads_json(row["metadata_json"], {}),
            "created_at": row["created_at"],
        }

    def _event_payload(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_uid": row["event_uid"],
            "session_uid": row["session_uid"],
            "event_type": row["event_type"],
            "title": row["title"],
            "payload": _loads_json(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    def llm_status(self) -> dict[str, Any]:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        return gateway_class(self.db_path).status()


def _intent_metadata(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": route.get("intent"),
        "confidence": route.get("confidence"),
        "target_document_type": route.get("target_document_type", ""),
        "answer_mode": route.get("answer_mode", ""),
        "router_source": route.get("router_source", ""),
        "reason": route.get("reason", ""),
        "llm": route.get("llm") or {},
        "policy": route.get("policy") or {"allowed": True, "fallback": False, "reason": ""},
    }


def _valid_used_uids(value: Any, allowed_uids: list[str]) -> list[str]:
    allowed = {str(uid) for uid in allowed_uids if str(uid).strip()}
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        uid = str(item or "").strip()
        if uid in allowed and uid not in result:
            result.append(uid)
    return result


def should_run_learning_reflector(context: dict[str, Any], route: dict[str, Any]) -> bool:
    intent = str(route.get("intent") or "")
    if intent in {"generate_document", "revise_draft", "propose_code_patch", "run_validation", "learn_feedback", "analyze_input_source"}:
        return True
    prompt = str(context.get("prompt") or "").strip()
    compact = "".join(prompt.lower().split())
    if not compact:
        return False
    if _has_explicit_learning_signal(compact):
        return True
    if _is_low_value_chat(compact):
        return False
    return True


def _has_explicit_learning_signal(compact: str) -> bool:
    signal_terms = (
        "以后",
        "下次",
        "不要固定模板",
        "按这种方式回答",
        "你理解错了",
        "刚才这样更好",
        "这个修改是对的",
        "以后都这样",
        "下次不要这样",
        "批准这条经验",
        "驳回刚才那条",
        "记住刚才那条",
    )
    return any(term in compact for term in signal_terms)


def _is_low_value_chat(compact: str) -> bool:
    if not compact:
        return True
    confirmations = {"好", "好的", "可以", "行", "嗯", "嗯嗯", "ok", "okay", "收到", "明白", "了解", "是的", "对", "没问题"}
    thanks = {"谢谢", "感谢", "辛苦了", "thanks", "thankyou", "thx"}
    greetings = {"你好", "hi", "hello", "早", "早上好", "晚上好"}
    if compact in confirmations | thanks | greetings:
        return True
    if len(compact) <= 8 and any(term in compact for term in confirmations | thanks | greetings):
        return True
    return False


def _skipped_learning_reflection(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_learning_signal": False,
        "confidence": 0.0,
        "feedback_type": "none",
        "scope": "project",
        "candidate_lesson": "",
        "anti_behavior": "",
        "approval_intent": "none",
        "reason": "learning_reflector_skipped",
        "skip_reason": "runtime_gate",
        "implicit_learning_events": [],
        "llm": {
            "call_id": None,
            "provider": "",
            "model": "",
            "status": "skipped",
            "purpose": "personal_learning_reflect",
        },
    }


def _attachment_metadata(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for source in sources:
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        original_name = str(metadata.get("original_name") or "").strip()
        title = str(source.get("title") or original_name or source.get("source_uid") or "")
        attachments.append(
            {
                "source_uid": str(source.get("source_uid") or ""),
                "title": title,
                "source_type": str(source.get("source_type") or ""),
                "original_name": original_name or title,
            }
        )
    return attachments


def _learning_metadata(reflection: dict[str, Any], candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "has_learning_signal": bool(reflection.get("has_learning_signal")),
        "candidate_id": candidate.get("id") if candidate else None,
        "status": candidate.get("status") if candidate else "",
        "scope": reflection.get("scope", ""),
        "feedback_type": reflection.get("feedback_type", ""),
        "approval_intent": reflection.get("approval_intent", "none"),
        "confidence": reflection.get("confidence", 0.0),
        "llm": reflection.get("llm") or {},
    }


def _safe_fallback_answer(context: dict[str, Any], route: dict[str, Any]) -> str:
    reason = str((route.get("policy") or {}).get("reason") or route.get("reason") or "LLM 路由不可用")
    llm_error = _llm_error_notice(route)
    if context.get("active_source_uids"):
        return f"{reason}{llm_error} 本轮不会生成草稿、不会修改代码、不会执行工具；你可以继续补充材料，或在 LLM 路由恢复后重新发起生成请求。"
    return f"{reason}{llm_error} 当前没有激活输入材料；请先粘贴或上传需求资料。本轮没有生成草稿、没有修改代码、没有执行工具。"


def _draft_generation_answer(draft: dict[str, Any]) -> str:
    generation = draft.get("generation") if isinstance(draft.get("generation"), dict) else {}
    quality = generation.get("quality") if isinstance(generation.get("quality"), dict) else {}
    passed = quality.get("passed")
    if passed is False:
        failures = quality.get("blocking_failures") if isinstance(quality.get("blocking_failures"), list) else []
        first_failure = str(failures[0]) if failures else "质量门未通过"
        return (
            f"已生成《{draft['title']}》待修复草稿 v{draft['current_revision']}，draft_uid={draft['draft_uid']}。"
            f"质量门未通过：{first_failure}。草稿已保留，可在 draft 面板查看质量项后修订或重新生成。"
        )
    return f"已生成《{draft['title']}》草稿 v{draft['current_revision']}，draft_uid={draft['draft_uid']}。"


def _llm_error_notice(route: dict[str, Any]) -> str:
    llm = route.get("llm") if isinstance(route.get("llm"), dict) else {}
    error = str(llm.get("error") or "").strip()
    if not error and str(llm.get("status") or "").lower() != "failed":
        return ""

    provider = str(llm.get("provider") or "").strip()
    model = str(llm.get("model") or "").strip()
    call_id = llm.get("call_id")
    target = "/".join(part for part in [provider, model] if part)
    prefix = f" LLM 调用失败"
    if target:
        prefix += f"（{target}"
        if call_id:
            prefix += f"，call_id={call_id}"
        prefix += "）"
    elif call_id:
        prefix += f"（call_id={call_id}）"

    summary = _summarize_llm_error(error)
    return f"{prefix}：{summary}。"


def _summarize_llm_error(error: str) -> str:
    compact = " ".join(error.split())
    lower = compact.lower()
    if "insufficient balance" in lower or "llm http 402" in lower or "http 402" in lower:
        return "LLM 服务返回 402，余额或额度不足；请充值、更新密钥，或切换到可用模型后重试"
    if "llm_not_configured" in lower or "not configured" in lower or "requires " in lower:
        return _truncate_text(compact, 180)

    message = _extract_llm_error_message(compact)
    if message:
        return _truncate_text(message, 180)
    return _truncate_text(compact or "未知错误", 180)


def _extract_llm_error_message(error: str) -> str:
    json_start = error.find("{")
    json_end = error.rfind("}")
    if json_start < 0 or json_end <= json_start:
        return ""
    try:
        payload = json.loads(error[json_start : json_end + 1])
    except Exception:
        return ""
    detail = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or "").strip()
        code = str(detail.get("code") or "").strip()
        if message and code:
            return f"{message}（{code}）"
        return message or code
    return ""


def _truncate_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _title_from_prompt(prompt: str) -> str:
    cleaned = prompt.strip().replace("\n", " ")
    return cleaned[:28] or "新会话"


def _loads_json(text: str, default: Any) -> Any:
    import json

    try:
        return json.loads(text or "")
    except Exception:
        return default
