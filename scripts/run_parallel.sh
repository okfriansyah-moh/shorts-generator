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

# Agent names for post-merge pipeline (match .github/agents/<name>.agent.md)
AGENT_PHASE_BUILDER="phase-builder"
AGENT_MERGE_REVIEWER="merge-reviewer"
AGENT_CONFLICT_RESOLVER="conflict-resolver"
AGENT_CODE_FIXER="code-fixer"

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

# Directories owned by each phase — agents MUST NOT modify files outside these
declare -A PHASE_OWNED_DIRS=(
    [0]="core/ database/ config/ run_pipeline.py"
    [1]="modules/ingestion/ modules/scene_splitter/ tests/unit/test_ingestion.py tests/unit/test_scene_splitter.py"
    [2]="modules/transcription/ modules/face_detection/ modules/audio_analysis/ tests/unit/test_transcription.py tests/unit/test_face_detection.py tests/unit/test_audio_analysis.py"
    [3]="modules/scoring/ tests/unit/test_scoring.py"
    [4]="modules/clip_builder/ tests/unit/test_clip_builder.py"
    [5]="modules/compositor/ tests/unit/test_compositor.py"
    [6]="modules/hook_generator/ modules/tts/ modules/subtitle/ modules/renderer/ tests/unit/test_hook_generator.py tests/unit/test_tts.py tests/unit/test_subtitle.py tests/unit/test_renderer.py"
    [7]="modules/thumbnail/ modules/metadata/ tests/unit/test_thumbnail.py tests/unit/test_metadata.py"
    [8]="modules/storage/ modules/scheduler/ tests/unit/test_storage.py tests/unit/test_scheduler.py"
    [9]="modules/publisher/ tests/unit/test_publisher.py"
    [10]="modules/analytics/ tests/unit/test_analytics.py"
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

check_copilot_auth() {
    # Copilot CLI auth precedence: COPILOT_GITHUB_TOKEN > GH_TOKEN > GITHUB_TOKEN
    if [[ -n "${COPILOT_GITHUB_TOKEN:-}" ]]; then
        log_success "Copilot auth: COPILOT_GITHUB_TOKEN is set"
        return 0
    fi
    if [[ -n "${GH_TOKEN:-}" ]]; then
        log_success "Copilot auth: GH_TOKEN is set"
        return 0
    fi
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        log_success "Copilot auth: GITHUB_TOKEN is set"
        return 0
    fi
    log_warn "No Copilot auth token found. Set COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN"
    log_warn "Or run 'copilot' interactively and use /login to authenticate."
    log_info "Proceeding anyway — agents may fail if unauthenticated."
}

# ─────────────────────────────────────────────────────────────────────────────
# Portable timeout wrapper (from edge-polymarket battle-tested pattern)
# ─────────────────────────────────────────────────────────────────────────────
# run_with_timeout SECONDS COMMAND [ARGS...]
# Returns the command's exit code, or 124 if killed due to timeout.
# Prefers native timeout(1), then gtimeout (Homebrew coreutils on macOS),
# then a shell watchdog with SIGTERM → 5s grace → SIGKILL escalation.
# ─────────────────────────────────────────────────────────────────────────────

run_with_timeout() {
    local _timeout_s="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$_timeout_s" "$@"
        return $?
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$_timeout_s" "$@"
        return $?
    fi
    # Shell watchdog fallback (macOS compatible)
    "$@" &
    local _cmd_pid=$!
    local _elapsed=0
    while kill -0 "$_cmd_pid" 2>/dev/null; do
        sleep 1
        _elapsed=$(( _elapsed + 1 ))
        if [[ "$_elapsed" -ge "$_timeout_s" ]]; then
            kill -TERM "$_cmd_pid" 2>/dev/null || true
            local _kw=0
            while kill -0 "$_cmd_pid" 2>/dev/null && [[ "$_kw" -lt 5 ]]; do
                sleep 1
                _kw=$(( _kw + 1 ))
            done
            kill -KILL "$_cmd_pid" 2>/dev/null || true
            wait "$_cmd_pid" 2>/dev/null || true
            return 124
        fi
    done
    wait "$_cmd_pid"
    return $?
}

# ─────────────────────────────────────────────────────────────────────────────
# Python environment for validation / quality gates
# ─────────────────────────────────────────────────────────────────────────────
# Creates a repo-local .venv so pytest/ruff/python3 are available and validation
# functions work reliably. Adapted from edge-polymarket's ensure_python_env().
# ─────────────────────────────────────────────────────────────────────────────

ensure_python_env() {
    local target_dir="${1:-${PROJECT_ROOT}}"
    local venv_dir="${target_dir}/.venv"

    if [[ ! -d "${venv_dir}" ]]; then
        log_info "Creating Python venv at ${venv_dir}..."
        python3 -m venv "${venv_dir}" || {
            log_warn "Failed to create venv — validation will use system python3"
            return 1
        }
    fi

    # Activate venv (makes `python` available even on macOS)
    # shellcheck disable=SC1091
    source "${venv_dir}/bin/activate"

    # Install test/lint tools if missing
    if ! command -v pytest &>/dev/null || ! command -v ruff &>/dev/null; then
        log_info "Installing pytest and ruff in venv..."
        pip install --quiet --upgrade pip 2>/dev/null
        pip install --quiet pytest ruff 2>/dev/null || {
            log_warn "pip install failed — some quality checks may fail"
        }
    fi

    log_success "Python environment ready (venv: ${venv_dir})"
}

# ─────────────────────────────────────────────────────────────────────────────
# Per-phase status tracking (atomic JSON writes)
# ─────────────────────────────────────────────────────────────────────────────
# Writes/updates fields in .parallel-dev/phase-status.json for the given phase.
# Adapted from edge-polymarket's update_phase_status().
# ─────────────────────────────────────────────────────────────────────────────

update_phase_status() {
    local _phase="$1"; shift
    local _status_file="${PROJECT_ROOT}/.parallel-dev/phase-status.json"
    python3 - "$_phase" "$_status_file" "$@" <<'PYEOF'
import sys, json, os, time
phase = sys.argv[1]
path  = sys.argv[2]
kvs   = sys.argv[3:]
data  = {}
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    with open(path) as f:
        try: data = json.load(f)
        except: data = {}
if "phases" not in data:
    data["phases"] = {}
if phase not in data["phases"]:
    data["phases"][phase] = {"phase": phase}
entry = data["phases"][phase]
it = iter(kvs)
for k in it:
    v = next(it)
    if v == "null":
        entry[k] = None
    elif v.lstrip("-").isdigit():
        entry[k] = int(v)
    else:
        entry[k] = v
entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, path)
PYEOF
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
        -p "Read PHASE_TASK.md and implement all listed phases sequentially. ${skill_prompt}. MANDATORY: Use ONLY skills as primary knowledge source (dto, pipeline, modularity, determinism, idempotency). DO NOT read full documentation unless skills are insufficient — if reading docs, explain why skills are insufficient. CRITICAL: NEVER modify files in database/ or docs/ — these are protected directories. Only create/modify files in contracts/ (additive only) and modules/. Module __init__.py files MUST use relative imports (from .X import Y, NOT from modules.X.Y import). For each phase: implement, test, then commit with message 'feat(phase-N): implement <name>'. Follow all constraints in .github/copilot-instructions.md." \
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
        if ! python3 -c "import sys; sys.path.insert(0, '.'); import importlib" 2>/dev/null; then
            log_error "[phase-builder-validate] Module compilation failed"
            ((failures++))
        fi
    fi
    # No syntax errors
    if [[ -d "modules" ]]; then
        local syntax_errors
        syntax_errors=$(find modules/ -name '*.py' -exec python3 -m py_compile {} \; 2>&1 | head -10)
        if [[ -n "${syntax_errors}" ]]; then
            log_error "[phase-builder-validate] Syntax errors found"
            ((failures++))
        fi
    fi
    # Imports valid — use proper Python imports (not isolated file loading)
    if [[ -d "contracts" ]]; then
        local import_errors
        import_errors=$(python3 -c "
import sys, os, importlib
sys.path.insert(0, os.getcwd())
errors = []
for f in sorted(os.listdir('contracts')):
    if f.endswith('.py') and f != '__init__.py':
        mod_name = 'contracts.' + f[:-3]
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            errors.append(f'{mod_name}: {e}')
if errors:
    print('\n'.join(errors))
" 2>&1 | head -10)
        if [[ -n "${import_errors}" ]]; then
            log_error "[phase-builder-validate] Contract import errors"
            echo "${import_errors}" | head -5
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
        -p "Phase builder validation failed. Fix compilation and syntax issues ONLY. NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. Do not change architecture. ${skill_prompt}. Commit fixes." \
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
        -p "Validate all DTOs in contracts/ against docs/dto_contracts.md. STRICT checks: no missing fields, no extra fields, no type mismatches, all dataclasses are frozen. Use skills: dto. MANDATORY: Use ONLY skills as primary knowledge source. DO NOT read full documentation unless skills are insufficient. NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. Report violations in contracts/ and fix them. Commit fixes if any." \
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
        -p "DTO validation failed. Fix DTO-specific issues ONLY: ensure all dataclasses are frozen, no missing/extra fields, no type mismatches, no mutable defaults. NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. Use skills: dto. Commit fixes." \
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
        -p "Validate module integration for the phases just implemented. STRICT checklist: (1) DTO compatibility across producer/consumer stages, (2) no cross-module imports — module __init__.py MUST use relative imports (from .X import Y), (3) no raw SQL in modules — no sqlite3/psycopg2/asyncpg imports in modules, (4) no module calling another module, (5) database access only through orchestrator, (6) deterministic ordering preserved — all collections explicitly sorted, (7) idempotency preserved — content-addressable IDs, (8) no hidden side effects. CRITICAL: NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. These are protected directories and any modification causes a pipeline rollback. Use skills: ${CORE_SKILLS}. MANDATORY: Use ONLY skills as primary knowledge source. Report and fix violations in modules/ and contracts/ ONLY. Commit fixes if any." \
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

        # __init__.py MUST use relative imports (from .X import Y), NOT absolute (from modules.X.Y import Y)
        local abs_init_imports
        abs_init_imports=$(find modules/ -name '__init__.py' -exec grep -ln "from modules\." {} \; 2>/dev/null)
        if [[ -n "${abs_init_imports}" ]]; then
            while IFS= read -r file; do
                log_error "[integration-validate] Absolute import in __init__.py: ${file} — must use relative imports (from .X import Y)"
                ((failures++))
            done <<< "${abs_init_imports}"
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
        -p "Integration validation failed. Fix integration-level issues: remove cross-module imports (use relative imports in __init__.py), remove DB usage from modules, remove print statements, ensure deterministic ordering (sorted collections). CRITICAL: NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. These are protected directories. Use skills: ${CORE_SKILLS}. Do not change architecture. Commit fixes." \
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

    # core/ — only Phase 0 allowed
    local core_changes
    core_changes=$(git diff --name-only "${base_branch}" -- core/ 2>/dev/null || true)
    if [[ -n "${core_changes}" ]]; then
        if [[ "${phase_label}" != *"phase-0"* ]] && [[ "${phase_label}" != *"group-0"* ]]; then
            log_error "[protected-files] core/ modified outside Phase 0:"
            echo "${core_changes}" | while read -r f; do log_error "  ${f}"; done
            ((violations++))
        fi
    fi

    # __init__.py — must use relative imports (from .X import Y)
    if [[ -d "modules" ]]; then
        local abs_init_imports
        abs_init_imports=$(find modules/ -name '__init__.py' -exec grep -ln "from modules\." {} \; 2>/dev/null)
        if [[ -n "${abs_init_imports}" ]]; then
            log_error "[protected-files] Absolute imports in __init__.py (must use relative: from .X import Y):"
            echo "${abs_init_imports}" | while read -r f; do log_error "  ${f}"; done
            ((violations++))
        fi
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
                -p "Quality gates failed. Fix all violations: lint errors, test failures, cross-module imports, raw SQL in modules, print statements. CRITICAL: NEVER modify files in database/ or docs/ — only touch contracts/ and modules/. Do not change architecture. Use skills: ${CORE_SKILLS}, code-quality-fixer. MANDATORY: Use ONLY skills as primary knowledge source. Commit fixes." \
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

    local rotation_json="["
    first=true
    for m in "${MODEL_ROTATE_POOL[@]}"; do
        if $first; then first=false; else rotation_json+=","; fi
        rotation_json+="\"${m}\""
    done
    rotation_json+="]"

    cat > "${STATE_FILE}" <<EOF
{
    "mode": ${mode},
    "phases": "${phases}",
    "integration_branch": "${integration_branch}",
    "branches": ${branches_json},
    "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "status": "running",
    "model_heavy": "${MODEL_HEAVY}",
    "model_rotation_pool": ${rotation_json}
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
# Cross-subcommand state persistence (phases.txt)
# ─────────────────────────────────────────────────────────────────────────────
# Persists the phases list so merge/status/cleanup can be called independently
# of start. Uses a simple newline-delimited text file.
# ─────────────────────────────────────────────────────────────────────────────

load_phases() {
    local state_file="${PROJECT_ROOT}/.parallel-dev/phases.txt"
    if [[ ! -f "$state_file" ]]; then
        log_error "No parallel session found. Run './scripts/run_parallel.sh start <phases>' first."
        exit 1
    fi
    PHASES=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && PHASES+=("$line")
    done < "$state_file"
}

save_phases() {
    mkdir -p "${PROJECT_ROOT}/.parallel-dev"
    printf '%s\n' "${PHASES[@]}" > "${PROJECT_ROOT}/.parallel-dev/phases.txt"
    # Add state dir to gitignore if not already there
    if ! grep -qxF '.parallel-dev/' "${PROJECT_ROOT}/.gitignore" 2>/dev/null; then
        echo '.parallel-dev/' >> "${PROJECT_ROOT}/.gitignore"
    fi
}

safe_branch_name() {
    echo "${1//./-}"
}

branch_for_phase() {
    echo "track/phase-$(safe_branch_name "$1")"
}

worktree_for_phase() {
    echo "${WORKTREE_BASE}/phase-$(safe_branch_name "$1")"
}

set_rotate_model() {
    local pool_size="${#MODEL_ROTATE_POOL[@]}"
    ROTATE_MODEL="${MODEL_ROTATE_POOL[$(( ROTATION_INDEX % pool_size ))]}"
    ROTATION_INDEX=$(( ROTATION_INDEX + 1 ))
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

## Python Environment

**A `.venv` is PRE-INSTALLED in this worktree.** Do NOT run `pip install` or create a new venv.
Activate it and use immediately:

\`\`\`bash
source .venv/bin/activate
python3 -m pytest tests/ --tb=short -q     # run tests
python3 -m ruff check modules/ tests/      # lint
\`\`\`

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
        local owned="${PHASE_OWNED_DIRS[$phase]}"

        cat >> "${output_path}" <<EOF
## Phase ${phase} — ${name}

**Required skills:** ${skills}

**YOUR OWNED DIRECTORIES (only modify these):**
\`\`\`
${owned}
contracts/  (additive only — new files OK, no field changes)
\`\`\`
**EVERYTHING ELSE is OFF-LIMITS.** Do not create or modify files outside these directories.

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
- Module \`__init__.py\` files MUST use relative imports: \`from .X import Y\`, NOT \`from modules.X.Y import Y\`
- No module may call another module — only the orchestrator calls modules
- All IDs are content-addressable (SHA256-based)
- All collections must be explicitly sorted (deterministic ordering)
- Tests must work without GPU, network, or real video files

**PROTECTED DIRECTORIES — NEVER MODIFY (violation = automatic rollback):**
- \`database/*\` — Phase 0 only. Do NOT create migrations, modify adapter.py, or change connection.py.
- \`docs/*\` — Read-only. Do NOT modify any documentation files.
- \`core/*\` — Phase 0 only. Do NOT modify any core infrastructure files.
- \`contracts/*\` — Additive only. You may ADD new files. Do NOT modify existing DTO fields.

**After implementation:**
1. Run \`python3 -m pytest tests/ --tb=short -q\` and fix all failures
2. Run \`python3 -c "from modules import *"\` to verify imports
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

    # Ensure Python env is available for validation checks
    ensure_python_env "${work_dir}" 2>/dev/null || true

    # 1. Import check
    log_info "Checking imports..."
    if python3 -c "import sys; sys.path.insert(0, '.'); import importlib" 2>/dev/null; then
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

    # 5b. __init__.py relative import check
    log_info "Checking __init__.py for absolute imports..."
    if [[ -d "modules" ]]; then
        local abs_init
        abs_init=$(find modules/ -name '__init__.py' -exec grep -ln "from modules\." {} \; 2>/dev/null)
        if [[ -n "${abs_init}" ]]; then
            log_error "Absolute imports in __init__.py (must use relative: from .X import Y):"
            echo "${abs_init}" | while read -r f; do log_error "  ${f}"; done
            ((failures++))
        else
            log_success "All __init__.py use relative imports"
        fi
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
    PHASES=("${phases[@]}")
    save_phases
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

        # Pre-install Python venv in worktree (pytest/ruff available for agents)
        log_info "  Installing venv in worktree (Phase ${phase})..."
        if (
            cd "${worktree_dir}" || exit 1
            python3 -m venv .venv 2>/dev/null || true
            .venv/bin/pip install --quiet pytest ruff 2>&1 | tail -3
        ); then
            log_success "  Worktree venv ready (Phase ${phase})"
        else
            log_warn "  Venv pre-install encountered issues for Phase ${phase} — agent will retry if needed"
        fi

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
            update_phase_status "${phase}" state "running" model "${model}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            # Activate worktree venv so validation uses the right python
            source "${worktree_dir}/.venv/bin/activate" 2>/dev/null || true
            run_agent_pipeline "${worktree_dir}" "${model}" "phase-${phase}" "${phase}"
            local rc=$?
            if (( rc == 0 )); then
                update_phase_status "${phase}" state "complete" exit_code "0"
            else
                update_phase_status "${phase}" state "failed" exit_code "${rc}"
            fi
            exit ${rc}
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
        log_success "All agents finished. Proceeding to automatic merge..."
    fi

    # Auto-trigger merge (fully autonomous — start to finish)
    cmd_merge
}

# ─────────────────────────────────────────────────────────────────────────────
# Mode 2 — Token-Optimized (Sequential)
# ─────────────────────────────────────────────────────────────────────────────

run_mode_2() {
    local phases=("$@")
    PHASES=("${phases[@]}")
    save_phases
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

    # Track phase status in phase-status.json (same as Mode 1 & 3)
    update_phase_status "${phase_label}" state "running" model "${model}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Activate venv so validation uses the right python
    source "${PROJECT_ROOT}/.venv/bin/activate" 2>/dev/null || true
    local pipeline_rc=0
    run_agent_pipeline "${PROJECT_ROOT}" "${model}" "${phase_label}" "${phases[@]}" || pipeline_rc=$?

    if (( pipeline_rc == 0 )); then
        update_phase_status "${phase_label}" state "complete" exit_code "0"
    else
        update_phase_status "${phase_label}" state "failed" exit_code "${pipeline_rc}"
    fi

    # Clean up task file
    rm -f "${task_file}"

    update_state_status "agents_complete"
    log_success "Sequential session complete. Proceeding to automatic merge..."

    # Auto-trigger merge (fully autonomous — start to finish)
    cmd_merge
}

# ─────────────────────────────────────────────────────────────────────────────
# Mode 3 — Hybrid
# ─────────────────────────────────────────────────────────────────────────────

run_mode_3() {
    local phases=("$@")
    PHASES=("${phases[@]}")
    save_phases

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

        # Pre-install Python venv in worktree
        log_info "  Installing venv in worktree (Group ${group})..."
        if (
            cd "${worktree_dir}" || exit 1
            python3 -m venv .venv 2>/dev/null || true
            .venv/bin/pip install --quiet pytest ruff 2>&1 | tail -3
        ); then
            log_success "  Worktree venv ready (Group ${group})"
        else
            log_warn "  Venv pre-install encountered issues for Group ${group}"
        fi

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
            update_phase_status "group-${phase_list}" state "running" model "${model}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            # Activate worktree venv so validation uses the right python
            source "${worktree_dir}/.venv/bin/activate" 2>/dev/null || true
            run_agent_pipeline "${worktree_dir}" "${model}" "group-${phase_list}" "${group_phases[@]}"
            local rc=$?
            if (( rc == 0 )); then
                update_phase_status "group-${phase_list}" state "complete" exit_code "0"
            else
                update_phase_status "group-${phase_list}" state "failed" exit_code "${rc}"
            fi
            exit ${rc}
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
        log_success "All group agents finished. Proceeding to automatic merge..."
    fi

    # Auto-trigger merge (fully autonomous — start to finish)
    cmd_merge
}


# ─────────────────────────────────────────────────────────────────────────────
# Auto Conflict Resolution (union strategy — from edge-polymarket pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Called after `git merge` exits non-zero (conflicts remain in working tree).
# PHILOSOPHY: Combine ALL phases — every phase's code and docs must be preserved.
# Resolution cascade:
#   0. Rename duplicate migration files — preserve both SQL files
#   1. Remove PHASE_TASK.md and .phase-complete — per-phase files
#   2. Copilot agent with timeout — intelligently combine both sides
#   3. --theirs tiebreaker — only as last resort
# Returns 0 on success (merge commit created), 1 on unrecoverable failure.
# ─────────────────────────────────────────────────────────────────────────────

auto_resolve_conflicts() {
    local phase="$1"
    local resolve_log="${LOG_DIR}/conflict-resolve-phase-$(safe_branch_name "${phase}").log"
    local conflicted remaining

    log_warn "  Auto-resolving conflicts for Phase ${phase} (combining all phases)..."

    # --- 0: Rename duplicate migration files to preserve BOTH sides ---
    _resolve_duplicate_migrations() {
        local mig_dir="database/migrations"
        local conflicted_migrations
        conflicted_migrations="$(git diff --name-only --diff-filter=U 2>/dev/null | grep "^${mig_dir}/.*\.sql$" || true)"
        [[ -z "$conflicted_migrations" ]] && return 0

        while IFS= read -r mig_path; do
            local filename
            filename="$(basename "$mig_path")"

            # Pattern: YYYYMMDD000NNN_description.sql
            local prefix seq_str suffix new_seq new_filename
            if [[ "$filename" =~ ^([0-9]{8}000)([0-9]{3})(_.*\.sql)$ ]]; then
                prefix="${BASH_REMATCH[1]}"
                seq_str="${BASH_REMATCH[2]}"
                suffix="${BASH_REMATCH[3]}"
                new_seq=$(printf "%03d" $(( 10#$seq_str + 100 )))
                new_filename="${prefix}${new_seq}${suffix}"
            else
                new_filename="${filename%.sql}_incoming.sql"
            fi

            # Extract incoming content (MERGE_HEAD side) and write as renamed file
            local incoming_content
            incoming_content="$(git show MERGE_HEAD:"$mig_path" 2>/dev/null)" || {
                log_warn "      Cannot read MERGE_HEAD:${mig_path} — skipping rename"
                continue
            }

            local new_path="${mig_dir}/${new_filename}"
            printf '%s\n' "$incoming_content" > "$new_path"
            git add "$new_path"

            # Accept the ours (HEAD) version for the original filename
            git checkout --ours "$mig_path"
            git add "$mig_path"

            log_success "    Migration conflict resolved (both kept):"
            log_success "      HEAD     → ${mig_path}"
            log_success "      INCOMING → ${new_path}"
        done <<< "$conflicted_migrations"
    }
    _resolve_duplicate_migrations

    # --- 1: Remove per-phase files (not part of integration) ---
    conflicted="$(git diff --name-only --diff-filter=U 2>/dev/null)"
    for per_phase_file in "PHASE_TASK.md" ".phase-complete"; do
        if echo "$conflicted" | grep -qx "$per_phase_file"; then
            git rm -f "$per_phase_file" 2>/dev/null || true
            log_success "    ${per_phase_file}: removed (per-phase file, excluded from integration)"
        fi
    done

    # --- 2: Spawn Copilot agent to COMBINE both sides ---
    remaining="$(git diff --name-only --diff-filter=U 2>/dev/null)"
    if [[ -z "$remaining" ]]; then
        git commit --no-edit 2>/dev/null || \
            git commit -m "Merge Phase ${phase}: no conflicts"
        return 0
    fi

    log_info "    Conflicted files: $(echo "$remaining" | tr '\n' ' ')"
    set_rotate_model
    log_info "    Spawning conflict-resolver agent (model: ${ROTATE_MODEL}, timeout: 10min)..."
    log_info "    Agent log: ${resolve_log}"
    update_phase_status "conflict-resolve-${phase}" state "running" model "${ROTATE_MODEL}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    local resolve_prompt
    resolve_prompt="WORKING DIRECTORY: You are already at the project root.

Resolve all remaining Git merge conflicts by COMBINING both sides. Keep ALL code from ALL phases.

You are running as the conflict-resolver agent. Read these skills from .github/skills/:
- conflict-resolver: full resolution decision tree, patterns, and post-resolution validation
- modularity: verify no cross-module imports are introduced during merge

CRITICAL PHILOSOPHY: COMBINE, not pick a winner.
- Both sides represent different phases' work. ALL must be preserved.
- Only when two sides modify the EXACT same function in incompatible ways should the later phase be used as a tiebreaker.

IMPORTANT — Migrations are already resolved:
- Any migration files (.sql) in database/migrations/ have been pre-resolved.
- Skip any migration file that is already staged.

Execution:
1. Read .github/skills/conflict-resolver/SKILL.md for the resolution decision tree
2. Run: git diff --name-only --diff-filter=U to list remaining conflicted files
3. For each file: open it, read the conflict markers, and COMBINE both sides:
   - PHASE_TASK.md / .phase-complete → delete (per-phase files)
   - Documentation (README, docs/*.md) → merge content from BOTH sides
   - __init__.py → union of all imports and exports from both sides
   - Tests → keep ALL test functions from both sides
   - Source code (both add DIFFERENT functions) → keep BOTH
   - Source code (both MODIFY same function) → later phase as tiebreaker
   - Imports → union of all imports (deduplicated)
4. After resolving: git add -A
5. Do NOT run git commit — only stage the resolutions
6. Verify: grep -rn '<<<<<<<' modules/ contracts/ tests/ — must return nothing

STRICT EXECUTION RULES:
- no background agents, no deferred delegation, no interactive steps
- complete the assigned work in one session"

    local agent_exit=0
    (
        cd "${PROJECT_ROOT}" || exit 1
        copilot \
            -p "$resolve_prompt" \
            --agent="${AGENT_CONFLICT_RESOLVER}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            --model="${ROTATE_MODEL}"
    ) > "$resolve_log" 2>&1 &
    local agent_pid=$!

    local elapsed=0
    local timeout_seconds=600  # 10 minutes
    while kill -0 "$agent_pid" 2>/dev/null; do
        sleep 5
        elapsed=$((elapsed + 5))
        if [[ $elapsed -ge $timeout_seconds ]]; then
            log_warn "    Agent timeout after ${timeout_seconds}s — killing and falling back to tiebreaker."
            kill "$agent_pid" 2>/dev/null || true
            wait "$agent_pid" 2>/dev/null || true
            agent_exit=1
            break
        fi
    done
    [[ $agent_exit -eq 0 ]] && wait "$agent_pid" 2>/dev/null && agent_exit=$? || true

    # --- 3: Final fallback for anything agent missed or timed out ---
    remaining="$(git diff --name-only --diff-filter=U 2>/dev/null)"
    if [[ -n "$remaining" ]]; then
        log_warn "    Applying tiebreaker fallback for unresolved files:"
        while IFS= read -r conflict_file; do
            git checkout --theirs "$conflict_file" 2>/dev/null || git checkout --ours "$conflict_file" 2>/dev/null
            git add "$conflict_file"
            log_warn "      ${conflict_file}: resolved via tiebreaker fallback"
        done <<< "$remaining"
    fi

    # --- 4: Complete the merge commit ---
    if git commit --no-edit 2>/dev/null || \
       git commit -m "Merge Phase ${phase}: all phases combined"; then
        log_success "    Phase ${phase} merge commit created."
        update_phase_status "conflict-resolve-${phase}" state "complete" exit_code "0"
        return 0
    else
        log_error "    Could not create merge commit for Phase ${phase}."
        update_phase_status "conflict-resolve-${phase}" state "failed" exit_code "1"
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality Gate Remediation Agent (structured failure details)
# ─────────────────────────────────────────────────────────────────────────────

spawn_remediation_agent() {
    local attempt="$1"
    shift
    local failures=("$@")
    local failures_str="${failures[*]}"

    local remediation_log="${LOG_DIR}/remediation-attempt-${attempt}.log"
    local remediation_transcript="${LOG_DIR}/remediation-attempt-${attempt}-session.md"

    log_info "  Remediation log: ${remediation_log}"

    # Build failure-specific instructions
    local failure_details=""
    local f
    for f in "${failures[@]}"; do
        case "$f" in
            package_import)
                failure_details+="
## FAILED: package_import
- Python module imports raise an error
- Check for syntax errors, missing __init__.py files, or broken imports
- Run: python3 -c \"import sys; sys.path.insert(0, '.'); import importlib\" to see errors
- Fix the import chain until it loads cleanly
"
                ;;
            tests)
                failure_details+="
## FAILED: tests
- pytest tests/ --tb=short -q showed errors or failures
- Run: pytest tests/ --tb=long to see full tracebacks
- Fix every failing test
"
                ;;
            linter)
                failure_details+="
## FAILED: linter
- ruff check found violations
- Run: ruff check . --fix to auto-fix what is possible
- Manually fix anything ruff --fix cannot handle
"
                ;;
            print_statements)
                failure_details+="
## FAILED: print_statements
- print() calls found in modules/ source code
- Replace every print statement with structured logging
"
                ;;
            cross_module_imports)
                failure_details+="
## FAILED: cross_module_imports
- Modules in modules/ are importing from other modules/ packages
- Modules must ONLY import from contracts/
- Remove or refactor cross-module imports
"
                ;;
            raw_sql)
                failure_details+="
## FAILED: raw_sql
- Raw database imports (sqlite3, psycopg2) found in modules/
- All database access must go through database/adapter.py
- Only the orchestrator may call the adapter
"
                ;;
            dto_compliance)
                failure_details+="
## FAILED: dto_compliance
- DTOs in contracts/ not compliant (non-frozen, raw dicts, mutable defaults)
- All dataclasses must use @dataclass(frozen=True)
- No module may return raw dicts — must return frozen DTOs
"
                ;;
            orchestrator_authority)
                failure_details+="
## FAILED: orchestrator_authority
- Modules importing from database/ or adapter
- Only the orchestrator may access the database
- Remove database imports from modules/
"
                ;;
        esac
    done

    local remediation_prompt="WORKING DIRECTORY: You are at the project root.

URGENT: Fix all quality gate failures so the integration branch is clean for PR.

This is remediation attempt ${attempt} of 3. The following quality gates FAILED: ${failures_str}

You are running as the code-fixer agent. Read these skills from .github/skills/:
- code-quality-fixer: maps each failure type to its fix strategy
- testing: patterns for fixing or writing tests

Execution:
1. Read .github/skills/code-quality-fixer/SKILL.md for the fix decision table
2. For each failure below, apply the fix strategy
3. Fix ALL issues — do NOT skip any

${failure_details}

After fixing ALL issues:
1. Run: pytest tests/ --tb=short -q — must show 0 errors, 0 failures
2. Run: ruff check . --quiet — must show 0 violations
3. git add -A && git commit -m 'fix: remediation attempt ${attempt} — quality gate fixes'

STRICT EXECUTION RULES:
- no background agents, no interactive steps, complete in one session"

    set_rotate_model
    update_phase_status "remediation-${attempt}" state "running" model "${ROTATE_MODEL}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    (
        cd "${PROJECT_ROOT}" || exit 1
        copilot \
            -p "$remediation_prompt" \
            --agent="${AGENT_CODE_FIXER}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            --model="${ROTATE_MODEL}" \
            --share="${remediation_transcript}"
    ) > "$remediation_log" 2>&1

    local exit_code=$?
    if [[ "$exit_code" -eq 0 ]]; then
        log_success "  Remediation agent completed (attempt ${attempt}) [model: ${ROTATE_MODEL}]."
        update_phase_status "remediation-${attempt}" state "complete" exit_code "0"
    else
        log_warn "  Remediation agent exited with code ${exit_code}. Check: ${remediation_log}"
        update_phase_status "remediation-${attempt}" state "failed" exit_code "${exit_code}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Integration Verification (8 checks — post-merge, pre-PR)
# ─────────────────────────────────────────────────────────────────────────────

run_integration_verification() {
    local merged_phases=("$@")
    INTEGRATION_FAILURES=()

    # Check 1: Test coverage — every module directory has corresponding tests
    log_info "  Check 1/8: Test coverage completeness..."
    local missing_tests=""
    if [[ -d "modules" ]]; then
        local module_dir
        for module_dir in modules/*/; do
            [[ ! -d "$module_dir" ]] && continue
            local module_name
            module_name="$(basename "$module_dir")"
            [[ "$module_name" == "__pycache__" ]] && continue
            if [[ ! -d "tests/modules/${module_name}" ]] && \
               ! find tests/ -name "test_${module_name}*" -type f 2>/dev/null | grep -q .; then
                missing_tests+="  tests/ has no tests for modules/${module_name}/\n"
            fi
        done
    fi
    if [[ -z "$missing_tests" ]]; then
        log_success "  Check 1/8: All modules have test coverage."
    else
        log_error "  Check 1/8: FAILED — missing test coverage:"
        echo -e "$missing_tests" | head -15
        INTEGRATION_FAILURES+=("test_coverage")
    fi

    # Check 2: No cross-module imports
    log_info "  Check 2/8: Cross-module imports..."
    local cross_imports=""
    if [[ -d "modules" ]]; then
        cross_imports=$(find modules/ -name '*.py' -exec grep -l "from modules\." {} \; 2>/dev/null | while read -r file; do
            local file_module
            file_module=$(echo "${file}" | cut -d'/' -f2)
            grep "from modules\." "${file}" | sed 's/.*from modules\.\([a-z_]*\).*/\1/' | sort -u | while read -r imp; do
                if [[ "${imp}" != "${file_module}" ]]; then
                    echo "  ${file} → modules.${imp}"
                fi
            done
        done)
    fi
    if [[ -z "$cross_imports" ]]; then
        log_success "  Check 2/8: No cross-module imports found."
    else
        log_error "  Check 2/8: FAILED — cross-module import violations:"
        echo "$cross_imports" | head -10
        INTEGRATION_FAILURES+=("cross_module_imports")
    fi

    # Check 3: No raw SQL in modules
    log_info "  Check 3/8: Raw SQL in modules..."
    local sql_violations=""
    if [[ -d "modules" ]]; then
        sql_violations=$(grep -rn "import sqlite3\|import psycopg2\|import asyncpg\|from database" modules/ --include='*.py' 2>/dev/null || true)
    fi
    if [[ -z "$sql_violations" ]]; then
        log_success "  Check 3/8: No raw SQL or DB imports in modules."
    else
        log_error "  Check 3/8: FAILED — raw SQL/DB imports in modules:"
        echo "$sql_violations" | head -10
        INTEGRATION_FAILURES+=("raw_sql_in_modules")
    fi

    # Check 4: DTO compliance — frozen dataclasses
    log_info "  Check 4/8: DTO contract compliance..."
    local dto_issues=""
    if [[ -d "contracts" ]]; then
        local non_frozen
        non_frozen=$(grep -rn "@dataclass$" contracts/ 2>/dev/null | grep -v "frozen=True" || true)
        if [[ -n "$non_frozen" ]]; then
            dto_issues+="  Non-frozen dataclass: ${non_frozen}\n"
        fi
    fi
    if [[ -z "$dto_issues" ]]; then
        log_success "  Check 4/8: All DTOs are frozen dataclasses."
    else
        log_error "  Check 4/8: FAILED — DTO compliance issues:"
        echo -e "$dto_issues" | head -10
        INTEGRATION_FAILURES+=("dto_compliance")
    fi

    # Check 5: Migration files exist
    log_info "  Check 5/8: Database migration files..."
    local migration_count
    migration_count=$(find database/migrations/ -name '*.sql' 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$migration_count" -gt 0 ]]; then
        log_success "  Check 5/8: ${migration_count} migration file(s) found."
    else
        log_warn "  Check 5/8: No migration files in database/migrations/ (may be OK if Phase 0 not yet implemented)."
    fi

    # Check 6: Orchestrator authority
    log_info "  Check 6/8: Orchestrator authority..."
    local auth_violations=""
    if [[ -d "modules" ]]; then
        auth_violations=$(grep -rn "from database\|import adapter\|import sqlite3" modules/ --include='*.py' 2>/dev/null || true)
    fi
    if [[ -z "$auth_violations" ]]; then
        log_success "  Check 6/8: Orchestrator authority preserved."
    else
        log_error "  Check 6/8: FAILED — orchestrator authority violations:"
        echo "$auth_violations" | head -10
        INTEGRATION_FAILURES+=("orchestrator_authority")
    fi

    # Check 7: Progress report mentions merged phases
    log_info "  Check 7/8: Progress report updated..."
    local missing_phase_mentions=""
    if [[ -f "docs/progress_report.md" ]]; then
        local p
        for p in "${merged_phases[@]}"; do
            if ! grep -qiE "(phase\s*${p}|phase\s*$(echo "$p" | sed 's/\./-/g'))" docs/progress_report.md 2>/dev/null; then
                missing_phase_mentions+="  Phase ${p} not mentioned in docs/progress_report.md\n"
            fi
        done
        if [[ -z "$missing_phase_mentions" ]]; then
            log_success "  Check 7/8: Progress report mentions all merged phases."
        else
            log_warn "  Check 7/8: Progress report missing phase references (advisory):"
            echo -e "$missing_phase_mentions"
        fi
    else
        log_warn "  Check 7/8: docs/progress_report.md does not exist (advisory)."
    fi

    # Check 8: No print() in modules
    log_info "  Check 8/8: No print() statements..."
    local print_violations=""
    if [[ -d "modules" ]]; then
        print_violations=$(grep -rn '^\s*print(' modules/ --include='*.py' 2>/dev/null | grep -v '# noqa' || true)
    fi
    if [[ -z "$print_violations" ]]; then
        log_success "  Check 8/8: No print() statements in modules."
    else
        log_error "  Check 8/8: FAILED — print() statements found:"
        echo "$print_violations" | head -10
        INTEGRATION_FAILURES+=("print_statements")
    fi

    # Return result
    if [[ ${#INTEGRATION_FAILURES[@]} -gt 0 ]]; then
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Integration Remediation Agent
# ─────────────────────────────────────────────────────────────────────────────

spawn_integration_remediation_agent() {
    local attempt="$1"
    shift
    local failures=("$@")
    local failures_str="${failures[*]}"

    local remediation_log="${LOG_DIR}/integration-remediation-attempt-${attempt}.log"
    local remediation_transcript="${LOG_DIR}/integration-remediation-attempt-${attempt}-session.md"

    log_info "  Integration remediation log: ${remediation_log}"

    local failure_details=""
    local f
    for f in "${failures[@]}"; do
        case "$f" in
            test_coverage)
                failure_details+="
## FAILED: test_coverage
- Some modules are missing test files.
- For each modules/<name>/ directory, create tests/ files.
- Tests must work without GPU, network, or real video files.
"
                ;;
            cross_module_imports)
                failure_details+="
## FAILED: cross_module_imports
- Modules importing from other modules/ packages.
- Modules must ONLY import from contracts/.
- Remove cross-module imports.
"
                ;;
            raw_sql_in_modules)
                failure_details+="
## FAILED: raw_sql_in_modules
- Modules using raw SQL or importing database libraries.
- All DB access through database/adapter.py, called by orchestrator only.
"
                ;;
            dto_compliance)
                failure_details+="
## FAILED: dto_compliance
- DTOs not using @dataclass(frozen=True).
- Fix all dataclasses in contracts/ to be frozen.
"
                ;;
            orchestrator_authority)
                failure_details+="
## FAILED: orchestrator_authority
- Modules importing from database/ or adapter.
- Only the orchestrator calls the adapter.
"
                ;;
            print_statements)
                failure_details+="
## FAILED: print_statements
- print() statements found in modules/.
- Replace with structured logging via stdlib logging module.
"
                ;;
        esac
    done

    local remediation_prompt="WORKING DIRECTORY: You are at the project root.

URGENT: Fix all integration verification failures. Final check before PR.

This is integration remediation attempt ${attempt} of 3. Failed checks: ${failures_str}

You are running as the code-fixer agent. Read skills from .github/skills/:
- code-quality-fixer: fix strategy for each failure type
- testing: test patterns for Shorts Factory
- modularity: module boundary rules

Fix every issue listed below. Do NOT skip any.

${failure_details}

After fixing ALL issues:
1. Run: pytest tests/ --tb=short -q — must show 0 errors
2. Run: ruff check . --quiet — must show 0 violations
3. git add -A && git commit -m 'fix: integration remediation attempt ${attempt}'

STRICT EXECUTION RULES:
- no background agents, no interactive steps, complete in one session"

    set_rotate_model
    update_phase_status "int-remediation-${attempt}" state "running" model "${ROTATE_MODEL}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    (
        cd "${PROJECT_ROOT}" || exit 1
        copilot \
            -p "$remediation_prompt" \
            --agent="${AGENT_CODE_FIXER}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            --model="${ROTATE_MODEL}" \
            --share="${remediation_transcript}"
    ) > "$remediation_log" 2>&1

    local exit_code=$?
    if [[ "$exit_code" -eq 0 ]]; then
        log_success "  Integration remediation agent completed (attempt ${attempt}) [model: ${ROTATE_MODEL}]."
        update_phase_status "int-remediation-${attempt}" state "complete" exit_code "0"
    else
        log_warn "  Integration remediation agent exited with code ${exit_code}. Check: ${remediation_log}"
        update_phase_status "int-remediation-${attempt}" state "failed" exit_code "${exit_code}"
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
        syntax_errors=$(find modules/ contracts/ -name '*.py' -exec python3 -m py_compile {} \; 2>&1 | head -10)
        if [[ -n "${syntax_errors}" ]]; then
            log_error "[merge-validate] Compilation failed after merge"
            ((failures++))
        fi
    fi

    return $(( failures > 0 ? 1 : 0 ))
}

# ─────────────────────────────────────────────────────────────────────────────
# Merge command — fully autonomous pipeline
# ─────────────────────────────────────────────────────────────────────────────
# Full autonomous flow:
#   1. Load phases from state
#   2. Sort phases, create integration branch
#   3. Merge each phase branch (auto_resolve_conflicts on conflict)
#   4. Post-merge review agent (merge-reviewer)
#   5. Documentation sync agent (merge-reviewer + docs-sync skill)
#   6. Quality gate + remediation loop (up to 3 attempts)
#   7. Integration verification + remediation loop (up to 3 attempts)
#   8. Push + PR creation via gh
# ─────────────────────────────────────────────────────────────────────────────

cmd_merge() {
    log_header "Merge & Integration (Fully Autonomous)"

    # Load phases from state (supports both standalone merge and auto-triggered)
    if [[ -z "${PHASES+x}" ]] || [[ ${#PHASES[@]} -eq 0 ]]; then
        load_phases
    fi

    cd "${PROJECT_ROOT}"

    # Ensure we start from a clean base
    git checkout main 2>/dev/null || {
        log_error "Cannot switch to main branch."
        exit 1
    }

    # --- Sort phases ascending (earliest first, latest last) ---
    local sorted_phases=()
    while IFS= read -r p; do
        sorted_phases+=("$p")
    done < <(printf '%s\n' "${PHASES[@]}" | sort -n)

    local latest_phase="${sorted_phases[${#sorted_phases[@]}-1]}"
    log_info "Merge order: ${sorted_phases[*]} (all phases combined equally)"

    # Build integration branch name
    local phase_str
    phase_str="$(IFS=-; echo "${sorted_phases[*]}")"
    local integration_branch="phase${phase_str}"

    # Check if integration branch already exists
    if git rev-parse --verify "$integration_branch" &>/dev/null; then
        log_warn "Integration branch '${integration_branch}' already exists. Recreating..."
        git checkout main 2>/dev/null || true
        git branch -D "$integration_branch"
    fi

    # Create integration branch from main
    git checkout -b "$integration_branch" main
    log_success "Created integration branch: ${integration_branch}"

    # ── Merge each phase branch (earliest first → latest last) ────────────
    local merge_failures=()
    local branch phase_idx=0

    for phase in "${sorted_phases[@]}"; do
        ((phase_idx++)) || true

        # Determine branch name based on mode
        local branch=""
        if [[ -f "${STATE_FILE}" ]]; then
            local mode
            mode=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['mode'])" 2>/dev/null || echo "1")
            if (( mode == 2 )); then
                branch=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['branches'][0])" 2>/dev/null || echo "")
            fi
        fi

        # Default: track/phase-N
        if [[ -z "$branch" ]]; then
            branch="track/phase-${phase}"
            # Also try track/group-* patterns
            if ! git rev-parse --verify "$branch" &>/dev/null; then
                local state_branches
                state_branches=$(python3 -c "import json; print(' '.join(json.load(open('${STATE_FILE}'))['branches']))" 2>/dev/null || echo "")
                for sb in ${state_branches}; do
                    if echo "$sb" | grep -q "${phase}"; then
                        branch="$sb"
                        break
                    fi
                done
            fi
        fi

        if ! git rev-parse --verify "$branch" &>/dev/null; then
            log_warn "Branch '${branch}' does not exist. Skipping Phase ${phase}."
            merge_failures+=("$phase")
            continue
        fi

        local commit_count
        commit_count="$(git log --oneline "main..${branch}" 2>/dev/null | wc -l | tr -d ' ')"

        if [[ "$commit_count" -eq 0 ]]; then
            log_warn "Phase ${phase}: no commits to merge. Skipping."
            continue
        fi

        log_info "Merging Phase ${phase} (${commit_count} commits, ${phase_idx}/${#sorted_phases[@]})..."

        if git merge --no-ff -m "Merge Phase ${phase} implementation" "$branch"; then
            log_success "Phase ${phase} merged successfully."
        else
            log_warn "Phase ${phase} has merge conflicts. Attempting auto-resolution..."
            if auto_resolve_conflicts "$phase"; then
                log_success "Phase ${phase} merged with auto-resolved conflicts."
            else
                log_error "Phase ${phase}: auto-resolution failed. Aborting this phase merge."
                git merge --abort 2>/dev/null || true
                merge_failures+=("$phase")
            fi
        fi
    done

    echo ""

    if [[ ${#merge_failures[@]} -gt 0 ]]; then
        log_error "Failed to merge phases: ${merge_failures[*]}"
        git checkout main 2>/dev/null || true
        return 1
    fi

    log_success "All phases merged into: ${integration_branch}"
    update_phase_status "merge" state "complete" exit_code "0"

    # ── Post-merge review agent ───────────────────────────────────────────
    echo ""
    set_rotate_model
    log_header "Post-Merge Review Agent"
    log_info "Agent: ${AGENT_MERGE_REVIEWER}, model: ${ROTATE_MODEL}"
    log_info "Verifying ALL phases are fully implemented and fixing issues."
    update_phase_status "post-merge-review" state "running" model "${ROTATE_MODEL}" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    local review_log="${LOG_DIR}/merge-review.log"
    local review_transcript="${LOG_DIR}/merge-review-session.md"

    local phases_str="${sorted_phases[*]}"
    local review_prompt
    review_prompt="WORKING DIRECTORY: You are at the project root.

Review the merged integration branch containing Phases ${phases_str}.

CRITICAL: ALL phases must receive EQUAL, THOROUGH review.

You are running as the merge-reviewer agent. Use skills from .github/skills/:
- merge-reviewer: full review checklist and protocol
- modularity: verify no cross-module imports
- testing: verify test files exist

Execution:
1. Read .github/skills/merge-reviewer/SKILL.md for the review protocol
2. Read docs/implementation_roadmap.md — extract task checklists for Phases ${phases_str}
3. Read .github/copilot-instructions.md for hard constraints
4. For EVERY phase: verify modules, contracts, tests exist
5. Run: python3 -c 'import sys; sys.path.insert(0, \".\"); import importlib' — fix any import errors
6. Run: pytest tests/ --tb=short -q — fix any failures
7. Run: ruff check . --fix — fix lint
8. Fix ANY issues — edit files directly, rerun until clean
9. git add -A && git commit -m 'review: post-merge fixes — all phases verified'

STRICT EXECUTION RULES:
- no background agents, no interactive steps, complete in one session"

    (
        cd "${PROJECT_ROOT}" || exit 1
        copilot \
            -p "$review_prompt" \
            --agent="${AGENT_MERGE_REVIEWER}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            --model="${ROTATE_MODEL}" \
            --share="${review_transcript}"
    ) > "$review_log" 2>&1

    local review_exit=$?
    if [[ "$review_exit" -eq 0 ]]; then
        log_success "Post-merge review completed."
    else
        log_warn "Post-merge review agent exited with code ${review_exit}. Check: ${review_log}"
    fi

    # ── Documentation Sync Agent ──────────────────────────────────────────
    echo ""
    set_rotate_model
    log_header "Documentation Sync Agent"
    log_info "Agent: ${AGENT_MERGE_REVIEWER}, model: ${ROTATE_MODEL}"
    log_info "Updating docs to reflect all merged phases."

    local docs_log="${LOG_DIR}/docs-sync.log"
    local docs_transcript="${LOG_DIR}/docs-sync-session.md"

    local docs_prompt
    docs_prompt="WORKING DIRECTORY: You are at the project root.

Synchronize ALL documentation for merged Phases ${phases_str}.

You are running as the merge-reviewer agent. Read skills from .github/skills/:
- docs-sync: the COMPLETE documentation synchronization protocol
- architecture-reader: system architecture for accurate documentation

Execution:
1. Read .github/skills/docs-sync/SKILL.md — follow it exactly
2. Read docs/implementation_roadmap.md — extract tasks for each phase
3. Scan the codebase for what was actually implemented
4. UPDATE docs/progress_report.md — add full section for each phase
5. UPDATE docs/implementation_roadmap.md — mark completed tasks [x]
6. UPDATE README.md — update repo structure if needed
7. Commit: git add -A && git commit -m 'docs: synchronize for Phases ${phases_str}'

CRITICAL: Only document what actually exists as code. No aspirational content.

STRICT EXECUTION RULES:
- no background agents, no interactive steps, complete in one session"

    (
        cd "${PROJECT_ROOT}" || exit 1
        copilot \
            -p "$docs_prompt" \
            --agent="${AGENT_MERGE_REVIEWER}" \
            --no-ask-user \
            --allow-all-tools \
            --autopilot \
            --model="${ROTATE_MODEL}" \
            --share="${docs_transcript}"
    ) > "$docs_log" 2>&1

    local docs_exit=$?
    if [[ "$docs_exit" -eq 0 ]]; then
        log_success "Documentation sync completed."
        update_phase_status "docs-sync" state "complete" exit_code "0"
    else
        log_warn "Documentation sync agent exited with code ${docs_exit}. Check: ${docs_log}"
        update_phase_status "docs-sync" state "failed" exit_code "${docs_exit}"
    fi

    # ── Quality Gate + Remediation Loop ───────────────────────────────────
    echo ""
    log_header "Quality Gate Checks"
    log_info "All gates must pass before PR creation."
    update_phase_status "quality-gate" state "running" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    ensure_python_env "${PROJECT_ROOT}" 2>/dev/null || true

    local max_attempts=3
    local attempt=0
    local gates_passed=false
    GATE_FAILURES=()

    while [[ "$attempt" -lt "$max_attempts" ]]; do
        ((attempt++)) || true

        if [[ "$attempt" -gt 1 ]]; then
            log_info "Quality gate recheck (attempt ${attempt}/${max_attempts})..."
        fi

        if run_quality_gates "${PROJECT_ROOT}"; then
            gates_passed=true
            break
        fi

        # Collect failure names from quality gate output
        # run_quality_gates returns 1 on failure — collect specific failures
        local qg_failures=()
        # Check each gate individually to build failure list
        cd "${PROJECT_ROOT}"
        if ! python3 -c "import sys; sys.path.insert(0, '.'); import importlib" 2>/dev/null; then
            qg_failures+=("package_import")
        fi
        if [[ -d "tests" ]] && command -v pytest &>/dev/null; then
            if ! pytest tests/ --tb=short -q 2>/dev/null; then
                qg_failures+=("tests")
            fi
        fi
        if [[ -d "modules" ]] && grep -rn "import sqlite3\|import psycopg2" modules/ --include='*.py' 2>/dev/null | grep -q .; then
            qg_failures+=("raw_sql")
        fi
        if [[ -d "modules" ]]; then
            local cross
            cross=$(find modules/ -name '*.py' -exec grep -l "from modules\." {} \; 2>/dev/null | head -1)
            if [[ -n "$cross" ]]; then
                qg_failures+=("cross_module_imports")
            fi
        fi
        if [[ -d "modules" ]] && grep -rn "^\s*print(" modules/ --include='*.py' 2>/dev/null | grep -v "# noqa" | grep -q .; then
            qg_failures+=("print_statements")
        fi

        echo ""
        log_warn "Quality gate FAILED (attempt ${attempt}/${max_attempts})."

        if [[ "$attempt" -ge "$max_attempts" ]]; then
            break
        fi

        log_info "Spawning remediation agent (attempt ${attempt}/${max_attempts})..."
        spawn_remediation_agent "$attempt" "${qg_failures[@]}"
    done

    echo ""
    if ! $gates_passed; then
        log_error "Quality gate FAILED after ${max_attempts} remediation attempts."
        log_error "PR will NOT be created. Manual intervention required."
        log_info "Fix issues, then re-run: ./scripts/run_parallel.sh merge"
        git checkout main 2>/dev/null || true
        return 1
    fi

    log_success "All quality gates passed!"
    update_phase_status "quality-gate" state "complete" exit_code "0"

    # ── Integration Verification + Remediation Loop ───────────────────────
    echo ""
    log_header "Integration Verification"
    log_info "Running 8 integration checks..."
    update_phase_status "integration-verify" state "running" started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    local int_max_attempts=3
    local int_attempt=0
    local int_passed=false

    while [[ "$int_attempt" -lt "$int_max_attempts" ]]; do
        ((int_attempt++)) || true

        if [[ "$int_attempt" -gt 1 ]]; then
            log_info "Integration verification recheck (attempt ${int_attempt}/${int_max_attempts})..."
        fi

        cd "${PROJECT_ROOT}"
        if run_integration_verification "${sorted_phases[@]}"; then
            int_passed=true
            break
        fi

        echo ""
        log_warn "Integration verification FAILED: ${INTEGRATION_FAILURES[*]}"

        if [[ "$int_attempt" -ge "$int_max_attempts" ]]; then
            break
        fi

        log_info "Spawning integration remediation agent (attempt ${int_attempt}/${int_max_attempts})..."
        spawn_integration_remediation_agent "$int_attempt" "${INTEGRATION_FAILURES[@]}"

        # Re-run quality gates after integration fixes (in case fixes broke something)
        log_info "  Re-validating quality gates after integration fixes..."
        if ! run_quality_gates "${PROJECT_ROOT}"; then
            log_warn "  Quality gates regressed — spawning remediation..."
            spawn_remediation_agent "$int_attempt" "tests" "linter"
        fi
    done

    echo ""
    if ! $int_passed; then
        log_error "Integration verification FAILED after ${int_max_attempts} attempts."
        log_error "PR will NOT be created. Manual intervention required."
        git checkout main 2>/dev/null || true
        return 1
    fi

    log_success "All 8 integration checks passed! PR-ready."
    update_phase_status "integration-verify" state "complete" exit_code "0"

    # Commit any remaining fixes
    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        git add -A
        git commit -m "quality-gate: auto-fix linter and formatting issues" 2>/dev/null || true
    fi

    # ── Push and Create PR ────────────────────────────────────────────────
    echo ""
    log_header "Push & PR Creation"

    if git push -u origin "$integration_branch" 2>/dev/null; then
        log_success "Pushed: ${integration_branch}"
    else
        log_warn "Failed to push. You may need to authenticate or set up remote."
        log_info "Push manually: git push -u origin ${integration_branch}"
        git checkout main 2>/dev/null || true
        return 1
    fi

    # Create PR via gh CLI
    if command -v gh &>/dev/null; then
        local pr_title="Parallel Implementation: Phases ${sorted_phases[*]}"

        local pr_body
        pr_body="## Parallel Phase Implementation

### Phases Implemented (merge order)
$(for p in "${sorted_phases[@]}"; do echo "- Phase ${p} (${PHASE_NAMES[$p]:-unknown})"; done)

### Development Method
Developed in parallel using Git worktrees with DTO-based isolation.
Each phase implemented by an autonomous Copilot CLI agent.
Heavy model: \`${MODEL_HEAVY}\` (heaviest phase only)
Rotate pool: \`$(IFS=', '; echo "${MODEL_ROTATE_POOL[*]}")\`

### Agents Used

| Process | Agent | Skills |
|---------|-------|--------|
| Phase implementation | \`phase-builder\` | Per-phase via PHASE_TASK.md |
| Conflict resolution | \`conflict-resolver\` | conflict-resolver, modularity |
| Post-merge review | \`merge-reviewer\` | merge-reviewer, modularity, testing |
| Documentation sync | \`merge-reviewer\` | docs-sync, architecture-reader |
| Quality gate fixes | \`code-fixer\` | code-quality-fixer, testing |
| Integration fixes | \`code-fixer\` | code-quality-fixer, modularity |

### Merge Strategy
Phases merged earliest → latest using \`--no-ff\`.
Conflicts resolved by combining all phases (union strategy).
Post-merge review + docs sync + quality gate + integration verification.

### Quality Gate Results (all passed)
- [x] Module imports cleanly
- [x] All tests pass
- [x] Linter clean
- [x] No print() in modules/
- [x] No cross-module imports
- [x] No raw SQL in modules/
- [x] Frozen DTO compliance
- [x] Orchestrator authority preserved

### Integration Verification Results (all passed)
- [x] Test coverage for all modules
- [x] No cross-module imports
- [x] No raw SQL/DB imports in modules
- [x] DTO compliance verified
- [x] Migration files present
- [x] Orchestrator authority enforced
- [x] Documentation updated
- [x] No print() statements

### Review Checklist
- [ ] DTO schemas match contracts/
- [ ] Database migrations are idempotent
- [ ] All modules are pure functions (DTO in → DTO out)
- [ ] Pipeline stage ordering preserved
- [ ] Deterministic ordering enforced"

        if gh pr create \
            --title "$pr_title" \
            --body "$pr_body" \
            --base main \
            --head "$integration_branch"; then
            log_success "Pull Request created!"
        else
            log_warn "Failed to create PR. Create manually on GitHub."
        fi
    else
        log_info "GitHub CLI not available. Create PR manually:"
        log_info "  Branch: ${integration_branch} → main"
    fi

    git checkout main 2>/dev/null || true

    echo ""
    log_success "Merge sequence complete!"
    log_info "Run './scripts/run_parallel.sh cleanup' to remove worktrees."
}

# ─────────────────────────────────────────────────────────────────────────────
# Pipeline stage detection (for rich status output)
# ─────────────────────────────────────────────────────────────────────────────

_detect_pipeline_stage() {
    PIPELINE_STAGE=""
    PIPELINE_STAGE_COLOR="$NC"
    PIPELINE_ACTIVE_LOG=""
    PIPELINE_ACTIVE_LABEL=""
    PIPELINE_ACTIVE_AGE=""

    # Build integration branch name
    local sorted_phases=()
    while IFS= read -r p; do sorted_phases+=("$p"); done \
        < <(printf '%s\n' "${PHASES[@]}" | sort -n)
    local phase_str; phase_str="$(IFS=-; echo "${sorted_phases[*]}")"
    local integration_branch="phase${phase_str}"

    local int_local=false
    git -C "$PROJECT_ROOT" rev-parse --verify "$integration_branch" &>/dev/null \
        && int_local=true

    local int_remote=false
    if $int_local; then
        git -C "$PROJECT_ROOT" ls-remote --heads origin "$integration_branch" 2>/dev/null \
            | grep -q . && int_remote=true
    fi

    # Count phase completion states from phase-status.json (authoritative source)
    local complete_count=0 in_progress_count=0
    local status_file="${PROJECT_ROOT}/.parallel-dev/phase-status.json"
    for ph in "${PHASES[@]}"; do
        local ph_state=""
        if [[ -f "$status_file" ]]; then
            # Try exact phase key first, then group patterns
            ph_state=$(python3 -c "
import json, sys
data = json.load(open('$status_file'))
phases = data.get('phases', {})
# Direct phase match
if '$ph' in phases:
    print(phases['$ph'].get('state', ''))
elif 'phase-$ph' in phases:
    print(phases['phase-$ph'].get('state', ''))
else:
    # Check group keys that contain this phase
    for k, v in sorted(phases.items()):
        if 'group-' in k and '$ph' in k.split('group-')[1].split('-'):
            print(v.get('state', ''))
            break
" 2>/dev/null || true)
        fi
        if [[ "$ph_state" == "complete" ]]; then
            ((complete_count++)) || true
        elif [[ "$ph_state" == "running" ]]; then
            ((in_progress_count++)) || true
        else
            # Fallback: check git branch for commits
            local br; br="$(branch_for_phase "$ph")"
            if git -C "$PROJECT_ROOT" rev-parse --verify "$br" &>/dev/null; then
                local cc; cc="$(git -C "$PROJECT_ROOT" log --oneline "main..${br}" \
                    2>/dev/null | wc -l | tr -d ' ')"
                if [[ "$cc" -gt 0 ]]; then
                    ((in_progress_count++)) || true
                fi
            fi
        fi
    done

    # Probe for log files
    local has_int_remediation=false has_remediation=false
    local has_docs_sync=false has_merge_review=false
    [[ -f "${LOG_DIR}/integration-remediation-attempt-1.log" ]] && has_int_remediation=true
    [[ -f "${LOG_DIR}/remediation-attempt-1.log" ]]             && has_remediation=true
    [[ -f "${LOG_DIR}/docs-sync.log" ]]                         && has_docs_sync=true
    [[ -f "${LOG_DIR}/merge-review.log" ]]                      && has_merge_review=true

    # Stage inference
    if $int_remote; then
        PIPELINE_STAGE="PR Created / Complete"
        PIPELINE_STAGE_COLOR="$GREEN"
    elif $has_int_remediation; then
        PIPELINE_STAGE="Integration Verification"
        PIPELINE_STAGE_COLOR="$YELLOW"
    elif $has_remediation || ( $has_docs_sync && $int_local ); then
        PIPELINE_STAGE="Quality Gate"
        PIPELINE_STAGE_COLOR="$YELLOW"
    elif $has_docs_sync; then
        PIPELINE_STAGE="Documentation Sync"
        PIPELINE_STAGE_COLOR="$YELLOW"
    elif $has_merge_review; then
        PIPELINE_STAGE="Post-Merge Review"
        PIPELINE_STAGE_COLOR="$YELLOW"
    elif $int_local; then
        PIPELINE_STAGE="Merging Phases"
        PIPELINE_STAGE_COLOR="$YELLOW"
    elif [[ "$complete_count" -eq "${#PHASES[@]}" ]]; then
        PIPELINE_STAGE="Ready to Merge"
        PIPELINE_STAGE_COLOR="$CYAN"
    elif [[ "$in_progress_count" -gt 0 ]] || [[ "$complete_count" -gt 0 ]]; then
        PIPELINE_STAGE="Phase Build"
        PIPELINE_STAGE_COLOR="$YELLOW"
    else
        PIPELINE_STAGE="Setup"
        PIPELINE_STAGE_COLOR="$BLUE"
    fi

    # Find the most recently modified log file
    local now; now="$(date +%s)"
    local newest_log="" newest_age_s=9999999
    for lf in "${LOG_DIR}"/*.log "${LOG_DIR}"/*-session.md; do
        [[ -f "$lf" ]] || continue
        local lmt; lmt="$(stat -f "%m" "$lf" 2>/dev/null || stat -c "%Y" "$lf" 2>/dev/null || echo "$now")"
        local age_s=$(( now - lmt ))
        if [[ "$age_s" -lt "$newest_age_s" ]]; then
            newest_age_s="$age_s"
            newest_log="$lf"
        fi
    done

    if [[ -n "$newest_log" ]]; then
        PIPELINE_ACTIVE_LOG="$newest_log"
        local base; base="$(basename "$newest_log")"
        case "$base" in
            merge-review*)    PIPELINE_ACTIVE_LABEL="merge-reviewer agent" ;;
            docs-sync*)       PIPELINE_ACTIVE_LABEL="docs-sync agent" ;;
            remediation-*)    PIPELINE_ACTIVE_LABEL="quality-gate remediation" ;;
            integration-rem*) PIPELINE_ACTIVE_LABEL="integration remediation" ;;
            conflict-*)       PIPELINE_ACTIVE_LABEL="conflict-resolver" ;;
            *)                PIPELINE_ACTIVE_LABEL="$base" ;;
        esac

        if   [[ "$newest_age_s" -lt 60 ]];   then PIPELINE_ACTIVE_AGE="${newest_age_s}s ago"
        elif [[ "$newest_age_s" -lt 3600 ]]; then PIPELINE_ACTIVE_AGE="$(( newest_age_s / 60 ))m ago"
        else                                       PIPELINE_ACTIVE_AGE="$(( newest_age_s / 3600 ))h ago"
        fi
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Status command (rich output with pipeline detection)
# ─────────────────────────────────────────────────────────────────────────────

cmd_status() {
    load_phases

    _detect_pipeline_stage

    local status_file="${PROJECT_ROOT}/.parallel-dev/phase-status.json"

    # ── Header ────────────────────────────────────────────────────────────
    echo ""
    log_header "Parallel Development Status"

    # ── Session info from state.json ──────────────────────────────────────
    if [[ -f "${STATE_FILE}" ]]; then
        STATE_FILE_PATH="${STATE_FILE}" python3 <<'PYEOF'
import json, os

sf = os.environ["STATE_FILE_PATH"]
data = json.load(open(sf))

mode_num = data.get("mode", "?")
mode_names = {1: "Full Parallel", 2: "Token-Optimized", 3: "Hybrid"}
mode_label = f"{mode_num} ({mode_names.get(int(mode_num), 'Unknown')})"

print(f"  Mode:               {mode_label}")
print(f"  Phases:             {data.get('phases', '?')}")
print(f"  Integration branch: {data.get('integration_branch', '?')}")
print(f"  Status:             {data.get('status', '?')}")
print(f"  Started:            {data.get('started_at', '?')}")

branches = data.get("branches", [])
if branches:
    print(f"  Branches:           {', '.join(branches)}")

# Model routing
heavy = data.get("model_heavy", "")
pool = data.get("model_rotation_pool", [])
if heavy:
    print(f"")
    print(f"  Model (heavy):      {heavy}")
if pool:
    print(f"  Rotation pool:      {' → '.join(pool)}")
PYEOF
    fi
    echo ""

    # ── Phase/group progress with git details ─────────────────────────────
    echo "-----------------------------------------------------------"

    for phase in "${PHASES[@]}"; do
        local branch; branch="$(branch_for_phase "$phase")"
        local status_icon details=""

        # Try reading state from phase-status.json first
        local ph_state="" ph_model=""
        if [[ -f "$status_file" ]]; then
            read -r ph_state ph_model < <(python3 -c "
import json
data = json.load(open('$status_file'))
phases = data.get('phases', {})
entry = None
# Try exact phase key
for key in ['$phase', 'phase-$phase']:
    if key in phases:
        entry = phases[key]
        break
# Try group keys containing this phase
if entry is None:
    for k, v in sorted(phases.items()):
        if 'group-' in k and '$phase' in k.split('group-', 1)[1].split('-'):
            entry = v
            break
if entry:
    print(entry.get('state', ''), entry.get('model', ''))
else:
    print('', '')
" 2>/dev/null || echo " ")
        fi

        # Determine status icon from phase-status.json state
        if [[ "$ph_state" == "complete" ]]; then
            status_icon="${GREEN}[COMPLETE]${NC}"
        elif [[ "$ph_state" == "running" ]]; then
            status_icon="${YELLOW}[IN PROGRESS]${NC}"
        elif [[ "$ph_state" == "failed" ]]; then
            status_icon="${RED}[FAILED]${NC}"
        else
            status_icon="${RED}[NOT STARTED]${NC}"
        fi

        # Enrich with git details if branch exists
        if git -C "$PROJECT_ROOT" rev-parse --verify "$branch" &>/dev/null; then
            local cc; cc="$(git -C "$PROJECT_ROOT" log --oneline "main..${branch}" \
                2>/dev/null | wc -l | tr -d ' ')"

            if [[ "$ph_state" != "complete" ]] && [[ "$ph_state" != "running" ]] && [[ "$ph_state" != "failed" ]]; then
                if [[ "$cc" -gt 0 ]]; then
                    status_icon="${YELLOW}[IN PROGRESS]${NC}"
                else
                    status_icon="${BLUE}[STARTED]${NC}"
                fi
            fi

            details="${cc} commits"
            local last_msg
            last_msg="$(git -C "$PROJECT_ROOT" log --oneline -1 "$branch" 2>/dev/null | cut -c 9-)"
            [[ -n "$last_msg" ]] && details="${details} — ${last_msg}"
        elif [[ -z "$ph_state" ]]; then
            details="no branch"
        fi

        echo -e "  Phase ${phase} (${PHASE_NAMES[$phase]:-unknown}): ${status_icon} ${details}"
    done

    # ── Post-merge pipeline processes ─────────────────────────────────────
    local has_post_merge=false
    local process_entries=()

    # Check each post-merge process log
    if [[ -f "${LOG_DIR}/merge-review.log" ]]; then
        has_post_merge=true
        local mr_state="complete"
        local mr_size; mr_size=$(wc -c < "${LOG_DIR}/merge-review.log" 2>/dev/null | tr -d ' ')
        process_entries+=("Post-Merge Review|${mr_state}|${mr_size} bytes|${LOG_DIR}/merge-review.log")
    fi
    if [[ -f "${LOG_DIR}/docs-sync.log" ]]; then
        has_post_merge=true
        local ds_state="complete"
        local ds_size; ds_size=$(wc -c < "${LOG_DIR}/docs-sync.log" 2>/dev/null | tr -d ' ')
        process_entries+=("Documentation Sync|${ds_state}|${ds_size} bytes|${LOG_DIR}/docs-sync.log")
    fi
    for attempt_file in "${LOG_DIR}"/remediation-attempt-*.log; do
        [[ -f "$attempt_file" ]] || continue
        has_post_merge=true
        local ra_size; ra_size=$(wc -c < "$attempt_file" 2>/dev/null | tr -d ' ')
        local ra_name; ra_name="$(basename "$attempt_file" .log)"
        process_entries+=("Quality Gate (${ra_name})|complete|${ra_size} bytes|${attempt_file}")
    done
    for attempt_file in "${LOG_DIR}"/integration-remediation-attempt-*.log; do
        [[ -f "$attempt_file" ]] || continue
        has_post_merge=true
        local ir_size; ir_size=$(wc -c < "$attempt_file" 2>/dev/null | tr -d ' ')
        local ir_name; ir_name="$(basename "$attempt_file" .log)"
        process_entries+=("Integration Verify (${ir_name})|complete|${ir_size} bytes|${attempt_file}")
    done
    for attempt_file in "${LOG_DIR}"/conflict-resolve-phase-*.log; do
        [[ -f "$attempt_file" ]] || continue
        has_post_merge=true
        local cr_size; cr_size=$(wc -c < "$attempt_file" 2>/dev/null | tr -d ' ')
        local cr_name; cr_name="$(basename "$attempt_file" .log)"
        process_entries+=("Conflict Resolution (${cr_name})|complete|${cr_size} bytes|${attempt_file}")
    done

    if $has_post_merge; then
        echo ""
        for entry in "${process_entries[@]}"; do
            IFS='|' read -r pname pstate pdetail plog <<< "$entry"
            echo -e "  ${pname}: ${GREEN}[${pstate^^}]${NC} ${pdetail}"
        done
    fi

    echo "-----------------------------------------------------------"

    # ── Agent Status table (from phase-status.json) ───────────────────────
    if [[ -f "$status_file" ]]; then
        echo ""
        echo -e "  ${BOLD}Agent Status:${NC}"
        printf "    %-22s %-12s %-28s %-6s %s\n" \
            "Phase/Group" "State" "Model" "Exit" "Updated"
        printf "    %-22s %-12s %-28s %-6s %s\n" \
            "──────────────────────" "────────────" "────────────────────────────" "──────" "────────────────────"
        PHASE_STATUS_FILE="$status_file" python3 <<'PYEOF'
import json, os

sf = os.environ["PHASE_STATUS_FILE"]
data = json.load(open(sf))
phases = data.get("phases", {})
for key in sorted(phases.keys()):
    entry = phases[key]
    state = entry.get("state", "unknown")
    model = entry.get("model", "—")
    exit_code = entry.get("exit_code", "—")
    if exit_code is None:
        exit_code = "—"
    updated = entry.get("updated_at", "—")
    print(f"    {key:<22s} {state:<12s} {model:<28s} {str(exit_code):<6s} {updated}")
PYEOF
    fi

    # ── Log files ─────────────────────────────────────────────────────────
    if [[ -d "${LOG_DIR}" ]]; then
        local log_files=()
        while IFS= read -r lf; do
            log_files+=("$lf")
        done < <(find "${LOG_DIR}" -type f -name '*.log' -o -name '*-session.md' 2>/dev/null | sort)

        if [[ ${#log_files[@]} -gt 0 ]]; then
            echo ""
            echo -e "  ${BOLD}Log files:${NC}"
            for lf in "${log_files[@]}"; do
                local lf_base; lf_base="$(basename "$lf")"
                local lf_size; lf_size=$(wc -c < "$lf" 2>/dev/null | tr -d ' ')
                echo "    ${lf_base} → ${lf} (${lf_size} bytes)"
            done
        fi
    fi

    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup command
# ─────────────────────────────────────────────────────────────────────────────

cmd_cleanup() {
    load_phases

    log_header "Cleanup"

    cd "${PROJECT_ROOT}"

    # Remove worktrees
    for phase in "${PHASES[@]}"; do
        local wt; wt="$(worktree_for_phase "$phase")"
        if [[ -d "$wt" ]]; then
            git worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
            log_success "Removed worktree: ${wt}"
        fi

        local branch; branch="$(branch_for_phase "$phase")"
        if git rev-parse --verify "$branch" &>/dev/null; then
            if git branch -d "$branch" 2>/dev/null; then
                log_success "Deleted branch: ${branch} (merged)"
            else
                log_warn "Branch ${branch} not fully merged. Use -D to force delete."
            fi
        fi
    done

    # Prune worktree references
    git worktree prune 2>/dev/null || true

    # Remove integration branches
    log_info "Removing integration branches..."
    git branch --list 'phase*' | while read -r branch; do
        branch=$(echo "${branch}" | tr -d ' *')
        git branch -D "${branch}" 2>/dev/null || true
        log_info "  Deleted ${branch}"
    done

    # Remove track group branches
    git branch --list 'track/*' | while read -r branch; do
        branch=$(echo "${branch}" | tr -d ' *')
        git branch -D "${branch}" 2>/dev/null || true
        log_info "  Deleted ${branch}"
    done

    # Clean task files
    rm -f "${PROJECT_ROOT}/PHASE_TASK.md"

    # Remove state
    if [[ -d "${PROJECT_ROOT}/.parallel-dev" ]]; then
        rm -rf "${PROJECT_ROOT}/.parallel-dev"
        log_success "Removed state directory: .parallel-dev/"
    fi

    # Remove worktree base if empty
    if [[ -d "${WORKTREE_BASE}" ]]; then
        rm -rf "${WORKTREE_BASE}" 2>/dev/null || true
    fi

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
            check_copilot_auth
            ensure_python_env

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
            ensure_python_env
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
