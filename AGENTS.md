# Repository agent instructions

`BOUNDARIES.md` is the read-only ownership authority. Its current canonical
SHA-256 is `ec64ceb374a56a4217ca47cedd5ae238bab30926230ef0bf2aafd8000c3512c2`
and Git blob is `fb13c9ae5ca0544978bcde433f50958c446cd8cf`. OPEMOS.EXE still mirrors the
preceding bytes at pinned counterpart commit
`064d1d54c7ef2eda3d56e80c67e9f8e78a554725` while the authorized 2026-09-06
governance synchronization is staged. The preceding pin
`c6733c7c80a104f57b44411d2d4223c2d624818d` and the 2026-09-04 cleanup-ownership
decision remain preserved in `docs/boundary-decision-2026-09-04.md`; the staged
merge-governance decision is recorded in
`docs/boundary-decision-2026-09-06.md`. Do not modify the authority, integrity
test, or counterpart reference during ordinary work. A change requires explicit
user approval plus synchronized source commit, counterpart commit, Git blob, and
SHA-256 references. Do not infer permission from a task that touches both
repositories.

Repository summaries may link to the authority but must not contradict it.
Never edit OPEMOS.EXE from this repository's task unless the user separately
and explicitly authorizes that cross-repository mutation.

## Scheduled work and resource limits

Follow the user-authorized coordination policy at
`/home/connor/Documents/ChatGPT/Handoff troubleshooting/opemos-scheduler/POLICY.md`.
One primary plus at most one useful helper per repository; no nested helpers.
All heavy work must use the shared `opemos-scheduler/heavy.sh` wrapper.
This execution policy does not authorize any change to BOUNDARIES.md or its pins.

Boundary exception, conflict, or ambiguity: stop the affected work and ask the
user before proceeding. Do not resolve ownership questions autonomously or
work around them through another agent. Report scheduler state `approval`.
This applies equally to the primary and its helper.

Resolver may decide only small, reversible boundary-adjacent placement questions
under the shared POLICY.md. Major ownership, deletion authority, trust,
production, destructive, hardware, or governance questions still require the
user. Preserve completed TODO entries in place with commit/test evidence and
preserve Git and handoff history; do not erase or rewrite completed history.

## Lead-only necessary GitHub pushes

The user authorizes this repository's existing primary lead to push its own
commits to GitHub only when the exact remote commit is required for a
cross-repository immutable pin/handoff, remote-only CI or review, or a current
user-requested GitHub deliverable. Helpers and Resolver may not push. Local
commits remain local when remote availability is unnecessary.

Before pushing, verify the configured remote URL, target branch, exact commit
set, relevant tests, and absence of secrets, private inputs, or unrelated
commits. Use a normal fast-forward push of the short-lived work branch only.
Never force-push, rewrite published refs, alter remotes, push tags, publish
releases/assets, or bypass branch protection. The owning primary lead may
squash-merge only under the exact cross-lead approval gate in `BOUNDARIES.md`;
helpers and Resolver cannot approve or merge. Any changed head, base, material
scope, or required-check set invalidates approval. After verifying the protected
main squash commit, the owning lead may delete only that merged topic branch.
Stop on divergence, rejection, or ambiguity and record the remote, branch, pull
request, source commits, checks, and squash commit in TODO or handoff history.
This permission does not authorize production activation, trust publication, or
any other boundary and governance change.
