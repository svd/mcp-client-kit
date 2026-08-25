export const meta = {
  name: 'run-eval',
  description: 'Run generate-mcp-wrappers skill eval for one or more MCP servers',
  phases: [
    { title: 'Generate', detail: 'Run generate-mcp-wrappers skill per server' },
    { title: 'Verify', detail: 'Run 5-check contract, write result.json' },
    { title: 'Analyze', detail: 'Run session-analyzer on agent transcript' },
    { title: 'Synthesize', detail: 'Write per-server narrative + cross-server synthesis fragments' },
    { title: 'Report', detail: 'Aggregate all result.json → EVAL_REPORT.md' },
  ],
}

// ── Phase 1: Load manifest and resolve server list ──────────────────────────

phase('Generate')

if (args === undefined || args === null) {
  throw new Error('args is required: pass a server name array (e.g. ["github"]) or "all"')
}

// args may arrive as JSON string if the caller serialized it,
// or as a bare server name / space-/comma-separated list from a slash command.
let resolvedArgs = args
if (typeof resolvedArgs === 'string' && resolvedArgs !== 'all') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch(e) {}
  if (typeof resolvedArgs === 'string' && resolvedArgs !== 'all') {
    resolvedArgs = resolvedArgs.trim().split(/[\s,]+/).filter(Boolean)
  }
}

log('Generating .mcp.eval.json from servers.toml…')
const genConfig = await agent(
  'Run these three commands and report results:\n' +
  '1. `pwd` — capture the absolute working directory path\n' +
  '2. `uv run eval-kit gen-config`\n' +
  '3. `uuidgen` — a fresh unique id for this workflow run\n' +
  'Return the absolute path from pwd as project_root, whether gen-config succeeded, and the ' +
  'uuidgen output verbatim as run_nonce.',
  {
    label: 'gen-config',
    schema: {
      type: 'object',
      properties: {
        project_root:  { type: 'string' },
        gen_config_ok: { type: 'boolean' },
        run_nonce:     { type: 'string' },
      },
      required: ['project_root', 'gen_config_ok', 'run_nonce'],
    },
  }
)
const PROJECT_ROOT = genConfig.project_root
// Claude Code stores a project's sessions under ~/.claude/projects/<slug>, where the slug is
// the absolute project path with every non-alphanumeric character replaced by a dash. The
// analyze stage needs it to scope its transcript search to THIS project's workflow runs.
// Identifies THIS workflow run inside transcript files, so the analyze stage never
// confuses a concurrent /run-eval of the same server with its own. Workflow scripts
// cannot mint one themselves — Date.now()/Math.random() throw — so an agent does it.
const RUN_NONCE = String(genConfig.run_nonce ?? '').trim()
// Stale-run and concurrent-run protection now rest entirely on this id, and it is
// LLM-reported. A degenerate value ('', 'unknown', prose) would silently degrade the
// analyze stage to ambiguity for every server, so fail at minute 0 instead.
if (!/^[0-9a-fA-F-]{8,64}$/.test(RUN_NONCE)) {
  throw new Error(`gen-config returned a malformed run_nonce: ${JSON.stringify(genConfig.run_nonce)}`)
}

// `pwd` is agent-reported, so normalise trailing slashes and stray whitespace first:
// a slug ending in '-' matches no directory and would kill the whole analyze phase.
const PROJECT_SLUG = PROJECT_ROOT.trim().replace(/\/+$/, '').replace(/[^a-zA-Z0-9]/g, '-')

log('Loading servers manifest…')
const manifestAgent = await agent(
  'Read servers/servers.toml and return a JSON object with a "servers" array. Each item: {name, transport, launch, auth, auth_notes, seed}. For seed: the entry\'s "seed" array verbatim, or [] when the entry has no seed field. For auth_notes: if auth starts with "bearer:" write "Set ' + 'ENV_VAR=<token>" (use the actual env var name from the auth field); if auth is "oauth" write "Run: mcpgen login <server>"; if auth is "none" write "No auth required.".',
  {
    label: 'load-manifest',
    schema: {
      type: 'object',
      properties: {
        servers: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name:       { type: 'string' },
              transport:  { type: 'string' },
              launch:     { type: 'string' },
              auth:       { type: 'string' },
              auth_notes: { type: 'string' },
              seed:       { type: 'array', items: { type: 'string' } },
            },
            required: ['name', 'transport', 'launch', 'auth', 'auth_notes', 'seed'],
          },
        },
      },
      required: ['servers'],
    },
  }
)

