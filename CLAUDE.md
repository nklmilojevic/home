# Claude Code Guidelines

This is a Kubernetes homelab repository using Flux CD for GitOps. Every app lives under `kubernetes/apps/{namespace}/{app}/`.

## Tech Stack

- **Kubernetes**: Talos Linux
- **GitOps**: Flux v2 (Flux Operator + FluxInstance, not the legacy `flux-system` bootstrap)
- **Helm Charts**: bjw-s app-template for most apps, declared per-app via `OCIRepository` (Renovate keeps the tag current; copy it from any existing `ocirepository.yaml`)
- **Secrets**: External Secrets + 1Password Connect (runtime app secrets); bootstrap secrets injected via `vals` from `ref+op://` 1Password service-account refs (no SOPS)
- **Storage**: miroir (DRBD9 replicated volumes; default StorageClass `miroir`), OpenEBS hostpath (local scratch and mover caches), NFS (shared media)
- **Backups**: kopiur (kopia) to a `local` and an `r2` ClusterRepository, wired in via the shared Kustomize Components under `kubernetes/components/kopiur/`
- **Automation**: `just` (recipe runner; modules under `.just/`), Renovate (Mend app + `.renovate/` config)
- **Dev shell**: Nix flake (`flake.nix`); pre-commit via `lefthook.yml` (yamlfmt + yamllint)

## GitOps Mutation Boundary

- All persistent cluster changes MUST be made in this repository, committed, pushed, and reconciled by Flux.
- NEVER use `kubectl apply`, `kubectl create`, `kubectl delete`, `kubectl edit`, `kubectl patch`, `kubectl replace`, or `kubectl rollout restart`.
- NEVER apply locally rendered manifests or imperatively create Flux resources. `kubectl` is read-only for inspection and verification.
- The one sanctioned imperative write is creating an ad-hoc kopiur `Snapshot` (see "Useful Commands"): it is a one-shot object that nothing in Git owns, so there is no drift for Flux to fight.
- After pushing desired state, use `just kube reconcile` to request reconciliation through Flux.

## Directory Structure

```
kubernetes/
├── apps/                    # Applications by namespace
│   ├── {namespace}/
│   │   ├── namespace.yaml
│   │   ├── kustomization.yaml       # lists ./namespace.yaml + ./{app}/ks.yaml
│   │   └── {app}/
│   │       ├── ks.yaml              # Flux Kustomization (pulls in the kopiur Components)
│   │       └── app/
│   │           ├── kustomization.yaml   # lists helmrelease.yaml, ocirepository.yaml, [externalsecret.yaml], [pvc.yaml]
│   │           ├── helmrelease.yaml
│   │           ├── ocirepository.yaml   # per-app app-template OCIRepository
│   │           ├── externalsecret.yaml  # (optional)
│   │           └── pvc.yaml             # (optional, extra PVCs beyond the kopiur one)
├── components/
│   └── kopiur/              # Reusable Kustomize Components (kind: Component), wired via ks.yaml `components:`
│       ├── backup/          # SnapshotPolicy + SnapshotSchedule for the local and r2 repos (hashed `H` crons)
│       ├── secret/          # ExternalSecret with the repository credentials
│       └── volume/          # the app PVC + a ReplicationDestination-style restore from the latest snapshot
└── flux/
    └── apps.yaml            # Root Flux Kustomization `cluster-apps`; injects HelmRelease defaults (see below)
```

Repo root also contains `bootstrap/` (helmfile + kustomize for pre-Flux bootstrap) and `talos/` (Talos node config templates).

## Key Namespaces

- `home` - Home automation (Home Assistant, Z2MQTT, ESPHome, go2rtc, mosquitto, etc.)
- `media` - Media services (Plex, Sonarr, Radarr, Sabnzbd, Qbittorrent, etc.)
- `o11y` - Observability (VictoriaMetrics, VictoriaLogs, Vector, Grafana operator, gatus, NUT)
- `network` - Networking (Cilium, Envoy Gateway, external-dns, cloudflared, multus)
- `security` - Secrets and backups (external-secrets, onepassword-connect, versitygw, snapshot-controller, atuin)
- `database` - Data operators: redis-operator (+ per-app Redis instances), CloudNativePG, barman-cloud plugin
- `miroir-system` - Distributed storage (miroir operator + agents, DRBD9 over lvmthin)
- `kopiur-system` - Backups (kopiur operator, `local` + `r2` ClusterRepositories)
- `ai` - AI tooling (toolhive vMCP gateway, ha-mcp, grafana-mcp, kubesearch-mcp, hermes, atuin-ai)
- `misc` - Misc apps (paperless, forgejo, n8n, stirling-pdf, invoicing)
- `photos` - Immich (app-template) + its CloudNativePG cluster (barman-cloud plugin backups to R2); operators live in `database`
- `cert-manager`, `kube-system`, `openebs-system`, `renovate`, `system-upgrade`, `flux-system` - cluster infrastructure

## File Naming Conventions

