import asyncio
from datetime import datetime, timezone

from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.models import CacheStatus, ToolCall, ToolStatus
from paper_agent.techscout.runtime_skills import fixed_skill_registry
from paper_agent.techscout.tools import (
    FakeToolRuntime,
    PolicyToolRuntime,
    SearchOutput,
    SourceProvenance,
)
from paper_agent.techscout.tools.contracts import SearchHit


def _search_output() -> SearchOutput:
    return SearchOutput(
        query="Qdrant filtering",
        candidate_id="candidate:qdrant",
        results=(
            SearchHit(
                title="Filtering",
                url="https://qdrant.tech/documentation/filtering/",
                snippet="Payload filtering is supported.",
                score=0.9,
            ),
        ),
        provenance=SourceProvenance(
            provider="fake",
            retrieved_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            snapshot_sha256="a" * 64,
            cache_status=CacheStatus.MISS,
        ),
    )


def _call(tool_name: str = "web.search") -> ToolCall:
    return ToolCall(
        tool_call_id="tool-call:test:1",
        tool_name=tool_name,
        skill_id="skill:official-doc-research@1",
        arguments={
            "query": "Qdrant filtering",
            "candidate_id": "candidate:qdrant",
            "domains": ["qdrant.tech"],
            "max_results": 5,
        },
    )


def test_typed_fake_and_policy_allow_valid_intersection() -> None:
    fake = FakeToolRuntime({"web.search": [_search_output()]})
    runtime = PolicyToolRuntime(
        delegate=fake,
        skills=fixed_skill_registry(),
        local_allowlist={"web.search"},
    )

    result = asyncio.run(runtime.invoke(_call()))

    assert result.status is ToolStatus.SUCCEEDED
    assert result.cache_status is CacheStatus.MISS
    assert len(fake.calls) == 1


def test_policy_requires_both_skill_and_local_allowlists() -> None:
    fake = FakeToolRuntime({"web.search": [_search_output()]})
    runtime = PolicyToolRuntime(
        delegate=fake,
        skills=fixed_skill_registry(),
        local_allowlist={"github.inspect_repository"},
    )

    result = asyncio.run(runtime.invoke(_call()))

    assert result.status is ToolStatus.DENIED
    assert result.error_code is FailureCode.UNSAFE_REQUEST
    assert fake.calls == []


def test_policy_rejects_arbitrary_shell_tool_without_delegate_invocation() -> None:
    fake = FakeToolRuntime({"web.search": [_search_output()]})
    runtime = PolicyToolRuntime(
        delegate=fake,
        skills=fixed_skill_registry(),
        local_allowlist={"web.search"},
    )
    call = ToolCall(
        tool_call_id="tool-call:adversarial:shell",
        tool_name="shell.exec",
        skill_id="skill:official-doc-research@1",
        arguments={"command": "whoami"},
    )

    result = asyncio.run(runtime.invoke(call))

    assert result.status is ToolStatus.DENIED
    assert result.error_code is FailureCode.UNSAFE_REQUEST
    assert result.output == {}
    assert fake.calls == []


def test_policy_discovery_is_scoped_to_current_skill_and_local_policy() -> None:
    fake = FakeToolRuntime(
        {
            "web.search": [_search_output()],
            "github.inspect_repository": [],
        }
    )
    runtime = PolicyToolRuntime(
        delegate=fake,
        skills=fixed_skill_registry(),
        local_allowlist={"web.search", "github.inspect_repository"},
    )

    discovered = asyncio.run(
        runtime.discover_tools("skill:official-doc-research@1")
    )

    assert discovered == ("web.search",)
    assert asyncio.run(runtime.discover_tools()) == ()
    assert asyncio.run(runtime.discover_tools("skill:unknown@1")) == ()


def test_fake_fails_closed_on_malformed_output() -> None:
    fake = FakeToolRuntime({"web.search": [{"unexpected": True}]})

    result = asyncio.run(fake.invoke(_call()))

    assert result.status is ToolStatus.FAILED
    assert result.error_code.value == "malformed_mcp_response"
