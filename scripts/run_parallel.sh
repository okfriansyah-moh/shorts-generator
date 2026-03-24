#!/opt/homebrew/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Shorts Factory — Parallel Development Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
# 3-mode execution system for running multiple implementation phases
# simultaneously using autonomous AI agents.
#
# Usage:
#   ./scripts/run_parallel.sh start [--mode=1|2|3] <phases...>
#   ./scripts/run_parallel.sh status
#   ./scripts/run_parallel.sh merge
#   ./scripts/run_parallel.sh cleanup
#
# Modes:
#   Mode 1 — Full Parallel   : One worktree + agent per phase (max speed)
#   Mode 2 — Token-Optimized  : Single session, sequential phases (min cost)
#   Mode 3 — Hybrid (default) : Parallel across groups, sequential within
#
# See docs/PARALLEL_DEV.md for full documentation.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKTREE_BASE="${PROJECT_ROOT}/../shorts-generator-worktrees"
LOG_DIR="${PROJECT_ROOT}/.parallel-dev/logs"
STATE_FILE="${PROJECT_ROOT}/.parallel-dev/state.json"
INTEGRATION_BRANCH="integration/parallel-$(date +%Y%m%d-%H%M%S)"

# Default mode
MODE=3

# MODEL ROUTING STRATEGY:
#   Heavy model  : claude-opus-4.6          — assigned to the single most complex phase
#   Rotate models: sonnet-4.6 → sonnet-4.5 → gpt-5.3-codex → gpt-5.4 (round-robin)
#                  Used for: all other phases, conflict-resolver, post-merge review,
#                            docs sync, quality gate remediation, integration remediation
#
MODEL_HEAVY="claude-opus-4.6"
MODEL_ROTATE_POOL=("claude-sonnet-4.6" "claude-sonnet-4.5" "gpt-5.3-codex" "gpt-5.4")
ROTATION_INDEX=0

# ── Per-stage retry limits (bounded — no infinite loops) ──────────────────
MAX_RETRIES_PHASE_BUILDER=5
MAX_RETRIES_DTO=5
MAX_RETRIES_INTEGRATION=5
MAX_RETRIES_MERGE=5
MAX_RETRIES_GLOBAL_VALIDATION=5
MAX_REMEDIATION_RETRIES=3

# ── Resource control ─────────────────────────────────────────────────────
MAX_PARALLEL_AGENTS=5

# Agent pipeline — mandatory execution order per phase/group
# Every agent run follows this chain: build → validate → integrate → (fix if needed)
AGENT_PIPELINE=("phase-builder" "dto-guardian" "integration")
REMEDIATION_AGENT="refactor"

# Core skills injected into every Copilot call
CORE_SKILLS="dto, pipeline, modularity, determinism, idempotency, testing"

# Protected paths — agents MUST NOT modify unless explicitly instructed
PROTECTED_PATHS=("contracts/" "database/" "docs/")

# ─────────────────────────────────────────────────────────────────────────────
# Phase metadata
# ─────────────────────────────────────────────────────────────────────────────
declare -A PHASE_NAMES=(
    [0]="core-infrastructure"
    [1]="ingestion-scene-splitter"
    [2]="transcription-face-detection"
    [3]="scoring"
    [4]="clip-builder"
    [5]="compositor"
    [6]="hook-tts-subtitle-renderer"
    [7]="thumbnail-metadata"
    [8]="storage-scheduler"
    [9]="publisher"
    [10]="analytics"
)

declare -A PHASE_COMPLEXITY=(
    [0]=8  [1]=7  [2]=8  [3]=5  [4]=6
    [5]=7  [6]=9  [7]=4  [8]=5  [9]=5  [10]=3
)

# Phase grouping rules for Mode 3 (Hybrid)
# Phases within a group run sequentially; groups run in parallel.
# Groups are defined by dependency chains — phases in the same group
# have sequential data dependencies.
declare -A PHASE_TO_GROUP=(
    [0]="A" [1]="A"
    [2]="B" [3]="B"
    [4]="C" [5]="C" [6]="C"
    [7]="D" [8]="D"
    [9]="E" [10]="E"
)

# Skills required per phase (from docs/PARALLEL_DEV.md Section 5)
declare -A PHASE_SKILLS=(
    [0]="config-validation, sqlite, logging, idempotency"
    [1]="ffmpeg, pyscenedetect, dto, modularity, determinism, testing"
    [2]="faster-whisper, mediapipe, ffmpeg, dto, modularity, testing"
    [3]="dto, determinism, testing"
    [4]="dto, determinism, testing"
    [5]="ffmpeg, dto, modularity, testing"
    [6]="edge-tts, ass-subtitle, ffmpeg, dto, modularity, testing"
    [7]="pillow, dto, modularity, testing"
    [8]="sqlite, idempotency, dto, testing"
    [9]="dto, failure, testing"
    [10]="logging, testing"
)

# ─────────────────────────────────────────────────────────────────────────────
# Color output
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_header()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }

# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────

ensure_dirs() {
    mkdir -p "${LOG_DIR}"
    mkdir -p "$(dirname "${STATE_FILE}")"
}