- `ks.yaml` - Flux Kustomization resource
- `helmrelease.yaml` - HelmRelease resource
- `ocirepository.yaml` - OCIRepository (chart source) resource
- `kustomization.yaml` - Kustomize aggregation (or `kind: Component` under `components/`)
- `externalsecret.yaml` - External Secrets resource

## Centralized HelmRelease Defaults

`kubernetes/flux/apps.yaml` (the root `cluster-apps` Flux Kustomization) patches every app Kustomization so that each HelmRelease inherits standard `install`/`upgrade` remediation defaults:

- `install.remediation.retries: 3`
- `upgrade.cleanupOnFail: true`, `upgrade.remediation.strategy: rollback`, `upgrade.remediation.retries: 3`

A HelmRelease that needs different remediation opts out by carrying the label `flux.home/helm-defaults: skip`. **Do not repeat the `install`/`upgrade` boilerplate in individual HelmReleases.**

## Adding a New Application

1. Create directory: `kubernetes/apps/{namespace}/{app}/app/`

2. Create `{app}/ks.yaml` (Flux Kustomization). Wire in both kopiur Components, set `namespace:` on every `dependsOn` and `sourceRef`, and provide postBuild substitution vars:

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app {app-name}
spec:
  components:
    - ../../../../components/kopiur/backup
    - ../../../../components/kopiur/volume
  targetNamespace: {namespace}
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  dependsOn:
    - name: miroir-config
      namespace: miroir-system
    - name: external-secrets-stores
      namespace: security
  path: ./kubernetes/apps/{namespace}/{app}/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: home-kubernetes
    namespace: flux-system
  wait: false
  interval: 30m
  retryInterval: 1m
  timeout: 5m
  postBuild:
    substitute:
      APP: *app
      KOPIUR_CLAIM: {app}-config
      KOPIUR_CAPACITY: 1Gi
      APP_UID: "1000"
      APP_GID: "1000"
```

**Do not set per-app backup schedules.** The kopiur backup Component owns both
crons and spreads them itself: `H * * * *` for the hourly local repo and
`H 3 * * *` for the nightly R2 wave, where `H` is a per-schedule hash of the
object's identity. That gives every app a stable, distinct minute, so the
snapshots never all fire at once; a simultaneous snapshot storm has overloaded
the storage backend before.

1. Create `app/kustomization.yaml` (lists the app's own resources; the kopiur resources come from the Components in `ks.yaml`, not here):

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: { namespace }
resources:
  - ./helmrelease.yaml
  - ./ocirepository.yaml
  # - ./externalsecret.yaml   # if needed
  # - ./pvc.yaml              # for extra PVCs
```

1. Create `app/ocirepository.yaml` (per-app app-template chart source; Renovate keeps the tag current):

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/refs/heads/main/ocirepository-source-v1.json
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: { app-name }
  namespace: { namespace }
spec:
  interval: 10m
  layerSelector:
    mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
    operation: copy
  ref:
    tag: 5.1.0 # copy the current tag from any existing ocirepository.yaml
  url: oci://ghcr.io/bjw-s-labs/helm/app-template
```

1. Create `app/helmrelease.yaml` using the app-template pattern below.

2. Add `- ./{app}/ks.yaml` to the namespace's `kustomization.yaml`.

## HelmRelease Pattern

Each app references its **own** OCIRepository by name (not a shared one). Do not include `install`/`upgrade` remediation — it is injected centrally (see above). The canonical schema URL is the `bjw-s-labs` one:

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: {app}
  namespace: {namespace}
spec:
  interval: 5m
  chartRef:
    kind: OCIRepository
    name: {app}
  values:
    defaultPodOptions:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        fsGroupChangePolicy: OnRootMismatch
    controllers:
      {app}:
        containers:
          app:
            image:
              repository: {registry}/{image}
              tag: {version}
            env:
              TZ: "Europe/Belgrade"
            securityContext:
              allowPrivilegeEscalation: false
              capabilities:
                drop:
                  - ALL
    service:
      app:
        controller: {app}
        ports:
          http:
            port: &port {port}
    route:
      app:
        hostnames:
          - {app}.nikola.wtf
        parentRefs:
          - name: internal
            namespace: network
            sectionName: https
        rules:
          - backendRefs:
              - name: {app}
                port: *port
    persistence:
      config:
        existingClaim: {app}-config
```

## Common Patterns

### Security Context

Pod-level security context goes under `defaultPodOptions.securityContext`; container-level under `controllers.{app}.containers.{name}.securityContext`:

```yaml
defaultPodOptions:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    fsGroupChangePolicy: OnRootMismatch
controllers:
  { app }:
    containers:
      app:
        securityContext:
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
          capabilities:
            drop:
              - ALL
```

### NFS Media Mount

```yaml
persistence:
  media:
    type: custom
    volumeSpec:
      nfs:
        server: "10.5.0.10"
        path: "/var/nfs/shared/media"
    globalMounts:
      - path: "/media"
```

### LoadBalancer with Cilium

