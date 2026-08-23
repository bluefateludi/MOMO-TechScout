# MOMO TechScout

MOMO TechScout supports auditable, evidence-grounded choices among technology candidates for a defined software project.

## Language

**Decision Context**:
The user-owned facts and criteria that frame one technology-selection decision, including the problem, stack, use cases, deployment, team, quality constraints, and priorities.
_Avoid_: Project context, research context

**User Requirement**:
An atomic need, preference, or unresolved point supplied or confirmed by the user.
_Avoid_: Model assumption, inferred mandate

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
An explicitly unresolved fact or preference that remains visible until the user, evidence, or a bounded check resolves it.
_Avoid_: Default, implicit decision

**Research Question**:
An evidence-seeking question proposed to resolve or assess one or more User Requirements.
_Avoid_: User Requirement, conclusion

**PoC Check**:
A bounded verification target linked to one or more User Requirements; its result informs the decision but does not define the requirement.
_Avoid_: Benchmark claim, acceptance criterion

**Candidate**:
A technology under consideration within one Decision Context.
_Avoid_: Paper, option record
