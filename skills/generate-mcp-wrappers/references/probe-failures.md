# Probe failures — classify before you record

Read this when a probe returns `"str"`, an error-shaped object, a traceback, or nothing at
all. Everything here is about telling three classes apart, because they are recorded
differently and only one of them is worth retrying:

| Class | Retry? | Recorded as |
|---|---|---|
| Quota / rate-limit / auth | no | unprobed, or an error shape |
| Challenge / interstitial / transport blip | yes, bounded | unprobed after backoff |
| Settled fact about this call | no | unprobed with the message |

Misclassifying an auth failure as a challenge burns two backoffs and loses the actionable
half — which credential to set.

## First: read the raw payload

`probe` records structure, not content. A text payload collapses to the bare shape `"str"`
and the words are gone, so the phrases below match against text `probe` never kept. Capture
it before judging:

```bash
<mcpgen> call <server> <tool> --args '<same args>' --out <server>.<tool>.probe-raw.json
```

A probe that wrote no part file has nothing to capture — whether it died with a traceback or
exited on a single `[…] error:` line. `call` fails the same way and writes no `--out` file
either. Classify those from the error text alone.

## Quota / rate-limit / auth — do not retry

An HTTP 429 arrives as a status with no body, so it is recognized from the error line, not a
payload. Otherwise: a captured payload carrying a JSON `"error"` key, or text that is
*itself* the error message — `"quota exceeded"`, `"rate limit"`, `"try again later"`,
`"unauthorized"`, `"forbidden"`, `"invalid api key"`, `"not authenticated"`.

Require the phrase to **be** the error — a status code, error-shaped JSON, or the entire
response is the complaint — not a bare substring anywhere in a successful payload. A library
description containing the word "authentication" is not an auth error. Read the surrounding
content before concluding a probe failed.

Two outcomes, recorded differently:

- **The call returned, carrying an error payload.** Record the shape it actually has: a bare
  error string observes as `"str"`, an error-shaped JSON object is parsed by the client and
  observes as a dict. Either way leave `return_model: null` — the generated `-> Any` is
  correct and callers handle the error at runtime.
- **The transport or protocol failed.** No payload, therefore no shape: record the tool as
  **unprobed** with the reason. Do not invent `_observed_shape: "str"` for a call that never
  returned.

Three markers put a failure here rather than in the challenge class:

1. **`httpx.HTTPStatusError` reporting 429 on the tool call.** A 429 quoted inside an
   `[mcpgen] error:` credential line is a throttled token refresh, not this — the credential
   rule below governs it.
2. **Any `[mcpgen] error:` line naming a credential or token endpoint.** Most mention
   `mcpgen login` (in either case) after wording that varies — `OAuth re-auth needed for …`,
   `Token refresh failed (400, invalid_grant) …`, `No refresh_token for …` — and some carry
   no such tail at all, so match on the subject, not the sentence.

   These are **server-wide**: raised in the OAuth pre-flight, before any tool call, so every
   remaining tool fails identically and there is no point probing on.

   Two are transient rather than permanent — a line saying `retry later` or `retry when the
   authorization server is back` means the token endpoint was reachable but unhelpful. Wait
   once (the `Retry-After` if named, else 60 s) and re-issue the server's probing **from the
   top**; if it fails the same way, stop and record every unprobed tool with the credential
   note. Anything else here is permanent: stop now and record the same way.
3. **`httpx.HTTPStatusError` reporting 401.** Only the OAuth transport intercepts 401 and
   turns it into the message above. `--bearer`, static-header and raw-URL transports install
   no OAuth provider, so a stale token surfaces there as a bare 401 — the commonest
   static-credential expiry.

Note in `session-overview.md` which case it was, whether it was quota/rate-limit or auth, and
what credential (env var, API key) must be set before re-running to capture the real success
shape.

**403 and 503 are not classed here.** httpx builds its error message from the status, reason
and URL alone, so a genuine permission denial and a bot challenge are indistinguishable at
that point — the next section claims both and retries.

## Challenge / interstitial — retryable

A bot-protection interstitial is neither a shape nor a permanent failure. The tell is what the
*exception* looks like, not the body: a challenge page never reaches you, because `probe` and
`call` let httpx and SDK errors raise raw rather than excerpting the response.

Read it as a challenge when any of these lands on a host that answered moments earlier:

- an `httpx.HTTPStatusError` reporting **403, or any 5xx**. Its message carries the status,
  reason and URL and never the body. An error payload the *tool call itself* returned is the
  auth failure above, and that one arrives as a payload with no HTTP status attached;
- an `McpError` reporting `Connection closed`;
- **the probe does not return** after the last `[probe]   [i/n] args=…` line — with or without
  an `Unexpected content type: text/html` line from the SDK. An interstitial served at HTTP
  200 stalls the handshake indefinitely: the SDK pushes that error onto a stream nothing
  reads, so the probe hangs instead of exiting non-zero. Log line and stall are one marker,
  not two. `probe` has no timeout of its own — bound hosted probes with the harness's own
  timeout (never a `timeout` binary — see the Guards).

Any other transport-level exception — `httpx.ConnectError`, `ConnectTimeout`, `ReadTimeout`,
`RemoteProtocolError` — or an `HTTPStatusError` on a status no rule above claims, belongs here
too: **retry once** before recording the tool unprobed, since a connect blip must not
permanently abandon a tool.

**Backoff.** Do not record `_observed_shape` from a challenge. Retry that one tool 60 s, then
120 s, then stop; if the third attempt still challenges, record it unprobed with the reason.

**Per-marker give-up.** Once one tool has burned both retries on a given *marker* — a status,
a `Connection closed`, a stall — stop retrying that marker for this server. What survives the
backoff is not a challenge: a 403 that does is a real permission denial; a 5xx,
`Connection closed`, or stall that does is a host down or hanging.

Keep probing the other tools — entitlement is usually per tool, so most answer through a
persistent 403 — and record any *further* tool hitting that marker as unprobed, naming both
candidate causes and saying in `session-overview.md` which credential would have to be set.
That is the actionable half if the cause is entitlement, and it is lost if the failure is
filed as a challenge.

**Retry as a separate re-issue after the batch, never inside it.** The batch form aborts at
the first failure under `set -e`, and its accumulator variant collects failures without
retrying. Once a server has challenged, widen the interval for probes **not yet issued** to
~10 s — under the accumulator form the batch has already run to completion, so that means the
retries and any later batch. Resuming the 2 s cadence that tripped it re-trips it on the next
tool.

## Settled facts — neither class, no retry

A `[probe] error:` or `[call] error:` line that names no credential:
`MCP tool result has empty content`, `Unknown server …`, `config not found: …` and their like.
These are facts about this call, not a host under load. Record the tool unprobed with the
message. No backoff changes any of them.

## Cold-start noise on `uvx` / `npx` stdio servers — usually not a failure

The two launchers differ in where bootstrap output goes.

Anything the **npm** side writes to **stdout** before the first frame — an `npm install`
summary such as `added 40 packages` / `found 0 vulnerabilities`, or a package's own lifecycle
output — shares the stream that carries JSON-RPC frames, so the client tries to parse it and
logs `Failed to parse JSONRPC message from server` at ERROR, each with a full pydantic
`ValidationError` traceback.

`uv`/`uvx` writes its progress (`Downloaded lxml`, `Installed 1 package`) to **stderr**, which
passes through unparsed: you see those lines, they cause no parse error.

Those tracebacks come from the SDK's own logger, not from `mcpgen` — nothing here configures
logging, so they reach stderr through Python's last-resort handler, interleaved with the
`[probe] …` lines. A parse failure cannot fault the session: the error is handed to a message
handler that discards it, so the handshake completes on the first valid frame.

That makes them noise **only when the probe went on to succeed** — a written part file. Where
one landed, do not re-derive this, retry the probe, or record it.

The same lines mean something else in two cases:

- **A server whose own logging goes to stdout** emits them on every call, not only at cold
  start — expect them again on the next probe.
- **A mis-launched server** emits them and never sends a valid frame. If the launcher exits,
  that surfaces as `McpError: Connection closed`; if it stays up, the probe never returns
  after the last `[probe]` line.

A stdio server that has **never framed** is not a challenge — no backoff repairs a wrong
launch command, so fix the command and re-probe. Framing is the distinguishing question, not
the transport: a stdio server that answered earlier probes and then dies on one tool's input
*has* framed, so its `Connection closed` is claimed by the challenge marker above and takes
that routing unchanged.
