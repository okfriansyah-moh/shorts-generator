---
name: running-prompt
description: Structured workflow for executing tasks including planning, implementation, security review, verification, and issue remediation. Connects to the @reviewer custom agent for automated code review. Optimized for Principal Engineer execution standards.
---

# Task Execution Workflow

Follow the steps below to handle tasks effectively.

---

## 0. Temperature Configuration

Set temperature dynamically based on task type.

| Task Category                      | Temperature | Rationale                                       |
| ---------------------------------- | ----------- | ----------------------------------------------- |
| Implementation / Execution         | **0.15**    | Deterministic, precise, production-safe output  |
| Research / Planning / Architecture | **0.45**    | Controlled exploration with trade-off reasoning |
| Security Review / Audit            | **0.2**     | Deterministic threat modeling                   |
| Verification / Analysis            | **0.2**     | Accurate validation without creative deviation  |
| Remediation / Fixing               | **0.15**    | Precise issue resolution                        |

**Rules:**

- Never exceed **0.5** for production work.
- Use **≤ 0.2** for security-sensitive, financial, authentication, and
  data pipeline systems.

---

## 1. Planning via Subagent

Use the **subagent** to thoroughly plan the tasks.

Return the implementation plan including:

- Technical details and architecture decisions
- Confirmed critical approaches
- Identified risks and mitigation strategies
- Performance considerations
- Security implications

**Mandatory:** Use `askQuestion` to clarify all uncertainties before
proceeding. No assumptions allowed on decisions affecting:

- Functional behavior
- Resiliency and reliability
- Security and compliance
- Performance and scalability
- Cost efficiency

---

## 2. Immediate Implementation

Implement according to the approved plan on the **main agent**.

Follow:

- `AGENTS.md` project standards
- Security best practices
- Reliability engineering principles
- Performance optimization guidelines

Implementation must strictly align with approved planning outputs.

---

## 3. Parallel Post-Implementation Review

After implementation, invoke **`@reviewer`** and the **Verification
subagent** in parallel.

---

### 3a. @reviewer — Code Review

Invoke the `@reviewer` custom agent (`.github/agents/reviewer.agent.md`)
with the implemented files as the argument.

The `@reviewer` agent will produce a structured report covering:

- Possible bugs and logic errors
- Security vulnerabilities with CVSS scores
- Performance bottlenecks
- Code quality and convention gaps
- **Review Verdict**: ✅ Approved / ⚠️ Approved with Notes / 🚫 Blocked / 🔄 Request Changes

---

### 3b. Verification Subagent

Perform technical verification:

- Build validation: `go build ./...`
- Static analysis: `go vet ./...`, `golangci-lint run`
- Automated tests: `go test ./... -cover`
- Type checking and linting
- Coverage gap validation

**Report must include:** build failures, test failures, code quality
issues, type violations, and coverage gaps.

---

## 4. Issue Remediation Loop

If `@reviewer` verdict is 🚫 **Blocked** or 🔄 **Request Changes**, or
if the Verification subagent reports any failures:

1. Fix all issues immediately
2. Re-implement corrections
3. Re-invoke `@reviewer` + Verification subagent (Step 3) in parallel

Repeat until:

- `@reviewer` verdict = ✅ **Approved** or ⚠️ **Approved with Notes**
- All verification checks pass with zero failures

**Zero critical/high issue state is mandatory before proceeding to Step 5.**

---

## 5. Pre-Completion Approval Gate

Before generating the final summary, use `askQuestion` to obtain explicit
user approval.

Approval request must include:

- Implementation summary
- Key technical decisions made
- `@reviewer` final verdict and report reference
- Verification status (build ✅, lint ✅, tests ✅, coverage ✅)
- Remaining risks or trade-offs (if any)

⚠️ **Final summary is forbidden before approval is granted.**

---

## 6. Completion Confirmation

Once approval is received and zero critical/high issue state is confirmed,
declare that the implementation is:

- ✅ **Complete** — All planned features implemented
- ✅ **Secure** — Passed `@reviewer` with no Critical/High findings
- ✅ **Verified** — Passed all build, lint, test, and coverage checks

And has passed:

- `@reviewer` code review gate
- Verification subagent gate
- Explicit user approval gate