```yaml
service:
  app:
    type: LoadBalancer
    annotations:
      lbipam.cilium.io/ips: "10.40.0.X"
```

### Multus Network Attachment (e.g. for IoT/IoT-VLAN)

```yaml
controllers:
  { app }:
    pod:
      annotations:
        k8s.v1.cni.cncf.io/networks: |
          [{
            "name":"multus-iot",
            "namespace": "network",
            "ips": ["10.50.0.X/24"],
            "mac": "xx:xx:xx:xx:xx:xx"
          }]
```

### External Secrets

```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {app}-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-connect
    kind: ClusterSecretStore
  target:
    name: {app}-secrets
  dataFrom:
    - extract:
        key: {1password-item-name}
```

### CronJob Controller

```yaml
controllers:
  { app }:
    type: cronjob
    cronjob:
      schedule: "0 0 * * *"
      backoffLimit: 0
      concurrencyPolicy: Forbid
```

## Template Variables

Available via `postBuild.substitute` in `ks.yaml` (consumed by the kopiur Components):

- `${APP}` - Application name
- `${KOPIUR_CLAIM}` - PVC name for backups
- `${KOPIUR_CAPACITY}` - Storage size
- `${KOPIUR_CACHE_CAPACITY}` - Mover kopia cache size (default `10Gi` for
  restores, `5Gi` for snapshots); a restore streams every content blob it
  touches through this cache, so undersizing it fails mid-transfer
- `${APP_UID}` / `${APP_GID}` - User/group IDs (default: 1000)

There are no schedule variables — the Component's `H` crons place each app on
its own minute (see "Adding a New Application").

There are no global cluster variable ConfigMaps/Secrets in this repo; domains and network values are hardcoded per app (`*.nikola.wtf`, `10.40.0.x` LBs, `10.50.0.x` IoT).

## Domain & Routes

- Internal apps: `{app}.nikola.wtf` via the `internal` Envoy Gateway in `network`
- External apps: Use the `external` Envoy Gateway (in `network`) or a LoadBalancer
- Gateways are Envoy Gateway (GatewayClass `envoy`); Cilium's Gateway API is disabled

## Important Notes

- Never commit secret material — bootstrap secrets are plaintext YAML carrying `ref+op://` vals references (resolved from 1Password at apply time, no actual secret values), and runtime app secrets come from External Secrets + 1Password Connect
- Backups are wired in via the kopiur Kustomize Components (`components:` in `ks.yaml`), not by listing resources in `app/kustomization.yaml`
- Use YAML anchors (`&app`, `&port`) for DRY configuration
- Each app carries its own `ocirepository.yaml`; there is no centralized app-template OCIRepository
- Do not repeat HelmRelease `install`/`upgrade` remediation — it is injected by `flux/apps.yaml` (opt out with label `flux.home/helm-defaults: skip`)
- Use the canonical `bjw-s-labs` HelmRelease schema URL (not the old `bjw-s` org, which redirects)
- Timezone is `Europe/Belgrade`
- Standard dependencies: `miroir-config` (ns `miroir-system`), `external-secrets-stores` (ns `security`)

## Home Assistant

- HA runs in the `home` namespace as a plain container image (no Supervisor, so there is no `ha core ...` CLI and no `homeassistant.local`); UI at `hass.nikola.wtf`, config on the `home-assistant-config` PVC.
- Always use the ha-mcp tools (behind the toolhive gateway) for every Home Assistant task — reading state, searching for consumers of an entity, dashboards, automations, blueprints, entity-registry changes. They take effect live. Never use `hass-cli`, and do not fall back to SSH/`grep` over `/config` or `.storage`: `ha_search`, `ha_config_get_dashboard(mode="search")` and `ha_get_blueprint` cover the same ground, and `ha_call_service(ws_command=...)` is the escape hatch for anything that is a WebSocket command rather than a service (e.g. `blueprint/delete`, `repairs/list_issues`).
- Editing files under `/config` through the rootless sshd sidecar at `10.40.0.16` (user `me`) is a last resort, only when a change genuinely cannot be made through the API.
- Ask before restarting Home Assistant. Almost everything reloads without a restart, and a restart interrupts the household.
- In `telegram_bot.*` actions, never put `target:` inside `data:` — it is deprecated and removed in HA 2026.9.0. Use `data.chat_id: 104243855` (or an action-level `target.entity_id: notify.telegram_bot_...` notify entity, as the P1S `send_photo` automations do).

## Useful Commands

```bash
just                         # List all recipes (modules: bootstrap, kube, talos)
just kube reconcile          # Force a cluster reconcile from Git
just kube sync hr            # Force-sync all HelmReleases
kubectl create -f - <<'EOF'         # Trigger an ad-hoc kopiur snapshot
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Snapshot
metadata: {name: {app}-adhoc, namespace: {ns}}
spec: {policyRef: {name: {app}-local}}
EOF
just talos render-config {node}  # Render a node's Talos machine config
flux get ks -A               # List all Kustomizations
```