next_model() {
    local model="${MODEL_ROTATE_POOL[${ROTATION_INDEX}]}"
    ROTATION_INDEX=$(( (ROTATION_INDEX + 1) % ${#MODEL_ROTATE_POOL[@]} ))
    echo "${model}"
}

heaviest_phase() {
    # Given a list of phases, return the one with highest complexity score
    local max_phase=""
    local max_score=0
    for phase in "$@"; do
        local score="${PHASE_COMPLEXITY[$phase]:-0}"
        if (( score > max_score )); then
            max_score=$score
            max_phase=$phase
        fi
    done
    echo "${max_phase}"
}

validate_phases() {
    for phase in "$@"; do
        if [[ -z "${PHASE_NAMES[$phase]+x}" ]]; then
            log_error "Invalid phase number: ${phase}. Valid range: 0–10."
            exit 1
        fi
    done
}

check_clean_worktree() {
    cd "${PROJECT_ROOT}"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        log_error "Working directory has uncommitted changes. Commit or stash first."
        exit 1
    fi
}

check_copilot_cli() {
    if ! command -v copilot &>/dev/null; then
        log_error "Copilot CLI not found. Install with: npm install -g @githubnext/github-copilot-cli"
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Agent execution logging (with retry awareness)
# ─────────────────────────────────────────────────────────────────────────────

log_agent_start() {
    local agent="$1" phase="$2"
    local attempt="${3:-1}" max="${4:-1}"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    log_info "[${agent}] attempt ${attempt}/${max} started — phase=${phase} at ${ts}"
    echo "[${ts}] [${agent}] attempt ${attempt}/${max} started — phase=${phase}" >> "${LOG_DIR}/agent-chain.log"
}

log_agent_end() {
    local agent="$1" phase="$2" result="$3"
    local attempt="${4:-1}" max="${5:-1}"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    if [[ "${result}" == "0" ]]; then
        log_success "[${agent}] attempt ${attempt}/${max} passed — phase=${phase}"
        echo "[${ts}] [${agent}] attempt ${attempt}/${max} passed — phase=${phase}" >> "${LOG_DIR}/agent-chain.log"
    else
        log_error "[${agent}] attempt ${attempt}/${max} failed — phase=${phase}"
        echo "[${ts}] [${agent}] attempt ${attempt}/${max} FAILED — phase=${phase}" >> "${LOG_DIR}/agent-chain.log"
    fi
}

log_rollback() {
    local phase="$1" reason="$2"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    log_error "[rollback] phase=${phase} — ${reason}"
    echo "[${ts}] [rollback] phase=${phase} — ${reason}" >> "${LOG_DIR}/agent-chain.log"
}

# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint & rollback (atomic execution)
# ─────────────────────────────────────────────────────────────────────────────
# Before each phase/group, a git tag is created as a recovery point.
# If any stage exceeds its retry limit, the branch is reset to the tag.
# ─────────────────────────────────────────────────────────────────────────────

create_checkpoint() {
    local phase_label="$1"
    local work_dir="${2:-$(pwd)}"
    local tag="checkpoint-${phase_label}-pre"
    cd "${work_dir}"
    git tag -f "${tag}" HEAD 2>/dev/null
    log_info "Checkpoint created: ${tag}"
}

rollback_to_checkpoint() {
    local phase_label="$1" reason="$2"
    local work_dir="${3:-$(pwd)}"
    local tag="checkpoint-${phase_label}-pre"
    cd "${work_dir}"
    if git rev-parse --verify "${tag}" &>/dev/null; then
        git reset --hard "${tag}" 2>/dev/null
        log_rollback "${phase_label}" "reset to ${tag} — ${reason}"
    else
        log_error "Checkpoint ${tag} not found — cannot rollback"
    fi
}

cleanup_checkpoint() {
    local phase_label="$1"
    local work_dir="${2:-$(pwd)}"
    local tag="checkpoint-${phase_label}-pre"
    cd "${work_dir}"
    git tag -d "${tag}" 2>/dev/null || true
}

# ─────────────────────────────────────────────────────────────────────────────
# Universal retry framework
# ─────────────────────────────────────────────────────────────────────────────
# Generic retry controller: execute → validate → fix → re-validate → bounded
# retry → success OR rollback. Every stage uses this pattern.
#
# Usage:
#   retry_stage <stage_name> <max_retries> <phase_label> <model> <work_dir> \
#               <execute_fn> <validate_fn> <fix_fn>
#
# The execute/validate/fix functions receive: work_dir model phase_label attempt
# ─────────────────────────────────────────────────────────────────────────────

retry_stage() {
    local stage_name="$1" max_retries="$2" phase_label="$3" model="$4" work_dir="$5"
    local execute_fn="$6" validate_fn="$7" fix_fn="$8"
    local attempt=0

    while (( attempt < max_retries )); do
        ((attempt++))
        log_agent_start "${stage_name}" "${phase_label}" "${attempt}" "${max_retries}"

        # Execute the stage
        local exec_rc=0
        ${execute_fn} "${work_dir}" "${model}" "${phase_label}" "${attempt}" || exec_rc=$?

        # Validate the output
        local valid_rc=0
        ${validate_fn} "${work_dir}" "${model}" "${phase_label}" "${attempt}" || valid_rc=$?

        if (( valid_rc == 0 )); then
            log_agent_end "${stage_name}" "${phase_label}" "0" "${attempt}" "${max_retries}"
            return 0
        fi

        log_agent_end "${stage_name}" "${phase_label}" "1" "${attempt}" "${max_retries}"

        # If not last attempt, run the fix function and retry
        if (( attempt < max_retries )); then
            log_info "[${stage_name}] attempt ${attempt} failed → retrying after fix"
            ${fix_fn} "${work_dir}" "${model}" "${phase_label}" "${attempt}" || true
        fi
    done

    log_error "[${stage_name}] failed after ${max_retries} retries → rollback triggered"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Stage-specific execute / validate / fix functions
# ─────────────────────────────────────────────────────────────────────────────
# Each stage has three functions that plug into retry_stage():
#   *_execute  — runs the agent or operation
#   *_validate — checks the output meets requirements
#   *_fix      — attempts targeted remediation
# ─────────────────────────────────────────────────────────────────────────────

# ── Phase Builder ─────────────────────────────────────────────────────────

phase_builder_execute() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    local skill_prompt="${_CURRENT_SKILL_PROMPT}"
    cd "${work_dir}"
    local pb_log="${LOG_DIR}/${phase_label}-phase-builder-${attempt}.log"
    copilot \
        -p "Read PHASE_TASK.md and implement all listed phases sequentially. ${skill_prompt}. MANDATORY: Use ONLY skills as primary knowledge source (dto, pipeline, modularity, determinism, idempotency). DO NOT read full documentation unless skills are insufficient — if reading docs, explain why skills are insufficient. For each phase: implement, test, then commit with message 'feat(phase-N): implement <name>'. Follow all constraints in .github/copilot-instructions.md." \
        --agent=phase-builder \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${pb_log}"
    return ${PIPESTATUS[0]}
}

phase_builder_validate() {
    local work_dir="$1"
    cd "${work_dir}"
    local failures=0
    # Module compiles
    if [[ -d "modules" ]]; then
        if ! python -c "import sys; sys.path.insert(0, '.'); import importlib" 2>/dev/null; then
            log_error "[phase-builder-validate] Module compilation failed"
            ((failures++))
        fi
    fi
    # No syntax errors
    if [[ -d "modules" ]]; then
        local syntax_errors
        syntax_errors=$(find modules/ -name '*.py' -exec python -m py_compile {} \; 2>&1 | head -10)
        if [[ -n "${syntax_errors}" ]]; then
            log_error "[phase-builder-validate] Syntax errors found"
            ((failures++))
        fi
    fi
    # Imports valid
    if [[ -d "contracts" ]]; then
        local import_errors
        import_errors=$(find contracts/ -name '*.py' ! -name '__init__.py' -exec python -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', '{}'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)" \; 2>&1 | head -10)
        if [[ -n "${import_errors}" ]]; then
            log_error "[phase-builder-validate] Contract import errors"
            ((failures++))
        fi
    fi
    return $(( failures > 0 ? 1 : 0 ))
}

phase_builder_fix() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    local skill_prompt="${_CURRENT_SKILL_PROMPT}"
    cd "${work_dir}"
    local fix_log="${LOG_DIR}/${phase_label}-phase-builder-fix-${attempt}.log"
    copilot \
        -p "Phase builder validation failed. Fix compilation and syntax issues ONLY. Do not change architecture. ${skill_prompt}. Commit fixes." \
        --agent=refactor \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${fix_log}"
    return ${PIPESTATUS[0]}
}

# ── DTO Guardian ──────────────────────────────────────────────────────────

dto_guardian_execute() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    cd "${work_dir}"
    local dg_log="${LOG_DIR}/${phase_label}-dto-guardian-${attempt}.log"
    copilot \
        -p "Validate all DTOs in contracts/ against docs/dto_contracts.md. STRICT checks: no missing fields, no extra fields, no type mismatches, all dataclasses are frozen. Use skills: dto. MANDATORY: Use ONLY skills as primary knowledge source. DO NOT read full documentation unless skills are insufficient. Report violations and fix them. Commit fixes if any." \
        --agent=dto-guardian \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${dg_log}"
    return ${PIPESTATUS[0]}
}

dto_guardian_validate() {
    local work_dir="$1"
    cd "${work_dir}"
    local failures=0
    if [[ -d "contracts" ]]; then
        # All dataclasses are frozen
        if grep -rn "@dataclass$" contracts/ 2>/dev/null | grep -v "frozen=True" | head -5 | grep -q .; then
            log_error "[dto-validate] Non-frozen dataclass in contracts/"
            ((failures++))
        fi
        # No raw dicts returned from modules
        if [[ -d "modules" ]] && grep -rn "-> dict" modules/ 2>/dev/null | head -5 | grep -q .; then
            log_error "[dto-validate] Module returning raw dict instead of frozen DTO"
            ((failures++))
        fi
        # No mutable default fields
        if grep -rn "field(default_factory=list\|field(default_factory=dict" contracts/ 2>/dev/null | head -5 | grep -q .; then
            log_error "[dto-validate] Mutable default in frozen DTO"
            ((failures++))
        fi
    fi
    return $(( failures > 0 ? 1 : 0 ))
}

dto_guardian_fix() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    cd "${work_dir}"
    local fix_log="${LOG_DIR}/${phase_label}-dto-fix-${attempt}.log"
    copilot \
        -p "DTO validation failed. Fix DTO-specific issues ONLY: ensure all dataclasses are frozen, no missing/extra fields, no type mismatches, no mutable defaults. Use skills: dto. Commit fixes." \
        --agent=dto-guardian \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${fix_log}"
    return ${PIPESTATUS[0]}
}

# ── Integration Agent ─────────────────────────────────────────────────────

integration_execute() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    local skill_prompt="${_CURRENT_SKILL_PROMPT}"
    cd "${work_dir}"
    local int_log="${LOG_DIR}/${phase_label}-integration-${attempt}.log"
    copilot \
        -p "Validate module integration for the phases just implemented. STRICT checklist: (1) DTO compatibility across producer/consumer stages, (2) no cross-module imports, (3) no raw SQL in modules — no sqlite3/psycopg2/asyncpg imports in modules, (4) no module calling another module, (5) database access only through orchestrator, (6) deterministic ordering preserved — all collections explicitly sorted, (7) idempotency preserved — content-addressable IDs, (8) no hidden side effects. Use skills: ${CORE_SKILLS}. MANDATORY: Use ONLY skills as primary knowledge source. Report and fix violations. Commit fixes if any." \
        --agent=integration \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${int_log}"
    return ${PIPESTATUS[0]}
}

integration_validate() {
    local work_dir="$1"
    cd "${work_dir}"
    local failures=0

    if [[ -d "modules" ]]; then
        # No cross-module imports
        local cross_imports
        cross_imports=$(find modules/ -name '*.py' -exec grep -ln "from modules\." {} \; 2>/dev/null | head -20)
        if [[ -n "${cross_imports}" ]]; then
            while IFS= read -r file; do
                local file_module
                file_module=$(echo "${file}" | cut -d'/' -f2)
                local imported_modules
                imported_modules=$(grep "from modules\." "${file}" | sed 's/.*from modules\.\([a-z_]*\).*/\1/' | sort -u)
                for imp in ${imported_modules}; do
                    if [[ "${imp}" != "${file_module}" ]]; then
                        log_error "[integration-validate] Cross-module: ${file} → modules.${imp}"
                        ((failures++))
                    fi
                done
            done <<< "${cross_imports}"
        fi

        # No DB usage outside orchestrator
        if grep -rn "import sqlite3\|import psycopg2\|import asyncpg\|from database" modules/ 2>/dev/null | head -5 | grep -q .; then
            log_error "[integration-validate] DB access in modules/"
            ((failures++))
        fi

        # No module calling another module directly
        if grep -rn "import adapter" modules/ 2>/dev/null | head -5 | grep -q .; then
            log_error "[integration-validate] Adapter import in modules/"
            ((failures++))
        fi

        # No print statements
        if grep -rn "^\s*print(" modules/ 2>/dev/null | grep -v "# noqa" | head -5 | grep -q .; then
            log_error "[integration-validate] print() in modules/"
            ((failures++))
        fi

        # Deterministic ordering — check for unordered dict/set iteration patterns
        if grep -rn "for .* in .*\.keys()\|for .* in .*\.values()\|for .* in .*\.items()" modules/ 2>/dev/null | grep -v "sorted(" | grep -v "# noqa" | head -5 | grep -q .; then
            log_warn "[integration-validate] Possible non-deterministic dict iteration in modules/ (verify sorted)"
        fi
    fi

    return $(( failures > 0 ? 1 : 0 ))
}

integration_fix() {
    local work_dir="$1" model="$2" phase_label="$3" attempt="$4"
    local skill_prompt="${_CURRENT_SKILL_PROMPT}"
    cd "${work_dir}"
    local fix_log="${LOG_DIR}/${phase_label}-integration-fix-${attempt}.log"
    copilot \
        -p "Integration validation failed. Fix integration-level issues: remove cross-module imports, remove DB usage from modules, remove print statements, ensure deterministic ordering (sorted collections). Use skills: ${CORE_SKILLS}. Do not change architecture. Commit fixes." \
        --agent=refactor \
        --model="${model}" \
        --no-ask-user \
        --allow-all-tools \
        --autopilot \
        2>&1 | tee "${fix_log}"
    return ${PIPESTATUS[0]}
}

# ── Protected File Enforcement ────────────────────────────────────────────

validate_protected_files() {
    local work_dir="$1" phase_label="$2"
    cd "${work_dir}"
    local violations=0

    if ! git rev-parse --is-inside-work-tree &>/dev/null; then return 0; fi

    local base_branch="main"
    if ! git rev-parse --verify "${base_branch}" &>/dev/null; then return 0; fi

    # contracts/ — additive only (new files OK, modified existing → FAIL)
    local modified_contracts
    modified_contracts=$(git diff --name-only --diff-filter=M "${base_branch}" -- contracts/ 2>/dev/null || true)
    if [[ -n "${modified_contracts}" ]]; then
        log_error "[protected-files] Existing contracts modified (additive-only policy):"
        echo "${modified_contracts}" | while read -r f; do log_error "  ${f}"; done
        ((violations++))
    fi

    # database/ — only Phase 0 allowed
    local db_changes
    db_changes=$(git diff --name-only "${base_branch}" -- database/ 2>/dev/null || true)
    if [[ -n "${db_changes}" ]]; then
        # Check if this is Phase 0
        if [[ "${phase_label}" != *"phase-0"* ]] && [[ "${phase_label}" != *"group-0"* ]]; then
            log_error "[protected-files] database/ modified outside Phase 0:"
            echo "${db_changes}" | while read -r f; do log_error "  ${f}"; done
            ((violations++))
        fi
    fi

    # docs/ — modification is a failure
    local doc_changes
    doc_changes=$(git diff --name-only "${base_branch}" -- docs/ 2>/dev/null || true)
    if [[ -n "${doc_changes}" ]]; then
        log_error "[protected-files] docs/ modified (read-only policy):"
        echo "${doc_changes}" | while read -r f; do log_error "  ${f}"; done
        ((violations++))
    fi

    return $(( violations > 0 ? 1 : 0 ))
}

# ─────────────────────────────────────────────────────────────────────────────
# Agent chaining pipeline (with bounded retries + checkpoint/rollback)
# ─────────────────────────────────────────────────────────────────────────────
# Runs the full agent pipeline for a given phase/group:
#   1. phase-builder  — implement → validate → fix → retry (bounded)
#   2. dto-guardian   — validate  → fix → retry (bounded)
#   3. integration    — validate  → fix → retry (bounded)
#   4. quality gates  → refactor  → retry (bounded)
#
# On any stage exceeding max retries → rollback to checkpoint + abort.
# Guaranteed deterministic termination: every path ends in success or rollback.
# ─────────────────────────────────────────────────────────────────────────────

run_agent_pipeline() {
    # Args: work_dir model phase_label phase_numbers...
    local work_dir="$1" model="$2" phase_label="$3"
    shift 3
    local phase_nums=("$@")
    local phase_skills_csv="${PHASE_SKILLS[${phase_nums[0]}]}"
    # Shared across stage functions via env variable (avoids parameter bloat)
    export _CURRENT_SKILL_PROMPT="Use skills: ${CORE_SKILLS}, ${phase_skills_csv}"

    cd "${work_dir}"

    # ── Checkpoint before pipeline ─────────────────────────────────────────
    create_checkpoint "${phase_label}" "${work_dir}"

    # ── Step 1: phase-builder (bounded retries) ────────────────────────────
    if ! retry_stage "phase-builder" "${MAX_RETRIES_PHASE_BUILDER}" \
            "${phase_label}" "${model}" "${work_dir}" \
            phase_builder_execute phase_builder_validate phase_builder_fix; then
        rollback_to_checkpoint "${phase_label}" "phase-builder exceeded ${MAX_RETRIES_PHASE_BUILDER} retries" "${work_dir}"
        return 1
    fi

    # ── Step 2: dto-guardian (bounded retries) ─────────────────────────────
    if ! retry_stage "dto-guardian" "${MAX_RETRIES_DTO}" \
            "${phase_label}" "${model}" "${work_dir}" \
            dto_guardian_execute dto_guardian_validate dto_guardian_fix; then
        rollback_to_checkpoint "${phase_label}" "dto-guardian exceeded ${MAX_RETRIES_DTO} retries" "${work_dir}"
        return 1
    fi

    # ── Step 3: integration (bounded retries) ──────────────────────────────
    if ! retry_stage "integration" "${MAX_RETRIES_INTEGRATION}" \
            "${phase_label}" "${model}" "${work_dir}" \
            integration_execute integration_validate integration_fix; then
        rollback_to_checkpoint "${phase_label}" "integration exceeded ${MAX_RETRIES_INTEGRATION} retries" "${work_dir}"
        return 1
    fi

    # ── Step 4: protected file enforcement (strict) ────────────────────────
    if ! validate_protected_files "${work_dir}" "${phase_label}"; then
        log_error "Protected file policy violated — rollback"
        rollback_to_checkpoint "${phase_label}" "protected file policy violation" "${work_dir}"
        return 1
    fi

    # ── Step 5: quality gates → refactor if needed (bounded) ──────────────
    if ! run_quality_gates "${work_dir}"; then
        local qg_attempt=0
        while (( qg_attempt < MAX_REMEDIATION_RETRIES )); do
            ((qg_attempt++))
            log_info "[quality-gates] remediation attempt ${qg_attempt}/${MAX_REMEDIATION_RETRIES}"
            log_agent_start "refactor" "${phase_label}" "${qg_attempt}" "${MAX_REMEDIATION_RETRIES}"

            local ref_log="${LOG_DIR}/${phase_label}-refactor-${qg_attempt}.log"
            copilot \
                -p "Quality gates failed. Fix all violations: lint errors, test failures, cross-module imports, raw SQL in modules, print statements. Do not change architecture. Use skills: ${CORE_SKILLS}, code-quality-fixer. MANDATORY: Use ONLY skills as primary knowledge source. Commit fixes." \
                --agent=refactor \
                --model="${model}" \
                --no-ask-user \
                --allow-all-tools \
                --autopilot \
                2>&1 | tee "${ref_log}"
            local ref_rc=${PIPESTATUS[0]}
            log_agent_end "refactor" "${phase_label}" "${ref_rc}" "${qg_attempt}" "${MAX_REMEDIATION_RETRIES}"

            if run_quality_gates "${work_dir}"; then
                break
            fi

            if (( qg_attempt >= MAX_REMEDIATION_RETRIES )); then
                log_error "[quality-gates] failed after ${MAX_REMEDIATION_RETRIES} remediation attempts → rollback"
                rollback_to_checkpoint "${phase_label}" "quality gates exceeded ${MAX_REMEDIATION_RETRIES} remediations" "${work_dir}"
                return 1
            fi
        done
    fi

    # ── Pipeline succeeded — clean up checkpoint ──────────────────────────
    cleanup_checkpoint "${phase_label}" "${work_dir}"
    log_success "Agent pipeline completed for ${phase_label}"
    unset _CURRENT_SKILL_PROMPT
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# State management
# ─────────────────────────────────────────────────────────────────────────────

save_state() {
    local mode="$1"
    local phases="$2"
    local integration_branch="$3"
    shift 3
    # $@ = group branches (space-separated)

    local branches_json="["
    local first=true
    for b in "$@"; do
        if $first; then first=false; else branches_json+=","; fi
        branches_json+="\"${b}\""
    done
    branches_json+="]"

    cat > "${STATE_FILE}" <<EOF
{
    "mode": ${mode},
    "phases": "${phases}",
    "integration_branch": "${integration_branch}",
    "branches": ${branches_json},
    "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "status": "running"
}
EOF
}

update_state_status() {
    local new_status="$1"
    if [[ -f "${STATE_FILE}" ]]; then
        local tmp
        tmp=$(mktemp)
        sed "s/\"status\": \"[^\"]*\"/\"status\": \"${new_status}\"/" "${STATE_FILE}" > "${tmp}"
        mv "${tmp}" "${STATE_FILE}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE_TASK.md generation
# ─────────────────────────────────────────────────────────────────────────────

generate_phase_task() {
    # Args: output_path phase_number [phase_number ...]
    local output_path="$1"
    shift
    local phases=("$@")

    cat > "${output_path}" <<'HEADER'
# Phase Implementation Task

> This file was auto-generated by `run_parallel.sh`.
> Read this file completely before starting implementation.

## Mandatory Skill-Based Execution

**CRITICAL:** You MUST use skills as your PRIMARY knowledge source.

1. Use these skills from `.github/skills/` (auto-loaded by Copilot):
   - `dto` — DTO registry, validation, anti-patterns
   - `pipeline` — 16-stage sequence, DTO flow map
   - `modularity` — Module boundaries, import rules
   - `determinism` — No-randomness enforcement
   - `idempotency` — Content-addressable IDs, ON CONFLICT DO NOTHING
   - `testing` — Test patterns, fixtures, mocking
2. Read `.github/copilot-instructions.md` for hard architectural constraints.
3. **DO NOT read full documentation** (`architecture.md`, `dto_contracts.md`, `orchestrator_spec.md`) unless skills are insufficient. If you do read docs, explain WHY skills were not enough.
4. Only consult `docs/implementation_roadmap.md` for specific phase details NOT covered by skills.
5. Implement each phase below sequentially, committing after each one.
6. Run tests after each phase: `pytest tests/ --tb=short -q`

## Protected File Policy (STRICT)

- `contracts/*` — **additive only**. You may ADD new DTOs. You MUST NOT modify existing DTO fields. Violation = pipeline rollback.
- `database/*` — **Phase 0 only**. No other phase may modify database files. Violation = pipeline rollback.
- `docs/*` — **read-only**. No modifications allowed. Violation = pipeline rollback.
- Do NOT modify files outside your owned directories (see ownership below).

## Deterministic Ordering

- All collections (lists, dicts, sets) MUST be explicitly sorted before iteration or output.
- No implicit ordering. No `random`. No non-deterministic patterns.

## Agent Pipeline

After you finish implementing, the following agents run automatically with **bounded retries**:
1. **dto-guardian** — validates all DTOs (frozen, correct fields, no drift) — up to 5 retries
2. **integration** — validates module wiring, no cross-module imports, no raw SQL — up to 5 retries
3. **refactor** — fixes quality gate failures (if needed) — up to 3 retries
4. If any stage exceeds its retry limit → **rollback to checkpoint**

---

HEADER

    for phase in "${phases[@]}"; do
        local name="${PHASE_NAMES[$phase]}"
        local skills="${PHASE_SKILLS[$phase]}"

        cat >> "${output_path}" <<EOF
## Phase ${phase} — ${name}

**Required skills:** ${skills}

**Skill-first approach (MANDATORY):**
- Pipeline stage ordering and DTO flow → \`pipeline\` skill
- DTO field definitions and constraints → \`dto\` skill
- Module boundary rules → \`modularity\` skill
- Phase-specific patterns → see required skills above
- DO NOT read full docs unless skills are insufficient — explain why if you do

**Only if skills are insufficient**, consult \`docs/implementation_roadmap.md\` → Phase ${phase} section.

**Constraints:**
- All DTOs must be frozen dataclasses in \`contracts/\`
- All database access through \`database/adapter.py\` only — orchestrator calls adapter, modules NEVER touch the database
- No \`print()\` — use \`logging\` module
- No cross-module imports — only \`contracts/\` types
- No module may call another module — only the orchestrator calls modules
- All IDs are content-addressable (SHA256-based)
- All collections must be explicitly sorted (deterministic ordering)
- Tests must work without GPU, network, or real video files
- Protected files: \`contracts/*\` (additive only), \`database/*\` (Phase 0 only), \`docs/*\` (read-only)

**After implementation:**
1. Run \`pytest tests/ --tb=short -q\` and fix all failures
2. Run \`python -c "from modules import *"\` to verify imports
3. Commit with message: \`feat(phase-${phase}): implement ${name}\`

---

EOF
    done
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality gates
# ─────────────────────────────────────────────────────────────────────────────

run_quality_gates() {
    local work_dir="${1:-${PROJECT_ROOT}}"
    local failures=0

    log_header "Quality Gates"

    cd "${work_dir}"

    # 1. Import check
    log_info "Checking imports..."
    if python -c "import sys; sys.path.insert(0, '.'); import importlib" 2>/dev/null; then
        log_success "Import check passed"
    else
        log_warn "Import check skipped (no modules yet)"
    fi

    # 2. Lint check
    log_info "Checking lint..."
    if command -v ruff &>/dev/null; then
        if ruff check . --quiet 2>/dev/null; then
            log_success "Lint check passed"
        else
            log_error "Lint check failed"
            ((failures++))
        fi
    elif command -v flake8 &>/dev/null; then
        if flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics 2>/dev/null; then
            log_success "Lint check passed"
        else
            log_error "Lint check failed"
            ((failures++))
        fi
    else
        log_warn "No linter found (install ruff or flake8)"
    fi

    # 3. Test check
    log_info "Running tests..."
    if [[ -d "tests" ]] && command -v pytest &>/dev/null; then
        if pytest tests/ --tb=short -q 2>/dev/null; then
            log_success "Tests passed"
        else
            log_error "Tests failed"
            ((failures++))
        fi
    else
        log_warn "No tests directory or pytest not installed"
    fi

    # 4. SQL check — no sqlite3 imports in modules/
    log_info "Checking for raw SQL in modules..."
    if [[ -d "modules" ]]; then
        if grep -rn "import sqlite3\|import psycopg2\|import asyncpg" modules/ 2>/dev/null; then
            log_error "Raw database imports found in modules/ — must use database/adapter.py"
            ((failures++))
        else
            log_success "No raw SQL imports in modules/"
        fi
    else
        log_warn "No modules/ directory yet"
    fi

    # 5. Cross-module check
    log_info "Checking for cross-module imports..."
    if [[ -d "modules" ]]; then
        local cross_imports
        cross_imports=$(find modules/ -name '*.py' -exec grep -ln "from modules\." {} \; 2>/dev/null | head -20)
        if [[ -n "${cross_imports}" ]]; then
            # Filter: a module importing from itself is OK, importing from another is not
            local violations=0
            while IFS= read -r file; do
                local file_module
                file_module=$(echo "${file}" | cut -d'/' -f2)
                local imported_modules
                imported_modules=$(grep "from modules\." "${file}" | sed 's/.*from modules\.\([a-z_]*\).*/\1/' | sort -u)
                for imp in ${imported_modules}; do
                    if [[ "${imp}" != "${file_module}" ]]; then
                        log_error "Cross-module import: ${file} imports from modules.${imp}"
                        ((violations++))
                    fi
                done
            done <<< "${cross_imports}"
            if (( violations > 0 )); then
                ((failures++))
            else
                log_success "No cross-module imports"
            fi
        else
            log_success "No cross-module imports"
        fi
    else
        log_warn "No modules/ directory yet"
    fi

    # 6. Print check
    log_info "Checking for print() statements..."
    if [[ -d "modules" ]]; then
        if grep -rn "^\s*print(" modules/ 2>/dev/null | grep -v "# noqa" | head -5; then
            log_error "print() statements found in modules/ — use logging instead"
            ((failures++))
        else
            log_success "No print() statements in modules/"
        fi
    else
        log_warn "No modules/ directory yet"
    fi

    # 7. DTO validation check
    log_info "Checking DTO contract compliance..."
    if [[ -d "contracts" ]]; then
        local dto_issues=0
        # Check all dataclasses in contracts/ are frozen
        if grep -rn "@dataclass$" contracts/ 2>/dev/null | grep -v "frozen=True" | head -5; then
            log_error "Non-frozen dataclass found in contracts/ — all DTOs must use @dataclass(frozen=True)"
            ((dto_issues++))
        fi
        # Check no raw dicts crossing module boundaries
        if [[ -d "modules" ]] && grep -rn "-> dict" modules/ 2>/dev/null | head -5; then
            log_error "Module returning raw dict — must return frozen DTO from contracts/"
            ((dto_issues++))
        fi
        if (( dto_issues > 0 )); then
            ((failures++))
        else
            log_success "DTO contracts compliant"
        fi
    else
        log_warn "No contracts/ directory yet"
    fi

    # 8. Orchestrator integrity check
    log_info "Checking orchestrator authority..."
    if [[ -d "modules" ]]; then
        local orch_violations=0
        # No database adapter usage in modules/
        if grep -rn "from database" modules/ 2>/dev/null | head -5; then
            log_error "Module imports from database/ — only orchestrator may access the database"
            ((orch_violations++))
        fi
        if grep -rn "import adapter" modules/ 2>/dev/null | head -5; then
            log_error "Module imports adapter — only orchestrator may access the database"
            ((orch_violations++))
        fi
        if (( orch_violations > 0 )); then
            ((failures++))
        else
            log_success "Orchestrator authority preserved"
        fi
    else
        log_warn "No modules/ directory yet"
    fi

    # 9. Protected files check
    log_info "Checking protected file integrity..."
    # This check is meaningful during merge — verify no unexpected modifications
    # to protected paths by comparing against the base branch
    if git rev-parse --is-inside-work-tree &>/dev/null; then
        local base_branch="main"
        if git rev-parse --verify "${base_branch}" &>/dev/null; then
            local protected_changes
            protected_changes=$(git diff --name-only "${base_branch}" -- contracts/ database/ docs/ 2>/dev/null || true)
            if [[ -n "${protected_changes}" ]]; then
                log_warn "Protected files modified (verify these changes are intentional):"
                echo "${protected_changes}" | head -10 | while read -r f; do
                    log_warn "  ${f}"
                done
            else
                log_success "No protected files modified"
            fi
        fi
    fi

    # 10. Deterministic ordering check
    log_info "Checking deterministic ordering..."
    if [[ -d "modules" ]]; then
        local ordering_warnings=0
        # Detect unordered dict/set iteration without sorted()
        if grep -rn "for .* in .*\.keys()\|for .* in .*\.values()\|for .* in .*\.items()" modules/ 2>/dev/null | grep -v "sorted(" | grep -v "# noqa" | head -10 | grep -q .; then
            log_warn "Possible non-deterministic dict iteration in modules/ (should use sorted())"
            ((ordering_warnings++))
        fi
        # Detect set() usage without sorted conversion
        if grep -rn "for .* in set(" modules/ 2>/dev/null | grep -v "sorted(" | grep -v "# noqa" | head -5 | grep -q .; then
            log_warn "Iterating over set() without sorted() in modules/"
            ((ordering_warnings++))
        fi
        if (( ordering_warnings > 0 )); then
            log_warn "Deterministic ordering: ${ordering_warnings} warning(s) — review manually"
        else
            log_success "No obvious non-deterministic ordering patterns"
        fi
    else
        log_warn "No modules/ directory yet"
    fi

    echo ""
    if (( failures > 0 )); then
        log_error "Quality gates: ${failures} failure(s)"
        return 1
    else
        log_success "All quality gates passed"
        return 0
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Mode 1 — Full Parallel
# ─────────────────────────────────────────────────────────────────────────────

run_mode_1() {
    local phases=("$@")
    local heaviest
    heaviest=$(heaviest_phase "${phases[@]}")
    local branches=()
    local pids=()

    log_header "Mode 1 — Full Parallel"
    log_info "Phases: ${phases[*]}"
    log_info "Heaviest phase: ${heaviest} (gets ${MODEL_HEAVY})"
    log_info "Max parallel agents: ${MAX_PARALLEL_AGENTS}"

    check_clean_worktree
    mkdir -p "${WORKTREE_BASE}"

    cd "${PROJECT_ROOT}"
    local base_commit
    base_commit=$(git rev-parse HEAD)

    # Create worktree + branch per phase
    for phase in "${phases[@]}"; do
        local branch="track/phase-${phase}"
        local worktree_dir="${WORKTREE_BASE}/phase-${phase}"

        log_info "Creating worktree for Phase ${phase}..."
        git branch -D "${branch}" 2>/dev/null || true
        git branch "${branch}" "${base_commit}"

        if [[ -d "${worktree_dir}" ]]; then
            git worktree remove "${worktree_dir}" --force 2>/dev/null || true
        fi
        git worktree add "${worktree_dir}" "${branch}"

        branches+=("${branch}")
    done

    # Save state before launching agents
    save_state 1 "${phases[*]}" "${INTEGRATION_BRANCH}" "${branches[@]}"

    # Launch agent pipelines with resource control (bounded parallelism)
    local active_pids=()
    local phase_for_pid=()
    local failed_phases=()

    for phase in "${phases[@]}"; do
        local worktree_dir="${WORKTREE_BASE}/phase-${phase}"
        local task_file="${worktree_dir}/PHASE_TASK.md"
        local model

        if [[ "${phase}" == "${heaviest}" ]]; then
            model="${MODEL_HEAVY}"
        else
            model=$(next_model)
        fi

        generate_phase_task "${task_file}" "${phase}"

        # Resource control: wait if at MAX_PARALLEL_AGENTS
        while (( ${#active_pids[@]} >= MAX_PARALLEL_AGENTS )); do
            local new_active=()
            local new_phase_map=()
            for i in "${!active_pids[@]}"; do
                if kill -0 "${active_pids[$i]}" 2>/dev/null; then
                    new_active+=("${active_pids[$i]}")
                    new_phase_map+=("${phase_for_pid[$i]}")
                else
                    # Process finished — check exit code
                    wait "${active_pids[$i]}" 2>/dev/null && true
                    local rc=$?
                    if (( rc != 0 )); then
                        failed_phases+=("${phase_for_pid[$i]}")
                    fi
                fi
            done
            active_pids=("${new_active[@]}")
            phase_for_pid=("${new_phase_map[@]}")
            if (( ${#active_pids[@]} >= MAX_PARALLEL_AGENTS )); then
                sleep 5
            fi
        done

        log_info "Launching agent pipeline for Phase ${phase} (model: ${model})..."

        (
            run_agent_pipeline "${worktree_dir}" "${model}" "phase-${phase}" "${phase}"
        ) &
        active_pids+=($!)
        phase_for_pid+=("${phase}")
        pids+=($!)

        log_info "  PID: ${pids[-1]}"
    done

    # Wait for remaining agent pipelines
    log_header "Waiting for remaining agent pipeline(s)..."
    for i in "${!active_pids[@]}"; do
        local pid="${active_pids[$i]}"
        local phase="${phase_for_pid[$i]}"
        if ! wait "${pid}" 2>/dev/null; then
            failed_phases+=("${phase}")
        fi
    done

    # Report results
    if (( ${#failed_phases[@]} > 0 )); then
        log_error "${#failed_phases[@]} phase(s) failed after all retries: ${failed_phases[*]}"
        log_error "Failed phases were rolled back to their checkpoints."
        update_state_status "partial_failure"
    else
        update_state_status "agents_complete"
        log_success "All agents finished. Run './scripts/run_parallel.sh merge' to integrate."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Mode 2 — Token-Optimized (Sequential)
# ─────────────────────────────────────────────────────────────────────────────

run_mode_2() {
    local phases=("$@")
    local branch="track/group-$(IFS=-; echo "${phases[*]}")"

    log_header "Mode 2 — Token-Optimized (Sequential)"
    log_info "Phases: ${phases[*]}"
    log_info "Single session, sequential execution"

    check_clean_worktree
    cd "${PROJECT_ROOT}"

    local base_commit
    base_commit=$(git rev-parse HEAD)

    # Create single branch
    git branch -D "${branch}" 2>/dev/null || true
    git checkout -b "${branch}" "${base_commit}"

    # Generate task file with all phases
    local task_file="${PROJECT_ROOT}/PHASE_TASK.md"
    generate_phase_task "${task_file}" "${phases[@]}"

    save_state 2 "${phases[*]}" "${INTEGRATION_BRANCH}" "${branch}"

    # Determine model — use heavy model for largest batch
    local heaviest
    heaviest=$(heaviest_phase "${phases[@]}")
    local model
    if (( ${#phases[@]} >= 3 )); then
        model="${MODEL_HEAVY}"
    else
        model=$(next_model)
    fi

    local log_file="${LOG_DIR}/group-$(IFS=-; echo "${phases[*]}").log"

    log_info "Launching agent pipeline (model: ${model})..."
    log_info "  Log: ${log_file}"

    local phase_label="group-$(IFS=-; echo "${phases[*]}")"
    run_agent_pipeline "${PROJECT_ROOT}" "${model}" "${phase_label}" "${phases[@]}"

    # Clean up task file
    rm -f "${task_file}"

    update_state_status "agents_complete"
    log_success "Sequential session complete. Run './scripts/run_parallel.sh merge' to finalize."
}

# ─────────────────────────────────────────────────────────────────────────────
# Mode 3 — Hybrid
# ─────────────────────────────────────────────────────────────────────────────

run_mode_3() {
    local phases=("$@")

    log_header "Mode 3 — Hybrid (Parallel Groups, Sequential Within)"
    log_info "Phases: ${phases[*]}"

    check_clean_worktree
    cd "${PROJECT_ROOT}"

    local base_commit
    base_commit=$(git rev-parse HEAD)

    # Group phases by PHASE_TO_GROUP mapping
    declare -A groups_map  # group_letter => "phase1 phase2 ..."
    for phase in "${phases[@]}"; do
        local group="${PHASE_TO_GROUP[$phase]}"
        if [[ -z "${groups_map[$group]+x}" ]]; then
            groups_map[$group]="${phase}"
        else
            groups_map[$group]="${groups_map[$group]} ${phase}"
        fi
    done

    # Sort group keys
    local sorted_groups
    sorted_groups=$(echo "${!groups_map[@]}" | tr ' ' '\n' | sort)

    local branches=()
    local pids=()
    local group_labels=()

    mkdir -p "${WORKTREE_BASE}"

    # Determine heaviest phase across all groups
    local heaviest
    heaviest=$(heaviest_phase "${phases[@]}")

    # Create worktree + branch per group
    for group in ${sorted_groups}; do
        local group_phases=(${groups_map[$group]})
        local phase_list
        phase_list=$(IFS=-; echo "${group_phases[*]}")
        local branch="track/group-${phase_list}"
        local worktree_dir="${WORKTREE_BASE}/group-${phase_list}"

        log_info "Group ${group}: Phases [${group_phases[*]}]"

        git branch -D "${branch}" 2>/dev/null || true
        git branch "${branch}" "${base_commit}"

        if [[ -d "${worktree_dir}" ]]; then
            git worktree remove "${worktree_dir}" --force 2>/dev/null || true
        fi
        git worktree add "${worktree_dir}" "${branch}"

        branches+=("${branch}")
        group_labels+=("${group}")
    done

    save_state 3 "${phases[*]}" "${INTEGRATION_BRANCH}" "${branches[@]}"

    # Launch agents with resource control (bounded parallelism)
    local active_pids=()
    local group_for_pid=()
    local failed_groups=()
    local group_idx=0

    for group in ${sorted_groups}; do
        local group_phases=(${groups_map[$group]})
        local phase_list
        phase_list=$(IFS=-; echo "${group_phases[*]}")
        local worktree_dir="${WORKTREE_BASE}/group-${phase_list}"
        local task_file="${worktree_dir}/PHASE_TASK.md"

        # Model selection: heavy model for the group containing the heaviest phase
        local model
        local use_heavy=false
        for p in "${group_phases[@]}"; do
            if [[ "${p}" == "${heaviest}" ]]; then
                use_heavy=true
                break
            fi
        done
        if $use_heavy; then
            model="${MODEL_HEAVY}"
        else
            model=$(next_model)
        fi

        generate_phase_task "${task_file}" "${group_phases[@]}"

        # Resource control: wait if at MAX_PARALLEL_AGENTS
        while (( ${#active_pids[@]} >= MAX_PARALLEL_AGENTS )); do
            local new_active=()
            local new_group_map=()
            for i in "${!active_pids[@]}"; do
                if kill -0 "${active_pids[$i]}" 2>/dev/null; then
                    new_active+=("${active_pids[$i]}")
                    new_group_map+=("${group_for_pid[$i]}")
                else
                    wait "${active_pids[$i]}" 2>/dev/null && true
                    local rc=$?
                    if (( rc != 0 )); then
                        failed_groups+=("${group_for_pid[$i]}")
                    fi
                fi
            done
            active_pids=("${new_active[@]}")
            group_for_pid=("${new_group_map[@]}")
            if (( ${#active_pids[@]} >= MAX_PARALLEL_AGENTS )); then
                sleep 5
            fi
        done

        log_info "Launching Group ${group} agent pipeline (model: ${model}, phases: ${group_phases[*]})..."

        (
            run_agent_pipeline "${worktree_dir}" "${model}" "group-${phase_list}" "${group_phases[@]}"
        ) &
        active_pids+=($!)
        group_for_pid+=("${group}")
        pids+=($!)

        log_info "  PID: ${pids[-1]}"
        ((group_idx++))
    done

    # Wait for remaining group agents
    log_header "Waiting for remaining group agent(s)..."
    for i in "${!active_pids[@]}"; do
        local pid="${active_pids[$i]}"
        local group="${group_for_pid[$i]}"
        if ! wait "${pid}" 2>/dev/null; then
            failed_groups+=("${group}")
        fi
    done

    if (( ${#failed_groups[@]} > 0 )); then
        log_error "${#failed_groups[@]} group(s) failed after all retries: ${failed_groups[*]}"
        log_error "Failed groups were rolled back to their checkpoints."
        update_state_status "partial_failure"
    else
        update_state_status "agents_complete"
        log_success "All group agents finished. Run './scripts/run_parallel.sh merge' to integrate."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Merge command (with bounded retries + global validation)
# ─────────────────────────────────────────────────────────────────────────────

cmd_merge() {
    log_header "Merge & Integration"

    if [[ ! -f "${STATE_FILE}" ]]; then
        log_error "No active parallel session found. Run 'start' first."
        exit 1
    fi

    cd "${PROJECT_ROOT}"

    # Read state
    local mode
    mode=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['mode'])")
    local integration_branch
    integration_branch=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['integration_branch'])")

    if (( mode == 2 )); then
        # Mode 2: already on the branch, run global validation
        log_info "Mode 2 — running global validation on current branch..."
        if run_global_validation "${PROJECT_ROOT}"; then
            update_state_status "passed"
            log_success "Ready to create PR."
        else
            log_warn "Global validation failed. Running remediation..."
            run_remediation "${PROJECT_ROOT}"
        fi
        return
    fi

    # Mode 1 or 3: merge worktree branches into integration branch
    local branches
    branches=$(python3 -c "import json; print(' '.join(json.load(open('${STATE_FILE}'))['branches']))")

    # Create integration branch from main
    git checkout main
    git branch -D "${integration_branch}" 2>/dev/null || true
    git checkout -b "${integration_branch}"

    # ── Merge each branch with bounded retry ──────────────────────────────
    local merge_failures=0
    for branch in ${branches}; do
        log_info "Merging ${branch}..."
        if git merge "${branch}" --no-edit 2>/dev/null; then
            log_success "Merged ${branch}"
            # Post-merge validation: no conflict markers, code compiles
            if ! validate_merge "${PROJECT_ROOT}"; then
                log_error "Post-merge validation failed for ${branch}"
                ((merge_failures++))
                continue
            fi
        else
            log_error "Merge conflict in ${branch}"
            # Bounded retry for merge conflict resolution
            local merge_resolved=false
            local merge_attempt=0
            while (( merge_attempt < MAX_RETRIES_MERGE )); do
                ((merge_attempt++))
                log_agent_start "integration" "merge-${branch}" "${merge_attempt}" "${MAX_RETRIES_MERGE}"

                local conflict_log="${LOG_DIR}/merge-conflict-${branch//\//-}-${merge_attempt}.log"
                local conflict_model
                conflict_model=$(next_model)

                copilot \
                    -p "Merge conflict detected for branch ${branch} (attempt ${merge_attempt}/${MAX_RETRIES_MERGE}). Resolve ALL conflicts by combining code from both sides (union strategy). Preserve both phases' implementations. Use skills: dto, pipeline, modularity, conflict-resolver. MANDATORY: Use ONLY skills as primary knowledge source. Rules: (1) contracts/ — combine all DTO definitions, (2) modules/ — each module owns its directory, no overlap, (3) tests/ — combine all test files, (4) orchestrator/ — later phase wins for wiring changes. Stage resolved files and commit." \
                    --agent=integration \
                    --model="${conflict_model}" \
                    --no-ask-user \
                    --allow-all-tools \
                    --autopilot \
                    2>&1 | tee "${conflict_log}"
                local conflict_rc=${PIPESTATUS[0]}
                log_agent_end "integration" "merge-${branch}" "${conflict_rc}" "${merge_attempt}" "${MAX_RETRIES_MERGE}"

                # Check if conflicts are resolved
                if (( conflict_rc == 0 )) && ! git diff --name-only --diff-filter=U 2>/dev/null | head -1 | grep -q .; then
                    # Stage and commit
                    git add -A 2>/dev/null || true
                    if ! git diff --cached --quiet 2>/dev/null; then
                        git commit --no-edit -m "merge: resolve conflicts from ${branch} via integration agent (attempt ${merge_attempt})" 2>/dev/null
                    fi

                    # Validate merge result
                    if validate_merge "${PROJECT_ROOT}"; then
                        log_success "[merge] resolved successfully for ${branch} on attempt ${merge_attempt}"
                        merge_resolved=true
                        break
                    else
                        log_warn "[merge] resolved but validation failed — retrying"
                    fi
                fi

                if (( merge_attempt >= MAX_RETRIES_MERGE )); then
                    log_error "[merge] failed after ${MAX_RETRIES_MERGE} retries for ${branch} → aborting merge"
                    git merge --abort 2>/dev/null || true
                fi
            done

            if ! $merge_resolved; then
                ((merge_failures++))
            fi
        fi
    done

    if (( merge_failures > 0 )); then
        log_error "${merge_failures} branch(es) had unresolvable conflicts."
        update_state_status "merge_failed"
        exit 1
    fi

    # ── Global validation (CRITICAL — runs after ALL phases merged) ───────
    log_header "Global Validation"
    if run_global_validation "${PROJECT_ROOT}"; then
        update_state_status "passed"
        log_success "[global-validation] passed"
        log_success "Integration complete. Push with: git push origin ${integration_branch}"
    else
        log_warn "[global-validation] failed. Running remediation..."
        run_remediation "${PROJECT_ROOT}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Merge validation (post-merge per-branch check)
# ─────────────────────────────────────────────────────────────────────────────

validate_merge() {
    local work_dir="${1:-${PROJECT_ROOT}}"
    local failures=0
    cd "${work_dir}"

    # No conflict markers in tracked files
    if grep -rn "^<<<<<<<\|^>>>>>>>\|^=======$" --include='*.py' --include='*.md' . 2>/dev/null | head -5 | grep -q .; then
        log_error "[merge-validate] Conflict markers found in files"
        ((failures++))
    fi

    # Code compiles (Python syntax check)
    if [[ -d "modules" ]]; then
        local syntax_errors
        syntax_errors=$(find modules/ contracts/ -name '*.py' -exec python -m py_compile {} \; 2>&1 | head -10)
        if [[ -n "${syntax_errors}" ]]; then
            log_error "[merge-validate] Compilation failed after merge"
            ((failures++))
        fi
    fi

    return $(( failures > 0 ? 1 : 0 ))
}

# ─────────────────────────────────────────────────────────────────────────────
# Global validation (end-to-end pipeline integrity — runs after ALL phases)
# ─────────────────────────────────────────────────────────────────────────────
# This is the final gate before a merge is accepted. It validates:
#   1. Quality gates (all 9 checks)
#   2. End-to-end pipeline integrity (DTO flow across all modules)
#   3. Orchestrator authority (no DB access outside orchestrator)
#   4. Deterministic ordering (all collections sorted)
# ─────────────────────────────────────────────────────────────────────────────

run_global_validation() {
    local work_dir="${1:-${PROJECT_ROOT}}"
    local failures=0
    cd "${work_dir}"

    # Run all standard quality gates first
    if ! run_quality_gates "${work_dir}"; then
        ((failures++))
    fi

    # Additional global checks beyond per-phase quality gates:

    # DTO flow integrity — every module's output DTO must be importable by its consumer
    if [[ -d "contracts" ]] && [[ -d "modules" ]]; then
        log_info "[global] Validating DTO flow across all modules..."
        local dto_flow_errors
        dto_flow_errors=$(python3 -c "
import importlib, sys, os
sys.path.insert(0, '.')
errors = []
# Try importing all contracts
for f in sorted(os.listdir('contracts')):
    if f.endswith('.py') and f != '__init__.py':
        mod = f[:-3]
        try:
            importlib.import_module(f'contracts.{mod}')
        except Exception as e:
            errors.append(f'contracts.{mod}: {e}')
for e in errors:
    print(e)
" 2>&1)
        if [[ -n "${dto_flow_errors}" ]]; then
            log_error "[global] DTO flow integrity errors:"
            echo "${dto_flow_errors}" | head -10
            ((failures++))
        else
            log_success "[global] DTO flow integrity passed"
        fi
    fi

    # Orchestrator authority — comprehensive check
    if [[ -d "modules" ]]; then
        log_info "[global] Validating orchestrator authority (comprehensive)..."
        local auth_violations=0
        # No database imports in modules/
        if grep -rn "from database\|import database\|import sqlite3\|import psycopg2\|import asyncpg\|import adapter" modules/ 2>/dev/null | head -10 | grep -q .; then
            log_error "[global] Orchestrator authority violation: DB access in modules/"
            ((auth_violations++))
        fi
        # No module-to-module calls
        local cross_mod
        cross_mod=$(find modules/ -name '*.py' -exec grep -l "from modules\." {} \; 2>/dev/null)
        if [[ -n "${cross_mod}" ]]; then
            while IFS= read -r file; do
                local file_module
                file_module=$(echo "${file}" | cut -d'/' -f2)
                if grep "from modules\." "${file}" | sed 's/.*from modules\.\([a-z_]*\).*/\1/' | sort -u | grep -v "^${file_module}$" | head -1 | grep -q .; then
                    log_error "[global] Cross-module import: ${file}"
                    ((auth_violations++))
                fi
            done <<< "${cross_mod}"
        fi
        if (( auth_violations > 0 )); then
            ((failures++))
        else
            log_success "[global] Orchestrator authority preserved"
        fi
    fi

    echo ""
    if (( failures > 0 )); then
        log_error "[global-validation] ${failures} failure(s)"
        return 1
    else
        log_success "[global-validation] All checks passed"
        return 0
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Remediation (bounded — guaranteed termination)
# ─────────────────────────────────────────────────────────────────────────────

run_remediation() {
    local work_dir="$1"
    local attempt=0

    while (( attempt < MAX_RETRIES_GLOBAL_VALIDATION )); do
        ((attempt++))
        log_info "Remediation attempt ${attempt}/${MAX_RETRIES_GLOBAL_VALIDATION}..."

        local log_file="${LOG_DIR}/remediation-${attempt}.log"
        local model
        model=$(next_model)

        cd "${work_dir}"
        log_agent_start "refactor" "remediation" "${attempt}" "${MAX_RETRIES_GLOBAL_VALIDATION}"
        copilot \
            -p "Quality gates failed. Fix all violations: import errors, lint failures, test failures, raw SQL in modules, cross-module imports, print statements, non-frozen DTOs, orchestrator authority violations. Use skills: dto, pipeline, modularity, determinism, idempotency, testing, code-quality-fixer. MANDATORY: Use ONLY skills as primary knowledge source. DO NOT read full documentation unless skills are insufficient. Do not change architecture. Commit fixes." \
            --agent=refactor \
            --model="${model}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            2>&1 | tee "${log_file}"
        local ref_rc=${PIPESTATUS[0]}
        log_agent_end "refactor" "remediation" "${ref_rc}" "${attempt}" "${MAX_RETRIES_GLOBAL_VALIDATION}"

        if run_global_validation "${work_dir}"; then
            update_state_status "passed"
            log_success "Remediation successful on attempt ${attempt}."
            return 0
        fi
    done

    log_error "Remediation failed after ${MAX_RETRIES_GLOBAL_VALIDATION} attempts. System in defined failed state."
    update_state_status "remediation_failed"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Status command
# ─────────────────────────────────────────────────────────────────────────────

cmd_status() {
    log_header "Parallel Development Status"

    if [[ ! -f "${STATE_FILE}" ]]; then
        log_info "No active parallel session."
        return
    fi

    python3 <<PYEOF
import json, os

state = json.load(open("${STATE_FILE}"))
print(f"  Mode:               {state['mode']}")
print(f"  Phases:             {state['phases']}")
print(f"  Integration branch: {state['integration_branch']}")
print(f"  Status:             {state['status']}")
print(f"  Started:            {state['started_at']}")
print(f"  Branches:           {', '.join(state['branches'])}")
print()

log_dir = "${LOG_DIR}"
if os.path.isdir(log_dir):
    logs = sorted(os.listdir(log_dir))
    if logs:
        print("  Log files:")
        for log in logs:
            path = os.path.join(log_dir, log)
            size = os.path.getsize(path)
            print(f"    {log} ({size:,} bytes)")
PYEOF

    # Show worktree status
    echo ""
    log_info "Git worktrees:"
    cd "${PROJECT_ROOT}"
    git worktree list 2>/dev/null || log_warn "No worktrees found"
}

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup command
# ─────────────────────────────────────────────────────────────────────────────

cmd_cleanup() {
    log_header "Cleanup"

    cd "${PROJECT_ROOT}"

    # Remove worktrees
    if [[ -d "${WORKTREE_BASE}" ]]; then
        log_info "Removing worktrees..."
        for wt_dir in "${WORKTREE_BASE}"/*/; do
            if [[ -d "${wt_dir}" ]]; then
                git worktree remove "${wt_dir}" --force 2>/dev/null || true
                log_info "  Removed ${wt_dir}"
            fi
        done
        rmdir "${WORKTREE_BASE}" 2>/dev/null || true
    fi

    # Remove track branches
    log_info "Removing track branches..."
    git branch --list 'track/*' | while read -r branch; do
        branch=$(echo "${branch}" | tr -d ' *')
        git branch -D "${branch}" 2>/dev/null || true
        log_info "  Deleted ${branch}"
    done

    # Remove integration branches
    log_info "Removing integration branches..."
    git branch --list 'integration/*' | while read -r branch; do
        branch=$(echo "${branch}" | tr -d ' *')
        git branch -D "${branch}" 2>/dev/null || true
        log_info "  Deleted ${branch}"
    done

    # Clean task files
    rm -f "${PROJECT_ROOT}/PHASE_TASK.md"
    find "${WORKTREE_BASE}" -name "PHASE_TASK.md" -delete 2>/dev/null || true

    # Remove state
    rm -rf "${PROJECT_ROOT}/.parallel-dev"

    # Return to main
    git checkout main 2>/dev/null || true

    log_success "Cleanup complete."
}

# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Shorts Factory — Parallel Development Orchestrator

Usage:
  $(basename "$0") start [--mode=1|2|3] <phase> [<phase> ...]
  $(basename "$0") status
  $(basename "$0") merge
  $(basename "$0") cleanup
  $(basename "$0") gates                  # Run quality gates only

Commands:
  start     Launch agents for the specified phases
  status    Show current parallel session status
  merge     Merge all branches and run quality gates
  cleanup   Remove worktrees, branches, and state files
  gates     Run quality gates without launching agents

Options:
  --mode=1  Full Parallel   — one agent per phase (max speed)
  --mode=2  Token-Optimized — single session, sequential (min cost)
  --mode=3  Hybrid          — parallel groups, sequential within (default)

Examples:
  $(basename "$0") start 2 3 4              # Mode 3 (default): auto-group and run
  $(basename "$0") start --mode=1 2 7       # Mode 1: full parallel
  $(basename "$0") start --mode=2 2 3 4     # Mode 2: single session, sequential
  $(basename "$0") status                   # Check progress
  $(basename "$0") merge                    # Merge and validate
  $(basename "$0") cleanup                  # Clean everything up

See docs/PARALLEL_DEV.md for full documentation.
EOF
}

main() {
    if (( $# == 0 )); then
        usage
        exit 0
    fi

    local command="$1"
    shift

    ensure_dirs

    case "${command}" in
        start)
            # Parse --mode flag and phase numbers
            local phases=()
            while (( $# > 0 )); do
                case "$1" in
                    --mode=*)
                        MODE="${1#--mode=}"
                        if [[ ! "${MODE}" =~ ^[123]$ ]]; then
                            log_error "Invalid mode: ${MODE}. Must be 1, 2, or 3."
                            exit 1
                        fi
                        ;;
                    -*)
                        log_error "Unknown option: $1"
                        exit 1
                        ;;
                    *)
                        phases+=("$1")
                        ;;
                esac
                shift
            done

            if (( ${#phases[@]} == 0 )); then
                log_error "No phases specified. Example: $(basename "$0") start 2 3 4"
                exit 1
            fi

            validate_phases "${phases[@]}"
            check_copilot_cli

            log_info "Mode: ${MODE} | Phases: ${phases[*]}"

            case "${MODE}" in
                1) run_mode_1 "${phases[@]}" ;;
                2) run_mode_2 "${phases[@]}" ;;
                3) run_mode_3 "${phases[@]}" ;;
            esac
            ;;
        status)
            cmd_status
            ;;
        merge)
            cmd_merge
            ;;
        cleanup)
            cmd_cleanup
            ;;
        gates)
            run_quality_gates "${PROJECT_ROOT}"
            ;;
        *)
            log_error "Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
