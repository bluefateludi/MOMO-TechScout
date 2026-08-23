from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone

from paper_agent.techscout.decision_context import DecisionContext
from paper_agent.techscout.models import ResearchPlan
from paper_agent.techscout.planning import (
    CriteriaDraftPlanner,
    CriteriaPlanningInput,
    PlannerDraft,
    PocCheckDraft,
    RequirementKind,
    ResearchQuestionDraft,
    SelectionCriteriaContract,
    SelectionCriteriaService,
    UserRequirement,
)
from paper_agent.techscout.workflow.contracts import (
    DecisionWorkflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowState,
)
from paper_agent.techscout.workflow.store import DecisionWorkflowStore


class WorkflowTransitionError(RuntimeError):
    code = "invalid_workflow_transition"


class ResearchNotReadyError(RuntimeError):
    code = "research_not_ready"


class DeterministicCriteriaDraftPlanner:
    """Offline adapter used by the HTTP composition until a planner is injected."""

    def plan(self, planning_input: CriteriaPlanningInput) -> PlannerDraft:
        questions = tuple(
            ResearchQuestionDraft(
                question=f"What authoritative evidence establishes: {item.statement}",
                requirement_ids=(item.requirement_id,),
            )
            for item in planning_input.requirements
        )
        checks = tuple(
            PocCheckDraft(
                check=f"Verify with a bounded allowlisted PoC: {item.statement}",
                requirement_ids=(item.requirement_id,),
            )
            for item in planning_input.requirements
            if item.kind is RequirementKind.HARD_CONSTRAINT
        )
        return PlannerDraft(research_questions=questions, poc_checks=checks)