const allServers = manifestAgent.servers

let servers
if (resolvedArgs === 'all') {
  servers = allServers
  log(`Evaluating all ${servers.length} server(s): ${servers.map(s => s.name).join(', ')}`)
} else if (Array.isArray(resolvedArgs)) {
  servers = allServers.filter(s => resolvedArgs.includes(s.name))
  const missing = resolvedArgs.filter(name => !allServers.find(s => s.name === name))
  if (missing.length > 0) {
    log(`Warning: server(s) not found in manifest: ${missing.join(', ')}`)
  }
  log(`Evaluating ${servers.length} server(s): ${servers.map(s => s.name).join(', ')}`)
} else {
  throw new Error('args must be a server name array (e.g. ["github", "time"]) or "all"')
}

if (servers.length === 0) {
  throw new Error('No matching servers found. Check args and servers.toml.')
}

// ── Load agent prompt template ───────────────────────────────────────────────

log('Loading agent prompt template…')
const promptTemplate = await agent(
  'Read agents/server-eval-agent.md and return its full content verbatim. Do not summarize or modify it.',
  { label: 'load-template' }
)

log('Template loaded. Starting pipeline…')

// ── Phase 2: pipeline — Generate → Verify → Analyze per server ──────────────
// Verify runs before Analyze so the analyzer reads result.json and run.py as
// ground truth instead of predicting what the verifier will say.

