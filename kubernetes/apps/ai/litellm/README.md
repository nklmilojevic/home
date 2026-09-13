# LiteLLM — ChatGPT subscription

LiteLLM is managed by `litellm-operator` in `ai`, with a disposable Redis cache
in `database`. Models are `LiteLLMModel` resources in `app/models/`; no PostgreSQL,
API-billed OpenAI fallback, virtual keys, or DB-backed usage tracking is configured.

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
       https://litellm.nikola.wtf/v1/responses \
       -H 'Content-Type: application/json' \
       -d '{"model":"chatgpt/gpt-5.6-sol","input":"Reply with OK only."}'
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

Responses API is preferred; supported models also accept `/v1/chat/completions`
through LiteLLM's Responses bridge. Subscription models do not accept token-limit
fields; LiteLLM strips them. `/v1/models` proves configuration, not account access.

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

Redis contains only response-cache/router state and needs no PVC or backups.
Responses expire after one hour. Authenticated Prometheus metrics are scraped by
the ServiceMonitor; VictoriaMetrics converts these automatically.

Provider/model changes belong in Git. Commit, push and run `just kube reconcile`.
The operator renders models into the proxy config and rolls the deployment on
config changes. Environment-backed master-key changes need a rollout to take
effect; change a pod annotation in `app/litellmproxy.yaml` through Git if needed.
Never imperatively edit the operator-owned Deployment or ConfigMap.