class DecisionWorkflowService:
    """Auditable state machine connecting context, requirements, criteria, and research."""

    def __init__(
        self,
        store: DecisionWorkflowStore,
        *,
        planner: CriteriaDraftPlanner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._criteria = SelectionCriteriaService(planner)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, run_id: str, context: DecisionContext) -> DecisionWorkflow:
        now = self._clock()
        workflow = DecisionWorkflow(
            run_id=run_id,
            state=WorkflowState.DRAFT_CONTEXT,
            version=1,
            decision_context=context,
            created_at=now,
            updated_at=now,
        )
        return self._store.initialize(
            workflow,
            context_hash=_hash(context.model_dump(mode="json")),
            event=self._event(
                workflow,
                event_type=WorkflowEventType.WORKFLOW_CREATED,
                command_id=None,
                from_state=None,
            ),
        )

    def get(self, run_id: str) -> DecisionWorkflow:
        return self._store.get(run_id)

    def review_requirements(
        self,
        run_id: str,
        *,
        command_id: str,
        requirements: tuple[UserRequirement, ...],
    ) -> DecisionWorkflow:
        CriteriaPlanningInput(run_id=run_id, requirements=requirements)
        payload_hash = _hash({
            "operation": "review_requirements",
            "requirements": [item.model_dump(mode="json") for item in requirements],
        })
        replay = self._store.receipt(
            run_id, command_id=command_id, payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        current = self.get(run_id)
        if current.state not in {
            WorkflowState.DRAFT_CONTEXT,
            WorkflowState.REQUIREMENTS_REVIEW,
        }:
            raise WorkflowTransitionError(
                "requirements may be revised only before their confirmation"
            )
        event_type = (
            WorkflowEventType.REQUIREMENTS_REVIEW_STARTED
            if current.state is WorkflowState.DRAFT_CONTEXT
            else WorkflowEventType.REQUIREMENTS_REVIEW_REVISED
        )
        canonical_requirements = tuple(
            sorted(requirements, key=lambda item: item.requirement_id)
        )
        updated = current.model_copy(update={
            "state": WorkflowState.REQUIREMENTS_REVIEW,
            "version": current.version + 1,
            "requirements": canonical_requirements,
            "updated_at": self._clock(),
        })
        return self._transition(
            current,
            updated,
            command_id=command_id,
            payload_hash=payload_hash,
            event_type=event_type,
        )

    def confirm_requirements(self, run_id: str, *, command_id: str) -> DecisionWorkflow:
        payload_hash = _hash({"operation": "confirm_requirements"})
        replay = self._store.receipt(
            run_id, command_id=command_id, payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        current = self.get(run_id)
        self._require_state(current, WorkflowState.REQUIREMENTS_REVIEW)
        criteria = self._criteria.create(CriteriaPlanningInput(
            run_id=run_id,
            requirements=current.requirements,
        ))
        plan = _research_plan(criteria)
        updated = current.model_copy(update={
            "state": WorkflowState.CRITERIA_CONFIRMATION,
            "version": current.version + 1,
            "requirements_confirmed": True,
            "selection_criteria": criteria,
            "research_plan": plan,
            "updated_at": self._clock(),
        })
        return self._transition(
            current,
            updated,
            command_id=command_id,
            payload_hash=payload_hash,
            event_type=WorkflowEventType.REQUIREMENTS_CONFIRMED,
        )

    def confirm_criteria(
        self,
        run_id: str,
        *,
        command_id: str,
        contract_id: str,
    ) -> DecisionWorkflow:
        payload_hash = _hash({
            "operation": "confirm_criteria",
            "contract_id": contract_id,
        })
        replay = self._store.receipt(
            run_id, command_id=command_id, payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        current = self.get(run_id)
        self._require_state(current, WorkflowState.CRITERIA_CONFIRMATION)
        if current.selection_criteria is None or current.selection_criteria.contract_id != contract_id:
            raise WorkflowTransitionError("criteria confirmation does not match the current contract")
        updated = current.model_copy(update={
            "state": WorkflowState.RESEARCH_READY,
            "version": current.version + 1,
            "updated_at": self._clock(),
        })
        return self._transition(
            current,
            updated,
            command_id=command_id,
            payload_hash=payload_hash,
            event_type=WorkflowEventType.CRITERIA_CONFIRMED,
        )

    def research_plan(self, run_id: str) -> ResearchPlan:
        workflow = self.get(run_id)
        if workflow.state is not WorkflowState.RESEARCH_READY or workflow.research_plan is None:
            raise ResearchNotReadyError("research requires confirmed requirements and criteria")
        return workflow.research_plan

    def events(self, run_id: str) -> tuple[WorkflowEvent, ...]:
        return self._store.events(run_id)

    def _transition(
        self,
        current: DecisionWorkflow,
        updated: DecisionWorkflow,
        *,
        command_id: str,
        payload_hash: str,
        event_type: WorkflowEventType,
    ) -> DecisionWorkflow:
        return self._store.transition(
            current,
            updated,
            command_id=command_id,
            payload_hash=payload_hash,
            event=self._event(
                updated,
                event_type=event_type,
                command_id=command_id,
                from_state=current.state,
            ),
        )

    def _event(
        self,
        workflow: DecisionWorkflow,
        *,
        event_type: WorkflowEventType,
        command_id: str | None,
        from_state: WorkflowState | None,
    ) -> WorkflowEvent:
        return WorkflowEvent(
            sequence=1,
            run_id=workflow.run_id,
            event_type=event_type,
            command_id=command_id,
            from_state=from_state,
            to_state=workflow.state,
            workflow_version=workflow.version,
            occurred_at=workflow.updated_at,
        )

    @staticmethod
    def _require_state(workflow: DecisionWorkflow, expected: WorkflowState) -> None:
        if workflow.state is not expected:
            raise WorkflowTransitionError(
                f"expected {expected.value}, found {workflow.state.value}"
            )


def _research_plan(criteria: SelectionCriteriaContract) -> ResearchPlan:
    dimensions = tuple(
        item.statement
        for items in (
            criteria.hard_constraints,
            criteria.evaluation_criteria,
            criteria.unknowns,
        )
        for item in items
    )
    capabilities = tuple(item.statement for item in criteria.hard_constraints) or dimensions
    evidence = tuple(item.question for item in criteria.research_questions) or tuple(
        f"Review authoritative sources for: {item.statement}" for item in criteria.requirements
    )
    poc_intent = "; ".join(item.check for item in criteria.poc_checks) or (
        "No PoC check is planned; retain unsupported candidates as research-only."
    )
    payload = {
        "criteria_contract_id": criteria.contract_id,
        "investigation_dimensions": dimensions,
        "required_capabilities": capabilities,
        "planned_evidence": evidence,
        "poc_intent": poc_intent,
    }
    return ResearchPlan(
        plan_id="research-plan:" + _hash(payload)[:20],
        **payload,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
