---
name: token-optimization
description: "Token optimization for Shorts Factory development agents. Use when designing agent prompts, planning context loading, or reducing redundant document reads. Provides strategies for progressive loading, skill-first approach, and context compression."
---

# Token Optimization Skill

## When to Use

- Designing or refining agent prompts
- Planning which documents to read for a task
- Reducing context window usage
- Deciding when to use subagents for isolated reads

## Problem

The Shorts Factory documentation set is ~25,000 tokens:

| Document                    | ~Tokens | Read Frequency    |
| --------------------------- | ------- | ----------------- |
| `architecture.md`           | 8,000   | Every phase       |
| `implementation_roadmap.md` | 10,000  | Every phase       |
| `dto_contracts.md`          | 5,000   | Module work       |
| `orchestrator_spec.md`      | 3,000   | Orchestrator work |
| `copilot-instructions.md`   | 1,500   | Always loaded     |

Naively reading all docs per agent invocation wastes 80%+ of tokens on irrelevant sections.

## Strategy 1: Skill-First Loading

```
BEFORE (wasteful):
  Agent reads architecture.md (8K) → extracts 200 tokens of relevant rules
  Agent reads dto_contracts.md (5K) → extracts 300 tokens of DTO definitions
  Total: 13,000 tokens consumed, 500 tokens useful

AFTER (efficient):
  Agent loads dto skill (500 tokens) → gets pre-digested DTO rules
  Agent loads determinism skill (400 tokens) → gets enforcement rules
  Total: 900 tokens consumed, 900 tokens useful
```

**Rule:** Load skills first. Only read raw docs if the skill doesn't contain the answer.

## Strategy 2: Progressive Disclosure

```
Level 1 — Skill Discovery (~100 tokens)
  Read skill name + description
  Decide relevance

Level 2 — Skill Body (~300–500 tokens)
  Read SKILL.md
  Get focused rules and patterns

Level 3 — Doc Section (~500–2000 tokens)
  Read specific doc section via reference
  Only when skill references "see docs/X.md Section Y"

Level 4 — Full Doc (~5000–10000 tokens)
  Read entire document
  ONLY when implementing a full phase from scratch
```

## Strategy 3: Targeted Doc Reads

```python
# ❌ WASTEFUL — reading entire doc
read_file("docs/implementation_roadmap.md", 1, 2000)  # Everything

# ✅ EFFICIENT — reading only Phase 3 section
grep_search("## Phase 3", includePattern="docs/implementation_roadmap.md")
# Then read only that section
read_file("docs/implementation_roadmap.md", start_line, end_line)
```

## Strategy 4: Subagent Isolation

Use the `Explore` subagent for research that doesn't need to stay in main context:

```
Main agent context: 50K tokens available
  ├── Skills loaded: 2K tokens
  ├── Implementation work: 40K tokens
  └── Remaining: 8K tokens

Instead of reading 5 docs (25K) in main context:
  → Delegate to Explore subagent
  → Subagent reads all docs, returns 500-token summary
  → Main agent keeps 49.5K tokens for implementation
```

## Strategy 5: Context Compression

When passing information between agents:

```
# ❌ VERBOSE (500 tokens)
"The ScoredScene DTO has the following fields: scene_id (string, format video_id_start_end),
video_id (string, 16 hex chars), start_time (int, milliseconds), end_time (int, milliseconds),
duration (float, seconds, range 3-20), keyword_score (float, range 0.0-1.0), ..."

# ✅ COMPRESSED (50 tokens)
"ScoredScene: all scores [0.0-1.0], composite = weighted avg, sorted by -score then +start_time.
See .github/skills/dto/SKILL.md for full registry."
```

## Rules for All Agents

1. **Never read full docs as first action** — Load relevant skills first
2. **Never read docs you don't need** — Orchestrator agent doesn't need `dto_contracts.md` in full
3. **Cache within session** — Don't re-read a skill you already loaded
4. **Use grep for targeted reads** — Search for the section header, read only that section
5. **Delegate exploration** — Use `Explore` subagent for multi-doc research
6. **Reference, don't repeat** — Say "per dto skill" instead of re-stating the rules

## Anti-Patterns

| Anti-Pattern                                | Token Cost          | Fix                                        |
| ------------------------------------------- | ------------------- | ------------------------------------------ |
| Read all 4 docs before every task           | +25K per invocation | Load 2–3 skills instead                    |
| Re-read same doc in same session            | +8K wasted          | Cache the first read                       |
| Copy DTO definitions into response          | +500 per DTO        | Reference the skill                        |
| Explain architecture before implementing    | +2K per explanation | Skip — skills encode it                    |
| Read implementation_roadmap for refactoring | +10K wasted         | Refactor agent only needs modularity skill |
