# MOMO TechScout

MOMO TechScout supports evidence-grounded choices among technology candidates for a defined software project.

## Language

**Decision Context**:
The user-owned facts and criteria that frame one technology-selection decision, including the problem, stack, use cases, deployment, team, quality constraints, and priorities.
_Avoid_: Project context, research context

**Must-have**:
A non-negotiable condition that an eligible recommendation must satisfy or explicitly mark as unknown when no safe winner exists.
_Avoid_: Hard constraint, preference

**Preference**:
A ranking factor considered only after must-haves; it cannot make an ineligible candidate safe to recommend.
_Avoid_: Nice-to-have constraint, soft requirement

**Candidate**:
A technology under consideration within one Decision Context.
_Avoid_: Paper, option record