const results = await pipeline(
  servers,

  // Stage 1: Generate — run the generate-mcp-wrappers skill via server-eval-agent
  async (server) => {
    log(`[${server.name}] Starting generate stage…`)

    const prompt = promptTemplate
      .replace(/\{\{PROJECT_ROOT\}\}/g, PROJECT_ROOT)
      .replace(/\{\{SERVER_NAME\}\}/g, server.name)
      .replace(/\{\{TRANSPORT\}\}/g, server.transport)
      .replace(/\{\{LAUNCH\}\}/g, server.launch)
      .replace(/\{\{AUTH\}\}/g, server.auth)
      .replace(/\{\{AUTH_NOTES\}\}/g, server.auth_notes)
      .replace(/\{\{RUN_NONCE\}\}/g, RUN_NONCE)
      .replace(/\{\{SEED\}\}/g,
        server.seed && server.seed.length > 0
          ? '\n  - `' + server.seed.join('`\n  - `') + '`'
          : 'none')

    const summary = await agent(prompt, {
      label: `generate:${server.name}`,
      phase: 'Generate',
      schema: {
        type: 'object',
        properties: {
          server:        { type: 'string' },
          tool_count:    { type: 'number' },
          shaped_tools:  { type: 'array', items: { type: 'string' } },
          verdict_hint:  { type: 'string' },
          notes:         { type: 'string' },
        },
        required: ['server', 'tool_count', 'verdict_hint'],
      },
    })

    log(`[${server.name}] Generate done — ${summary.tool_count ?? '?'} tools, verdict: ${summary.verdict_hint ?? '?'}`)
    return { server, summary }
  },

  // Stage 2: Verify + Runner (via eval-kit commands)
  async ({ server, summary }) => {
    log(`[${server.name}] Starting verify/runner stage…`)

    const verifyRunner = await agent(
      `Do these two steps in the project root (your current working directory):

1. Run: uv run eval-kit verify ${server.name}
   Capture its stdout and exit code.

2. Generate the sample runner by invoking the **mcp-client-kit:generate-mcp-runner** skill
   via the Skill tool. Tell the skill:
   - Artifacts are in eval/${server.name}/ (${server.name}.py, ${server.name}.shapes.json,
     ${server.name}.verify.json).
   - Write the runner to eval/${server.name}/run.py.
   - Transport ${server.transport}, auth ${server.auth}, launch/URL: ${server.launch}.
   - Read-only tools only; do NOT auto-run the generated script.
   Let the skill select tools, pick real args from verify.json, emit one call per probed
   discriminator variant, and validate the output statically (ast/py_compile).

Return a JSON object with these fields:
- verified: true if verify exited 0, false otherwise
- runner_generated: true if eval/${server.name}/run.py was written and statically valid, false otherwise
- verify_output: the stdout from the verify command (first 500 chars)`,
      {
        label: `verify:${server.name}`,
        phase: 'Verify',
        schema: {
          type: 'object',
          properties: {
            verified:         { type: 'boolean' },
            runner_generated: { type: 'boolean' },
            verify_output:    { type: 'string' },
          },
          required: ['verified', 'runner_generated'],
        },
      }
    )

    log(`[${server.name}] Verify done — verified=${verifyRunner.verified}, runner=${verifyRunner.runner_generated}`)
    return { server, summary, verifyRunner }
  },

  // Stage 3: Analyze — run session-analyzer on the generate agent's transcript
  async ({ server, summary, verifyRunner }) => {
    log(`[${server.name}] Starting analyze stage…`)

    const analyzePrompt = `You are running the session-analyzer skill on the eval agent transcript for server "${server.name}".

**Locating the transcript.** You must analyze the transcript of this run's
\`generate:${server.name}\` subagent — and only that one. Identify it exactly like this:
1. Search this project's workflow transcripts:
   \`~/.claude/projects/${PROJECT_SLUG}/*/subagents/workflows/wf_*/agent-*.jsonl\`
2. Keep only files containing this run's unique id (use \`grep -lF\`), which no other
   \`/run-eval\` run has:
   \`${RUN_NONCE}\`
3. Of those, keep only files containing this exact single-line marker, which appears only in
   the generate agent's prompt:
   \`skill for the **${server.name}** MCP server\`
4. Exclude any remaining file that also contains the string \`Locating the transcript\` — that
   is your OWN transcript and any retry of it (this very instruction carries both the run id
   and the marker, so your transcript self-matches; expected). Do NOT filter on the string
   \`session-analyzer\`: every subagent transcript carries it in the skill roster, so it
   excludes everything.
5. Exactly one file must remain: the generate transcript. Do not rank candidates by
   modification time and do not guess — the run id in step 2 already pins the correct run,
   so anything left over is a genuine ambiguity. If ZERO remain, STOP: do not widen the
   search, and do not fall back to files that merely mention \`eval/${server.name}/\` (the
   verify agent's transcript matches that too, and analyzing it attributes harness actions to
   the skill under test). (A \`resumeFromRunId\` continuation reuses the same run id and
   this search spans every session directory in the project, so the original run's generate
   transcript is normally still found — a resumed run should analyze normally. Zero matches
   there means the original \`wf_*\` directory is gone.)
Never ask the agent for its session ID — it cannot know its own transcript path.

**Ground truth — read what exists, predict nothing.** The verify stage has already run for this
server, so its artifacts are on disk before you start:
- \`eval/${server.name}/result.json\` — the authoritative verifier outcome. Never describe check
  results from memory or inference; quote this file.
${verifyRunner?.runner_generated
  ? `- \`eval/${server.name}/run.py\` — the sample runner. It is generated by the **harness's** verify
  stage, not by the generate-mcp-wrappers skill, so its absence from the skill run is expected and
  is not a finding.`
  : `- \`eval/${server.name}/run.py\` was **not** produced: the harness's runner stage failed or was
  skipped this run. Do not read it and do not invent its contents. Runner generation belongs to the
  harness, not to the generate-mcp-wrappers skill, so record this as a harness-side gap — never as a
  skipped skill step.`}

**Attribution.** The skill under test is loaded from the path printed by:
\`uv run python -c "from eval_harness.versions import runtime_versions; print(runtime_versions()['skill_path'])"\`
Read \`skills/generate-mcp-wrappers/SKILL.md\` under that path and attribute each action to a
concrete skill step by reading it, rather than inferring attribution from \`skills_in_context\`.

**Duration.** One run gets one number. Quote the \`Duration\` from the \`## Run Metadata\` block of
\`eval/${server.name}/session-overview.md\` — the generate agent brackets its whole run with
\`date +%s\`, so that value is authoritative. Do not derive a second span from transcript
timestamps and do not report both.

Use the session-analyzer skill to analyze what happened in the generate-mcp-wrappers skill run for "${server.name}".
Write the analysis to eval/${server.name}/session-analyzer.md.
The analysis should cover: tool calls made, stages executed, decisions made, any errors/retries, approximate token usage.

For reference, the verify stage reported: verified=${verifyRunner?.verified}, runner_generated=${verifyRunner?.runner_generated}.

When done, return "DONE: eval/${server.name}/session-analyzer.md written"

If you could not identify the generate transcript with confidence, do NOT analyze any other
file. Write eval/${server.name}/session-analyzer.md containing only a
"## Transcript Not Identified" section explaining what you searched and what matched, and
return "FAILED: transcript not identified for ${server.name}".`

    const analyzeOk = (r) =>
      Boolean(r) && !r.includes('API Error') && !r.includes('Please run /login') && r.includes('DONE:')

    let analyzeResult = await agent(analyzePrompt, { label: `analyze:${server.name}`, phase: 'Analyze' })
    if (!analyzeOk(analyzeResult)) {
      log(`[${server.name}] Analyze failed — retrying once…`)
      analyzeResult = await agent(analyzePrompt, { label: `analyze:${server.name}:retry`, phase: 'Analyze' })
    }

    // A silently-failed analyze stage is worse than a loud one: the narrative and synthesis
    // stages downstream would otherwise present a missing analysis as a completed one.
    const analyzed = analyzeOk(analyzeResult)
    if (analyzed) {
      log(`[${server.name}] Analyze done`)
    } else {
      const reason = typeof analyzeResult === 'string' && analyzeResult.includes('FAILED:')
        ? 'transcript not identified'
        : 'no DONE marker returned'
      log(`[${server.name}] Analyze FAILED after retry (${reason}) — session-analyzer.md is missing or a stub`)
    }
    return { server, summary, verifyRunner, analyzed }
  }

)

