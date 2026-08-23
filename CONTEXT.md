# MOMO TechScout

MOMO TechScout supports auditable, evidence-grounded choices among technology candidates for a defined software project.

## Language

**Decision Context**:
The user-owned facts and criteria that frame one technology-selection decision, including the problem, stack, use cases, deployment, team, quality constraints, and priorities.
_Avoid_: Project context, research context

**User Requirement**:
An atomic need, preference, or unresolved point supplied or confirmed by the user.
_Avoid_: Model assumption, inferred mandate

**Decision Workflow**:
The auditable progression of one Decision Context through User Requirement review and Selection Criteria confirmation until research is allowed to begin.
_Avoid_: Research run, autonomous planning loop

**Requirements Review**:
The stage in which the user inspects the atomic User Requirements and their proposed categories before confirming them.
_Avoid_: Planner approval, inferred requirements

**Criteria Confirmation**:
The stage in which the user inspects Selection Criteria and the linked Research Plan derived from confirmed User Requirements.
_Avoid_: Automatic research start, model confirmation

**Research Ready**:
The state reached only after the user has confirmed both User Requirements and Selection Criteria; only this state permits research execution.
_Avoid_: Queued, planned, implicitly approved

**Must-have**:
A user-confirmed, non-negotiable condition that a viable candidate must satisfy; research may assess it but cannot create it.
_Avoid_: Hard constraint, preference, planner rule

**Preference**:
A user-confirmed priority considered only after must-haves; it cannot make an ineligible candidate safe to recommend.
_Avoid_: Must-have, implicit weight

**Evaluation Criterion**:
A comparison dimension that assesses one or more User Requirements without automatically disqualifying a candidate.
_Avoid_: Must-have, model-assigned weight

**Unknown**:
An explicitly unresolved requirement or claim that remains visible when the user or available Authoritative Sources do not establish it.
_Avoid_: Default, negative fact, unsupported inference

**Research Question**:
An evidence-seeking question proposed to resolve or assess one or more User Requirements.
_Avoid_: User Requirement, conclusion

**PoC Check**:
A bounded verification target linked to one or more User Requirements; its result informs the decision but does not define the requirement.
_Avoid_: Benchmark claim, acceptance criterion

**Experiment Recipe**:
A reviewed, versioned procedure that declares either a Research-only disposition or an ordered set of offline Recipe Checks under one Execution Budget.
_Avoid_: Generated shell script, candidate recommendation

**Recipe Check**:
An atomic, reviewed execution step within an Experiment Recipe that may produce Experiment Artifacts and Measurements for a PoC Check.
_Avoid_: PoC Check, User Requirement, arbitrary command

**Execution Budget**:
The approved limit on an Experiment Execution's Checks, wall time, per-Check time, sandbox resources, Artifact bytes, and Measurements.
_Avoid_: Performance target, observed usage

**Experiment Artifact**:
An immutable, content-hashed record produced by a Recipe Check and retained as audit evidence of what the sandbox observed.
_Avoid_: Measurement, live claim, mutable log

**Measurement**:
A typed value observed during a Recipe Check and linked to the Experiment Artifact that supports it.
_Avoid_: Retrieved Fact, target, inference

**Experiment Execution**:
One idempotently identified application of an Experiment Recipe to a subject, ending in exactly one sealed terminal outcome.
_Avoid_: Open-ended agent run, retry loop

**Research-only**:
A disposition that permits evidence research but forbids Experiment Recipe command execution because no reviewed procedure is available.
_Avoid_: Failed verification, unsupported fact, implicit rejection

**Candidate**:
A technology under consideration within one Decision Context.
_Avoid_: Paper, option record

**Source Candidate**:
A discovered official-documentation or project-maintainer record that has not yet passed identity, quality, version, and Freshness checks.
_Avoid_: Evidence, Retrieved Fact

**Canonical Source URL**:
The stable identity of a source after cosmetic and tracking URL variants are removed.
_Avoid_: Search-result URL

**Authoritative Source**:
An accessed official-documentation or project-maintainer record that matches the Candidate, is fresh enough for the Decision Context, and does not conflict with the requested version.
_Avoid_: Search Summary, search result

**Search Summary**:
Discovery text supplied by a search index; it may lead to a Source Candidate but is not itself an Authoritative Source.
_Avoid_: Retrieved Fact, citation

**Retrieved Fact**:
A claim stated directly by an Authoritative Source.
_Avoid_: Inference, assumption

**Inference**:
An interpretation derived from one or more Retrieved Facts but not stated directly by their sources.
_Avoid_: Retrieved Fact

**Freshness**:
Whether a source was accessed recently enough for the decision cutoff; publication time is separate provenance and does not replace access time.
_Avoid_: Publication date
