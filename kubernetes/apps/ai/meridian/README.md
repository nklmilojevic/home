# Meridian — Claude subscription

Meridian runs the official Claude Agent SDK and exposes it as an Anthropic
Messages endpoint at `http://meridian.ai.svc.cluster.local:3456`.

The ChatGPT subscription is served separately by `cliproxyapi`. Claude is not
routed through it: CLIProxyAPI reaches a Claude subscription only by cloaking
other clients as the Claude Code CLI. Routing Claude through an
OpenAI-compatible gateway also loses thinking blocks and Fable's
adaptive-thinking constraints, and Meridian's Pi support (adapter detection,
session affinity, prompt scrubbing) assumes Pi as the direct client. Bastion
uses `pi-meridian-extension`, which registers a `meridian/*` provider in Pi.

- Credential: the long-lived `claude setup-token` value in the 1Password item
  `bastion`, field `CLAUDE_CODE_OAUTH_TOKEN`. Meridian passes it to the SDK; no
  browser login and no credential volume in the cluster.
- Client auth: the 1Password item `meridian`, field `api_key`, sent by
  clients as `MERIDIAN_API_KEY`. Not an Anthropic key.
- Anthropic meters third-party harness traffic on Pro/Max as Extra Usage, and
  Pi's default system prompt trips that classifier on tool-bearing requests
  (`400 Third-party apps now draw from your extra usage`). Two idempotent
  layers rewrite Pi's identity line: `pi-meridian-extension` on the client and
  `@rynfar/meridian-plugin-pi-scrub`, loaded on Meridian via `MERIDIAN_PLUGINS`.
  If the error returns, check `GET /plugins` on Meridian shows `pi-scrub`
  active, then re-measure before assuming the prompt is fine.
- Meridian's session store is an emptyDir, so a restart costs one history
  replay per live conversation.
  Opus 1M is included on Max; Sonnet is served at 200k because its 1M tier is
  Extra Usage on every plan.
