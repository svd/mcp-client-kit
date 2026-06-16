export const meta = {
  name: 'run-eval',
  description: 'Run generate-mcp-wrappers skill eval for one or more MCP servers',
  phases: [
    { title: 'Generate', detail: 'Run generate-mcp-wrappers skill per server' },
    { title: 'Analyze', detail: 'Run session-analyzer on agent transcript' },
    { title: 'Merge', detail: 'Merge narrative + analyzer into session-overview.md' },
    { title: 'Verify', detail: 'Run 5-check contract, write result.json' },
    { title: 'Report', detail: 'Aggregate all result.json → EVAL_REPORT.md' },
  ],
}

// ── Phase 1: Load manifest and resolve server list ──────────────────────────

phase('Generate')

if (args === undefined || args === null) {
  throw new Error('args is required: pass a server name array (e.g. ["github"]) or "all"')
}

// args may arrive as JSON string if the caller serialized it
let resolvedArgs = args
if (typeof resolvedArgs === 'string' && resolvedArgs !== 'all') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch(e) {}
}

log('Loading servers manifest…')
const manifestAgent = await agent(
  'Read /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/servers/servers.toml and return a JSON object with a "servers" array. Each item: {name, transport, launch, auth, auth_notes}. For auth_notes: if auth starts with "bearer:" write "Set ' + 'ENV_VAR=<token>" (use the actual env var name from the auth field); if auth is "oauth" write "Run: mcp-kit login <server>"; if auth is "none" write "No auth required.".',
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
            },
            required: ['name', 'transport', 'launch', 'auth', 'auth_notes'],
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
  'Read the file /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/agents/server-eval-agent.md and return its full content verbatim. Do not summarize or modify it.',
  { label: 'load-template' }
)

log('Template loaded. Starting pipeline…')

// ── Phase 2: pipeline — Generate → Analyze → Merge+Verify per server ────────

const results = await pipeline(
  servers,

  // Stage 1: Generate — run the generate-mcp-wrappers skill via server-eval-agent
  async (server) => {
    log(`[${server.name}] Starting generate stage…`)

    const prompt = promptTemplate
      .replace(/\{\{SERVER_NAME\}\}/g, server.name)
      .replace(/\{\{TRANSPORT\}\}/g, server.transport)
      .replace(/\{\{LAUNCH\}\}/g, server.launch)
      .replace(/\{\{AUTH\}\}/g, server.auth)
      .replace(/\{\{AUTH_NOTES\}\}/g, server.auth_notes)

    const summary = await agent(prompt, {
      label: `generate:${server.name}`,
      phase: 'Generate',
      schema: {
        type: 'object',
        properties: {
          server:        { type: 'string' },
          tool_count:    { type: 'number' },
          shaped_tools:  { type: 'array', items: { type: 'string' } },
          modes_hit:     { type: 'array', items: { type: 'string' } },
          verdict_hint:  { type: 'string' },
          notes:         { type: 'string' },
        },
        required: ['server', 'tool_count', 'modes_hit', 'verdict_hint'],
      },
    })

    log(`[${server.name}] Generate done — ${summary.tool_count ?? '?'} tools, modes: ${(summary.modes_hit || []).join(', ')}`)
    return { server, summary }
  },

  // Stage 2: Analyze — run session-analyzer on the agent's transcript
  async ({ server, summary }) => {
    log(`[${server.name}] Starting analyze stage…`)

    await agent(
      `You are running the session-analyzer skill on the eval agent transcript for server "${server.name}".

Use the session-analyzer skill to analyze what just happened in the generate-mcp-wrappers skill run for "${server.name}".
Write the analysis to /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/${server.name}/session-analyzer.md.
The analysis should cover: tool calls made, stages executed, decisions made, any errors/retries, approximate token usage.

When done, return "DONE: ${server.name}/session-analyzer.md written"`,
      { label: `analyze:${server.name}`, phase: 'Analyze' }
    )

    log(`[${server.name}] Analyze done`)
    return { server, summary }
  },

  // Stage 3: Merge + Verify + Runner (via eval-kit commands)
  async ({ server, summary }) => {
    log(`[${server.name}] Starting merge/verify/runner stage…`)

    const mergeVerify = await agent(
      `Run these commands in sequence in the working directory /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/:

1. uv run eval-kit merge-session ${server.name}
2. uv run eval-kit verify ${server.name}
3. uv run eval-kit runner ${server.name}

Report what each command printed to stdout and whether it succeeded (exit code 0).
Return a JSON object with these fields:
- merged: true if merge-session exited 0, false otherwise
- verified: true if verify exited 0, false otherwise
- runner_generated: true if runner exited 0, false otherwise
- verify_output: the stdout from the verify command (first 500 chars)`,
      {
        label: `verify:${server.name}`,
        phase: 'Verify',
        schema: {
          type: 'object',
          properties: {
            merged:           { type: 'boolean' },
            verified:         { type: 'boolean' },
            runner_generated: { type: 'boolean' },
            verify_output:    { type: 'string' },
          },
          required: ['merged', 'verified', 'runner_generated'],
        },
      }
    )

    log(`[${server.name}] Verify done — merged=${mergeVerify.merged}, verified=${mergeVerify.verified}, runner=${mergeVerify.runner_generated}`)
    return { server, summary, mergeVerify }
  }
)

// ── Phase 3: Report ──────────────────────────────────────────────────────────

phase('Report')

const successCount = results.filter(Boolean).length
log(`Pipeline complete — ${successCount}/${servers.length} servers succeeded`)

const reportResult = await agent(
  `Run this command in /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/:

  uv run eval-kit report

This generates doc/EVAL_REPORT.md from all result.json files.
Return the first 30 lines of the generated report file at doc/EVAL_REPORT.md.`,
  { label: 'report', phase: 'Report' }
)

log('Report generated: doc/EVAL_REPORT.md')
log(reportResult)

return {
  servers_evaluated: successCount,
  results: results.filter(Boolean).map(r => ({
    server:       r.server.name,
    modes_hit:    r.summary?.modes_hit   || [],
    verdict_hint: r.summary?.verdict_hint || 'unknown',
    verified:     r.mergeVerify?.verified || false,
  })),
}
