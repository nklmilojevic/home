# Temporary ChatGPT output-recovery patch

`chatgpt-transformation.py.txt` is the unmodified provider module from
[BerriAI/litellm PR #38719](https://github.com/BerriAI/litellm/pull/38719), commit
`77423a29a8b59a101f834d6d10f778f64c2d7686`:

- Source: `litellm/llms/chatgpt/responses/transformation.py`.
- SHA-256: `fd9a9511e18820bc0b594286446e401f8653d8c5997a304dc110dcae5d8028af`.
- Base image: LiteLLM `v1.100.1`, Python 3.13, pinned by digest in `litellmproxy.yaml`.
- Original v1.100.1 module SHA-256:
  `cb474993f56e1dec8a458b1852dad28d0f7a2afc93a737bd0b917259f5c0498c`.
- License: MIT, retained in `LICENSE.litellm.txt`.

The `.txt` suffix preserves vendored bytes: this is an asset for the container,
not an importable module in this GitOps repository. Kustomize puts it under the
`transformation.py` ConfigMap key and the proxy mounts it over the installed
module. No code is fetched at pod startup and no OAuth credentials are embedded.
The ConfigMap name includes its content hash; the custom name reference rewrites
`LiteLLMProxy.spec.volumes`, causing the operator's Deployment to roll on changes.

## What it fixes

ChatGPT sends the answer in `response.output_item.done`, then a terminal
`response.completed` with `output: []`. The released provider's streaming path
loses the earlier items; the non-streaming Chat Completions bridge then raises
`Unknown items in responses API response: []` (HTTP 500).

The upstream override accumulates items per request, reconstructs an empty final
output, preserves authoritative non-empty outputs, and cleans up accumulators on
completed/failed/incomplete events. This is an **unmerged upstream patch**, not an
upstream release. Native Responses input-string normalization, its non-streaming
SDK contract, and database-backed UI functionality are outside this fix.

## Test without changing the running proxy

From the repository root:

```sh
# Negative control plus recovery, request isolation, cleanup and ordering tests:
bash kubernetes/apps/ai/litellm/tests/run.sh
# Also make three small synthetic requests (uses subscription allowance):
bash kubernetes/apps/ai/litellm/tests/run.sh --live
```

The runner passes the patch and tests over stdin to a separate Python process in
the current pod. The dependencies are those of the real image. It does not edit
files or patch the serving process; tokens are reused locally and never printed.
After deployment, also verify a real authenticated non-streaming HTTP request
from the hms-cpap pod; SDK tests do not exercise the entire proxy stack.

## Upgrade / removal

Renovate updates for this image are deliberately disabled in
`.renovate/allowedVersions.json5`. A new image must not silently receive an old
full-module override, especially if the Python site-packages path changes.

When an upstream release fixes the regression:

1. Test that release's stock provider against the same fixtures and live HTTP
   request before declaring the issue fixed.
2. Remove the ConfigMap generator, name-reference configuration, patch volume and
   mount, vendored assets and Renovate hold together with the image update.
3. Reconsider response caching only after confirming iterator serialization is
   fixed too. Do not mistake successful `/v1/models` or liveness for inference.
4. Commit, push and run `just kube reconcile`; verify actual rollout and an
   authenticated hms-cpap-shaped completion with text.

To roll back this workaround, revert its Git commit, push and reconcile. That
restores the known HTTP 500 behavior; it does not remove the OAuth token volume.