// ── Phase 3: Synthesize — per-server narrative + cross-server synthesis ──────

phase('Synthesize')

const completed = results.filter(Boolean)
const successCount = completed.length
log(`Pipeline complete — ${successCount}/${servers.length} servers succeeded`)

const unanalyzed = completed.filter(r => !r.analyzed).map(r => r.server.name)
if (unanalyzed.length > 0) {
  log(`WARNING: ${unanalyzed.length} server(s) have no usable session analysis: ${unanalyzed.join(', ')}`)
}
log('Generating per-server narrative fragments…')

await pipeline(
  completed,
  async ({ server, analyzed }) => {
    await agent(
      `${analyzed ? '' : `NOTE: the session analysis for this server FAILED — eval/${server.name}/session-analyzer.md is absent or a "Transcript Not Identified" stub. Say so in one sentence in the narrative and do not infer how the skill executed.

`}Read these two files:
- eval/${server.name}/session-overview.md
- eval/${server.name}/result.json

Write a concise 4–8 sentence narrative fragment to eval/${server.name}/narrative.md covering:
- How many tools the server exposes and how many were probed
- Which modes were hit and the key reason (e.g. "all tools returned unstructured text → Mode A only")
- Any notable errors or recovery (one sentence max)
- Any Path-E/F guard decisions made
- Overall assessment

Do not copy large blocks from session-overview.md — synthesize.
Return "DONE: eval/${server.name}/narrative.md written" when complete.`,
      { label: `narrative:${server.name}`, phase: 'Synthesize' }
    )
  }
)

log('Generating cross-server synthesis…')
await agent(
  `Read doc/EVAL_REPORT.md (the mechanical matrix) and all eval/*/narrative.md files that exist.

**Scope rule — no unverified harness claims.** The narratives describe servers, not the harness.
Any statement about what the eval harness itself does or cannot do (verify.py checks, skip
reasons, report rendering) must be read out of \`eval_harness/\` source before you write it, and
cited as \`file:line\`. If you cannot cite it, do not write it — a stale harness limitation
carried over from an earlier run has repeatedly become the report's top "next step" after the
code was already fixed. Prefer server-side observations; leave harness gaps to
doc/FIXES-eval-kit.md.

Write a cross-server synthesis to eval/_synthesis.md covering:
1. **Overall verdict** (1–2 sentences): how well the generate-mcp-wrappers skill performed across all servers
2. **Mode coverage gaps**: which servers hit only Mode A when richer probing could have yielded B/C; explain why
3. **Known issues**: recurring errors or patterns across servers
4. **Next steps**: the 2–3 highest-value improvements to pursue

Keep it under 400 words. Return "DONE: eval/_synthesis.md written" when complete.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

// ── Phase 4: Report ──────────────────────────────────────────────────────────

phase('Report')

const reportResult = await agent(
  `Run this command in the project root (your current working directory):

  uv run eval-kit report --with-narrative

This generates doc/EVAL_REPORT.md from all result.json files with narrative fragments spliced in.
Return the first 30 lines of the generated report file at doc/EVAL_REPORT.md.`,
  { label: 'report', phase: 'Report' }
)

log('Report generated: doc/EVAL_REPORT.md')
log(reportResult)

return {
  servers_evaluated: successCount,
  servers_unanalyzed: unanalyzed,
  results: completed.map(r => ({
    server:       r.server.name,
    verdict_hint: r.summary?.verdict_hint || 'unknown',
    verified:     r.verifyRunner?.verified || false,
    analyzed:     r.analyzed || false,
  })),
}
