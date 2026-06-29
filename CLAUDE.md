# Claude Code Guidelines

This is a Kubernetes homelab repository using Flux CD for GitOps, managing ~40 applications across 15 namespaces.

## Tech Stack

- **Kubernetes**: Talos Linux
- **GitOps**: Flux v2 (Flux Operator + FluxInstance, not the legacy `flux-system` bootstrap)
- **Helm Charts**: bjw-s app-template (v5.0.1) for most apps, declared per-app via `OCIRepository`
- **Secrets**: External Secrets + 1Password Connect (runtime app secrets); bootstrap secrets injected via `vals` from `ref+op://` 1Password service-account refs (no SOPS)
- **Storage**: Rook-Ceph (distributed), OpenEBS (local), NFS (shared media)
- **Backups**: VolSync with R2 backend, wired in via a shared Kustomize Component
- **Automation**: `just` (recipe runner; modules under `.just/`), Renovate (Mend app + `.renovate/` config)
- **Dev shell**: Nix flake (`flake.nix`); pre-commit via `lefthook.yml` (yamlfmt + yamllint)

## Directory Structure

```
kubernetes/
├── apps/                    # Applications by namespace
│   ├── {namespace}/
│   │   ├── namespace.yaml
│   │   ├── kustomization.yaml       # lists ./namespace.yaml + ./{app}/ks.yaml
│   │   └── {app}/
│   │       ├── ks.yaml              # Flux Kustomization (pulls in the volsync Component)
│   │       └── app/
│   │           ├── kustomization.yaml   # lists helmrelease.yaml, ocirepository.yaml, [externalsecret.yaml], [pvc.yaml]
│   │           ├── helmrelease.yaml
│   │           ├── ocirepository.yaml   # per-app app-template OCIRepository
│   │           ├── externalsecret.yaml  # (optional)
│   │           └── pvc.yaml             # (optional, extra PVCs beyond the volsync one)
├── components/
│   └── volsync/             # Reusable Kustomize Component (kind: Component), wired via ks.yaml `components:`
│       ├── kustomization.yaml
│       ├── externalsecret.yaml
│       ├── pvc.yaml
│       ├── replicationdestination.yaml
│       └── replicationsource.yaml
└── flux/
    └── apps.yaml            # Root Flux Kustomization `cluster-apps`; injects HelmRelease defaults (see below)
```

Repo root also contains `bootstrap/` (helmfile + kustomize for pre-Flux bootstrap) and `talos/` (Talos node config templates).

## Key Namespaces

- `home` - Home automation (Home Assistant, Z2MQTT, ESPHome, go2rtc, mosquitto, etc.)
- `media` - Media services (Plex, Sonarr, Radarr, Sabnzbd, Qbittorrent, etc.)
- `monitoring` - Observability (VictoriaMetrics, VictoriaLogs, Vector, Grafana operator, gatus, NUT)
- `network` - Networking (Cilium, Envoy Gateway, external-dns, cloudflared, multus)
- `security` - Secrets and backups (external-secrets, onepassword-connect, volsync, snapshot-controller, atuin)
- `database` - Redis (redis-operator + per-app instances)
- `rook-ceph` - Distributed storage (rook-ceph operator + cluster + ceph-csi-drivers)
- `ai` - AI tooling (toolhive, ha-mcp)
- `misc` - Misc apps (paperless, forgejo, n8n, manyfold, stirling-pdf, invoicing)
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

2. Create `{app}/ks.yaml` (Flux Kustomization). Wire in the volsync Component, set `namespace:` on every `dependsOn` and `sourceRef`, and provide postBuild substitution vars:
```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app {app-name}
spec:
  components:
    - ../../../../components/volsync
  targetNamespace: {namespace}
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  dependsOn:
    - name: rook-ceph-cluster
      namespace: rook-ceph
    - name: volsync
      namespace: security
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
      VOLSYNC_CLAIM: {app}-config
      VOLSYNC_CAPACITY: 1Gi
      APP_UID: "1000"
      APP_GID: "1000"
```

3. Create `app/kustomization.yaml` (lists the app's own resources; the volsync resources come from the Component in `ks.yaml`, not here):
```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: {namespace}
resources:
  - ./helmrelease.yaml
  - ./ocirepository.yaml
  # - ./externalsecret.yaml   # if needed
  # - ./pvc.yaml              # for extra PVCs
```

4. Create `app/ocirepository.yaml` (per-app app-template chart source; Renovate keeps the tag current):
```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/refs/heads/main/ocirepository-source-v1.json
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: {app-name}
  namespace: {namespace}
spec:
  interval: 10m
  layerSelector:
    mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
    operation: copy
  ref:
    tag: 5.0.1
  url: oci://ghcr.io/bjw-s-labs/helm/app-template
```

5. Create `app/helmrelease.yaml` using the app-template pattern below.

6. Add `- ./{app}/ks.yaml` to the namespace's `kustomization.yaml`.

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
  {app}:
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
  {app}:
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
  {app}:
    type: cronjob
    cronjob:
      schedule: "0 0 * * *"
      backoffLimit: 0
      concurrencyPolicy: Forbid
```

## Template Variables

Available via `postBuild.substitute` in `ks.yaml` (consumed by the volsync Component):
- `${APP}` - Application name
- `${VOLSYNC_CLAIM}` - PVC name for backups
- `${VOLSYNC_CAPACITY}` - Storage size
- `${APP_UID}` / `${APP_GID}` - User/group IDs (default: 1000)

There are no global cluster variable ConfigMaps/Secrets in this repo; domains and network values are hardcoded per app (`*.nikola.wtf`, `10.40.0.x` LBs, `10.50.0.x` IoT).

## Domain & Routes

- Internal apps: `{app}.nikola.wtf` via the `internal` Envoy Gateway in `network`
- External apps: Use the `external` Envoy Gateway (in `network`) or a LoadBalancer
- Gateways are Envoy Gateway (GatewayClass `envoy`); Cilium's Gateway API is disabled

## Important Notes

- Never commit secret material — bootstrap secrets are plaintext YAML carrying `ref+op://` vals references (resolved from 1Password at apply time, no actual secret values), and runtime app secrets come from External Secrets + 1Password Connect
- Backups are wired in via the volsync Kustomize Component (`components:` in `ks.yaml`), not by listing resources in `app/kustomization.yaml`
- Use YAML anchors (`&app`, `&port`) for DRY configuration
- Each app carries its own `ocirepository.yaml`; there is no centralized app-template OCIRepository
- Do not repeat HelmRelease `install`/`upgrade` remediation — it is injected by `flux/apps.yaml` (opt out with label `flux.home/helm-defaults: skip`)
- Use the canonical `bjw-s-labs` HelmRelease schema URL (not the old `bjw-s` org, which redirects)
- Timezone is `Europe/Belgrade`
- Standard dependencies: `rook-ceph-cluster` (ns `rook-ceph`), `volsync` (ns `security`), `external-secrets-stores` (ns `security`)

## Useful Commands

```bash
just                         # List all recipes (modules: bootstrap, kube, talos, rook, volsync)
just kube reconcile          # Force a cluster reconcile from Git
just kube apply-ks {ns} {app} # Apply a Kustomization locally
just kube sync hr            # Force-sync all HelmReleases
just volsync snapshot {ns} {app}   # Trigger a VolSync snapshot
just volsync restore {ns} {app}    # Restore an app PVC from VolSync
just talos render-config {node}  # Render a node's Talos machine config
flux get ks -A               # List all Kustomizations
```
