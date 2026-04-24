---
name: reviewcode
description: Perform a thorough, actionable code review with a consistent checklist and clear outputs
---

# reviewcode

Use this skill to review a change set (PR/commit/diff) and produce:

- A prioritized, actionable review (must-fix / should-fix / nit)
- Concrete patch suggestions where safe
- A verification plan (how to build/test/run)

This skill is repository-agnostic, but it must respect local conventions (linters, formatting, architecture, and rules in `.clinerules/`).

## Inputs required

Ask for any missing information before reviewing:

- **Scope**: PR link, commit range, or files to review
- **Intent**: what the change is supposed to do (user story / bug)
- **Risk**: release impact, backwards-compat constraints, performance constraints
- **How to verify**: expected commands or CI steps (if unknown, infer from repo)

## Usage

Use `reviewcode` when the user asks you to:

- Review a PR, commit, or local diff
- Validate a refactor for correctness and maintainability
- Audit changes for security, performance, and reliability issues
- Ensure changes align with architecture and domain boundaries

Do **not** use it when:

- The user needs a feature implemented (use implementation workflows)
- There is no code/diff to review yet (ask for scope first)

## Output format (always)

1. **Summary** (2–6 bullets): what changed and why (as understood)
2. **Key risks**: edge cases, migrations, compatibility, data loss risks
3. **Findings** grouped by severity:
   - **Must fix** (correctness/security/data loss/build break)
   - **Should fix** (maintainability/perf/a11y/robustness)
   - **Nice to have** (style/nits)
4. **Suggested patches** (minimal diffs/snippets) when changes are low-risk
5. **Verification plan**: exact commands to run and what to check
6. **Follow-up questions** (only if blocking)

## Steps

### 1) Establish context and constraints

- Identify language/framework for each changed area (e.g., Spring Boot, Angular/React, C++ JNI, Ada, SQL migrations).
- Confirm architecture boundaries (e.g., controllers thin, services own business logic, repositories data access).
- Confirm non-functional constraints: performance, security, compatibility, deployment.
- Identify if the change touches:
  - persistence schema/data migrations
  - public API contracts
  - concurrency / threading
  - native/JNI boundaries
  - security/auth, file upload, deserialization

### 2) Map the change surface

- List changed files and categorize them:
  - **API surface** (controllers, DTOs, route handlers)
  - **Domain/model**
  - **Business logic/services**
  - **Persistence/migrations**
  - **Infrastructure** (clients, adapters, JNI, HTTP)
  - **Tests**
  - **Docs/ops** (README, compose, configs)
- Identify blast radius: what callers/users are affected?

### 3) Correctness review (highest priority)

Check:

- Does the code meet the stated intent? Any missing behavior?
- Edge cases: empty inputs, null/undefined handling, bounds, time zones, encoding.
- Error handling: are exceptions translated to correct HTTP status / error payload?
- Resource handling: files/streams/sockets closed, timeouts set, retries bounded.
- Concurrency: thread safety, shared state, race conditions.
- Determinism: avoid flaky time-based logic; inject clocks/PRNG where appropriate.

### 4) API/contract review

- Breaking changes: removed/renamed fields, status code changes, behavior changes.
- Validation: server-side validation of request bodies/params; clear error messages.
- DTO mapping correctness: no leaking of entities if the architecture forbids it.
- Backward compatibility: defaults for new fields; versioning strategy if needed.

### 5) Security review

Assess:

- Input validation & sanitization (especially file upload, CSV parsing, HTML content).
- Injection risks: SQL injection, command injection, path traversal.
- Authentication/authorization checks present where required.
- Secrets: no credentials in code; safe env var usage.
- Deserialization: avoid unsafe deserialization; validate JSON.
- Native boundary: validate all inputs crossing JNI; ensure bounds checks.

### 6) Performance and reliability review

- Hot paths: O(n²) loops, repeated DB calls (N+1), large allocations.
- Timeouts and circuit breakers for network calls.
- Logging: no excessive logs in tight loops; sensitive data not logged.
- Caching only if correct and invalidation is understood.

### 7) Maintainability and design review

- Naming, clarity, SRP, duplication.
- Consistency with existing patterns (project conventions, `.clinerules/`).
- Separation of concerns and layering.
- Testability: dependencies injectable; avoid hidden global state.

### 8) Testing review

- Are new behaviors covered by tests (unit/integration/e2e as applicable)?
- Are tests meaningful (assert behavior, not implementation details)?
- Do tests cover edge cases and failures?
- Are mocks used appropriately?
- For migrations: is there a safe rollback strategy / test data setup?

### 9) Observability and operations review

- Logs are structured/consistent; errors include context.
- Metrics/tracing (if present in the repo) updated accordingly.
- Config changes documented; sensible defaults; environment-specific behavior clear.
- Docker/compose impacts considered (ports, env vars, volumes).

### 10) Documentation and developer experience

- README / API docs updated when contracts or workflows change.
- Clear migration notes when schema changes.
- Lint/format passes; no generated/build artifacts committed.

### 11) Verify locally (or propose exact verification)

Run or propose the minimal commands to validate:

- Backend: `mvn -q test` / `mvn -q package` (or repo standard)
- Frontend: `npm test` / `npm run build` (or repo standard)
- Native: `cmake --build ...` (if applicable)
- End-to-end: smoke test critical flows

Document expected outcomes and where to look for failures.

## Severity guidelines

- **Must fix**: build break, failing tests, incorrect results, security issues, data loss, API contract break without plan.
- **Should fix**: likely future bug, poor error handling, missing tests for critical logic, performance hazards.
- **Nice to have**: minor readability/style improvements, small refactors.

## Review comment templates

### Finding

- **Severity**: Must fix / Should fix / Nice to have
- **Location**: `path/to/file.ext: symbol or snippet`
- **Issue**: concise description
- **Why it matters**: correctness/security/perf/maintainability impact
- **Recommendation**: what to change
- **Suggested patch**: (optional) minimal snippet/diff

### Follow-up question

- **Question**: …
- **Context**: why you need the answer to review safely
