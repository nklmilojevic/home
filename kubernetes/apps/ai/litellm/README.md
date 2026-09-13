# LiteLLM — ChatGPT subscription

LiteLLM is managed by `litellm-operator` in `ai`, with disposable Redis router
state in `database`. Models are `LiteLLMModel` resources in `app/models/`; no PostgreSQL,
API-billed OpenAI fallback, virtual keys, or DB-backed usage tracking is configured.
The database-backed admin UI cannot log in in this mode; the master key is for
API authentication.

- External API base: `https://litellm.nikola.wtf/v1` (internal gateway only).
- Cluster API base: `http://litellm.ai.svc.cluster.local:4000/v1`.
- Model: `chatgpt/gpt-5.6-sol`.
- Authentication: the master key from 1Password item `litellm`, field `master_key`.
  Create a strong random `sk-` prefixed value before deploying. This protects the
  proxy; it is **not** an OpenAI API key. Do not commit it.

## Initial ChatGPT login

Use an eligible ChatGPT subscription and comply with the provider's usage terms.
Model availability and rate limits depend on the account; declaring a model does
not grant access to it. LiteLLM's [provider documentation](https://docs.litellm.ai/docs/providers/chatgpt)
describes the supported device flow.

1. Confirm the proxy, secret and volume are ready:

   ```sh
   kubectl -n ai get litellmproxy,pod,externalsecret,pvc
   ```

2. Send **one** request using the master key. In Bash, enter the key without
   echoing it or putting its value in shell history/command arguments:

   ```bash
   read -rsp 'LiteLLM master key: ' LITELLM_MASTER_KEY; echo
   printf 'header = "Authorization: Bearer %s"\n' "$LITELLM_MASTER_KEY" |
     curl --config - --fail-with-body --max-time 950 \
       https://litellm.nikola.wtf/v1/chat/completions \
       -H 'Content-Type: application/json' \
       -d '{"model":"chatgpt/gpt-5.6-sol","messages":[{"role":"user","content":"Reply with OK only."}],"stream":false}'
   unset LITELLM_MASTER_KEY
   ```

3. While that request is pending, use another terminal to view the device login:

   ```sh
   kubectl -n ai logs deployment/litellm --tail=40 -f
   ```

   Open the official OpenAI verification URL printed by LiteLLM, log into the
   intended account and enter the device code. Treat the code as sensitive; do
   not paste the logs into public issues. If OpenAI requires it, enable device
   code login in the account's security settings.

4. The request should complete after authorization. A client/router timeout may
   occur before the device-flow deadline; after successful login, retry the
   request. Do not send concurrent first-login requests or automatically retry
   failed logins: they can start competing device flows.

Use `/v1/chat/completions` for clients such as hms-cpap; the compatibility patch
below repairs its non-streaming Responses bridge. Native `/v1/responses` requests
must use an `input` **list**, not a string, with this ChatGPT backend; prefer
`stream: true` there because this patch does not fix the native non-streaming SDK
contract. Subscription models do not accept token-limit fields; LiteLLM strips
them. `/v1/models` proves configuration, not successful inference.

For hms-cpap's OpenAI-compatible provider, set Endpoint to
`http://litellm.ai.svc.cluster.local:4000` **without** `/v1` or a trailing slash
(the client appends `/v1/chat/completions`), Model to `chatgpt/gpt-5.6-sol`, and
API Key to the master key.

## Persistence and maintenance

`CHATGPT_TOKEN_DIR=/var/lib/litellm/chatgpt` is backed by the `litellm-chatgpt` PVC
(`miroir`, 1Gi). OAuth tokens never live in Git or the proxy ConfigMap. Kopiur's
shared Components back up this PVC to `local` hourly and `r2` nightly, using their
hashed schedules. A first deployment with no snapshot starts with an empty PVC.
Treat token backups as credentials. Restoring an old refresh token may still
require a new device login if the provider has rotated or revoked it.

Keep one replica. The operator currently has no deployment-strategy override and
uses RollingUpdate. Required self-affinity keeps its temporary surge pod on the
same node to avoid RWO multi-attach deadlocks, but the old and new processes can
briefly overlap. Avoid rollouts during initial login; simultaneous token refresh
is an upstream limitation, not solved by the single steady-state replica.

Redis contains only disposable router state and needs no PVC or backups.
Response caching is disabled: the ChatGPT provider returns a streaming iterator
internally, which LiteLLM's response cache cannot serialize. Authenticated
Prometheus metrics are scraped by the ServiceMonitor; VictoriaMetrics converts
these automatically.

Provider/model changes belong in Git. Commit, push and run `just kube reconcile`.
The operator renders models into the proxy config and rolls the deployment on
config changes. Environment-backed master-key changes need a rollout to take
effect; change a pod annotation in `app/litellmproxy.yaml` through Git if needed.
Never imperatively edit the operator-owned Deployment or ConfigMap.
