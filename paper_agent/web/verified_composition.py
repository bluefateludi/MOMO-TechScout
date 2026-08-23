"""Lazy composition root for the bounded verified TechScout Web path."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

from paper_agent.config import Settings
from paper_agent.generation import DashScopeChatTransport, DashScopeGenerationProvider
from paper_agent.evidence.hybrid import HybridEvidenceRetriever
from paper_agent.evidence.retriever import LexicalCandidateSource
from paper_agent.techscout.context import ContextEngine, HybridContextRetriever
from paper_agent.techscout.research import LiveEvidenceResearchService
from paper_agent.techscout.sandbox import DockerCliRunner, InstallNetworkPolicy, SandboxLimits
from paper_agent.techscout.sandbox.service import RealPocService
from paper_agent.techscout.tools.adapters import (
    AdapterError,
    CachedFetchAdapter,
    CachedGitHubAdapter,
    CachedSearchAdapter,
    GitHubReadOnlyAdapter,
    HttpxFetchAdapter,
    TavilySearchAdapter,
)
from paper_agent.techscout.tools.cache import ContentAddressedCache
from paper_agent.web.techscout_execution import StageServicesFactory, VerifiedStageServices


class _UnavailableSearch:
    def search(self, request):
        raise AdapterError("live search provider is unavailable")


def make_verified_services_factory(
    *,
    output_root: Path,
    state_root: Path,
    settings_loader: Callable[[], Settings],
    generation_max_tokens: int = 1_024,
) -> StageServicesFactory:
    """Build live dependencies only when a verified run is actually claimed."""

    workspace_root = output_root / "techscout"
    cache_root = state_root / "verified-cache"

    def factory(**kwargs):
        workspace_root.mkdir(parents=True, exist_ok=True)
        settings = settings_loader()
        client = httpx.Client()
        generation_provider = (
            DashScopeGenerationProvider(
                api_key=settings.dashscope_api_key,
                model=settings.dashscope_generation_model,
                base_url=settings.dashscope_generation_base_url,
                transport=DashScopeChatTransport(client),
                max_tokens=generation_max_tokens,
            )
            if settings.dashscope_api_key
            else None
        )
        cache = ContentAddressedCache(cache_root)
        live_search = (
            TavilySearchAdapter(
                client=client, api_key=settings.tavily_api_key, timeout_seconds=4
            )
            if settings.tavily_api_key
            else _UnavailableSearch()
        )
        search = CachedSearchAdapter(delegate=live_search, cache=cache)
        fetch = CachedFetchAdapter(
            delegate=HttpxFetchAdapter(client=client, timeout_seconds=4), cache=cache
        )
        github = CachedGitHubAdapter(
            delegate=GitHubReadOnlyAdapter(
                client=client, token=settings.github_token, timeout_seconds=4
            ),
            cache=cache,
        )
        retrieval = HybridEvidenceRetriever(
            lexical_source=LexicalCandidateSource(),
            vector_source=None,
            requested_mode="lexical",
            candidate_k=8,
            top_k=8,
            rrf_k=60,
        )
        context_engine = ContextEngine(HybridContextRetriever(retrieval))
        research = LiveEvidenceResearchService(
            search=search,
            fetch=fetch,
            github=github,
            context_engine=context_engine,
            max_sources=1,
        )
        install_network = None
        if settings.techscout_docker_install_network:
            install_network = InstallNetworkPolicy(
                docker_network=settings.techscout_docker_install_network,
                allowed_destinations=("pypi.org", "files.pythonhosted.org"),
                egress_allowlist_enforced=settings.techscout_docker_egress_allowlist_enforced,
            )
        poc = RealPocService(
            DockerCliRunner(
                workspace_root,
                limits=SandboxLimits(timeout_seconds=40),
                install_network=install_network,
            ),
            secrets=tuple(
                value for value in (settings.tavily_api_key, settings.github_token) if value
            ),
        )
        return VerifiedStageServices(
            research_service=research,
            context_engine=context_engine,
            poc_service=poc,
            generation_provider=generation_provider,
            generation_timeout_seconds=settings.dashscope_generation_timeout_seconds,
            **kwargs,
        )

    return factory
