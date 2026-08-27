# Dispatching subagents

Read this when the selected set is larger than ~4 tools and you are the **main thread** of a
session. A single driver thread is correct below that, and correct at any size when this skill
itself is running as a subagent.

> **When this skill runs as a subagent** (dispatched by a parent agent or workflow), execute
> as a **single driver thread — do NOT dispatch sub-subagents.** All phases run inline.

## Why fan out at all

Two separate benefits, and only one of them is always available:

- **Context economy** — big payloads stay in the subagent's context, never main's. Always
  available.
- **Parallelism** — real only against a local `stdio` server. Against a hosted HTTP server the
  ≥ 2 s probe interval rules out fan-out, so subagents buy context economy alone and probing
  runs one agent at a time.

The parts-based probe infrastructure (`_atomic_write_text`, `<shapes>.parts/<tool>.json`,
`mcpgen merge`) supports concurrent writers, which is what makes this safe.

## The hard constraint

**Subagents cannot call `AskUserQuestion`.** That line divides main from subagent:

- **Main thread** — every interactive gate (the step-2d selection offer, the >20-variant cap,
  the base-model-vs-`Any` choice, discriminator resolution spanning batches) and every
  deterministic barrier (codegen, merge, regenerate).
- **Subagents** — everything data-heavy and non-interactive: recon discovery dumps, per-batch
  probe + shape-entry draft, optional verify.

## Phase assignment

| Phase | Executor | Why |
|---|---|---|
| 1 codegen stubs | inline | one command, barrier |
| 2 select + discriminator detect | **main** | deterministic barrier; owns 2d's gate when a user is present |
| Recon | **1 subagent** | isolates discovery dumps; returns compact id + enum catalog |
| Discriminator Pass 2 | **main** | few calls, decides the sweep's variant count |
| 3 probe + draft | **batched parallel subagents** (local `stdio`); one agent for hosted HTTP | context economy + parallelism, bounded by the probe interval |
| 3b merge | **main** | deterministic barrier |
| 4 consistency + user choices | **main** | single coherent view; needs `AskUserQuestion` |
| 5 regenerate | **main** | deterministic barrier |
| 6 verify | **1 subagent / inline** | isolates generated-module read |

## Batching rule for step 3

Every **discriminator-sibling set lands in the same batch**, so variant consistency is
resolved inside one agent's context rather than across blind agents. Independent tools are
bucketed by relatedness and size.

Dispatch only the **non-mutating** tools in the step-2 selected set, and the agent prompt must
forbid touching anything off its assigned list. A mutating tool the user explicitly approved
is probed **inline on the main thread**, never handed to a batch agent.

Pass every agent the same `<shapes-path>`. Run `mcpgen merge` (step 3b) once all batch agents
finish — `probe` only writes a per-tool part file; nothing consolidates `<shapes-path>` until
that merge.

## Rich agent contract

Each batch agent:

1. probes its tools with ids from the recon catalog;
2. reads raw payloads in its own context;
3. drafts the step-4 shape entry — `unwrap` / `return_model` / `return_container` / `fields` /
   `input_overrides`, plus `discriminator` + `variants` for its sibling group;
4. writes the part with **raw** `probed_args` (the step-3 ignore preflight keeps parts out of
   git; the scrub runs once at step 4);
5. returns a compact per-tool summary — decision plus unwrap path — **never the payload**.

## Recon subagent

Dispatch it only once the ignore preflight is green and `<shapes-path>` is fixed: it makes
live calls, so it must not run before the preflight.

It calls whatever no-arg / discovery / listing tools *this* server exposes — infer them from
`mcpgen list`, no tool name is universal — or reports that none exist. It returns a compact
catalog of bootstrap ids and discriminator enum values, never a raw payload.

Where it finds nothing, main falls back to `AskUserQuestion` for sample ids if step 2d's
condition holds. Where it does not, probe only the tools whose required args reference nothing
and record the rest as unprobed, naming the id you lacked.

For dispatch mechanics see `superpowers:dispatching-parallel-agents`.
