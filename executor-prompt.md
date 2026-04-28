# CHAPI Executor Prompt

This file tells **you (the human)** what to do when implementing the
CHAPI dedup resolver via Claude Code (Sonnet 4.6). You can run this in
parallel with the HAPI implementation — each lives in its own git
worktree, so they won't step on each other.

---

## One-time setup (run once for the whole project)

From the **main checkout**:

```bash
cd /home/budi/code/sphere_project/patient_duplicate_fields

# init git if not already a repo
if [ ! -d .git ]; then
  git init -b main
  git add -A
  git commit -m "chore: initial scaffolding (exploration + deployment plans)"
fi

# create the chapi worktree on its own branch
git worktree add ../patient_duplicate_fields-chapi -b chapi-impl
```

If you've already run setup for HAPI, the `git init` block is a no-op
(idempotent guard).

---

## Open Claude Code in the worktree

```bash
cd ../patient_duplicate_fields-chapi/chapi_patient_duplicate_address
# launch Claude Code with Sonnet 4.6 (use whatever your launcher is)
claude --model claude-sonnet-4-6
```

---

## Phase prompts

Paste these one at a time. Wait for the agent to finish (it will update
`status.md`) before sending the next.

### Phase 1 — start here

```text
You are implementing the CHAPI patient-address dedup resolver. Read plan.md and status.md in this folder, and read ../.claude/deduplication_rule/dedup-patient-fields-prompt.md for the architectural spec.

Implement Phase 1 only. When done, update status.md to mark Phase 1 complete with a one-or-two-sentence note of what was built. Stop after Phase 1 — do not start Phase 2.
```

### Phase 2

```text
Read status.md to confirm Phase 1 is complete. Implement Phase 2 only. Update status.md when done. Stop after Phase 2.
```

### Phase 3

```text
Read status.md to confirm Phases 1 and 2 are complete. Implement Phase 3 only. Update status.md when done. Stop after Phase 3.
```

### Phase 4

```text
Read status.md to confirm Phases 1–3 are complete. Implement Phase 4 only.

NOTE: Phase 4 performs ~5 real PUT writes against the production CHAPI Purbalingga server. The dedup rule is conservative (only drops, never invents data), so blast radius is bounded.

Update status.md when done. Stop after Phase 4.
```

### Phase 5

```text
Read status.md to confirm Phases 1–4 are complete. Implement Phase 5 only — verification only. DO NOT run deploy.sh. The human will deploy after reviewing your work.

Update status.md when done. Stop after Phase 5.
```

---

## After all phases complete

Inside the worktree, commit everything:

```bash
cd /home/budi/code/sphere_project/patient_duplicate_fields-chapi
git add -A
git commit -m "feat(chapi): implement patient address dedup resolver"
```

Merge back into `main` and clean up the worktree:

```bash
cd /home/budi/code/sphere_project/patient_duplicate_fields
git merge chapi-impl --no-ff -m "merge: chapi resolver implementation"
git worktree remove ../patient_duplicate_fields-chapi
git branch -D chapi-impl
```

Now deploy when you're ready:

```bash
cd chapi_patient_duplicate_address
./deploy.sh purbalingga
# (later, once Lombok Barat URL/key are known)
# ./deploy.sh lombok-barat
```

---

## If you need to resume mid-implementation

Just open Claude Code in the worktree again and paste the next phase
prompt. The agent reads `status.md` to know where it left off; you don't
need to re-paste earlier prompts.

If you're not sure which phase is next, paste:

```text
Read status.md and tell me the next unchecked phase. Do not implement anything yet.
```
