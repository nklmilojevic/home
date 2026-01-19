# Claude Code Guidelines

This is a Kubernetes homelab repository using Flux CD for GitOps, managing ~40 applications across 13 namespaces.

## Tech Stack

- **Kubernetes**: Talos Linux
- **GitOps**: Flux v2
- **Helm Charts**: bjw-s app-template for most apps
- **Secrets**: External Secrets with 1Password Connect, SOPS encryption
- **Storage**: Rook-Ceph (distributed), OpenEBS (local), NFS (shared media)
- **Backups**: VolSync with R2 backend
- **Automation**: Task (Go-based task runner)

## Directory Structure

```
kubernetes/
├── apps/                    # Applications by namespace
│   ├── {namespace}/
│   │   ├── namespace.yaml
│   │   ├── kustomization.yaml
│   │   └── {app}/
│   │       ├── ks.yaml              # Flux Kustomization
│   │       └── app/
│   │           ├── kustomization.yaml
│   │           ├── helmrelease.yaml
│   │           └── externalsecret.yaml  # (optional)
├── flux/
│   ├── config/              # Flux controller config
│   ├── repositories/        # Helm/OCI repos
│   │   ├── helm/
│   │   └── oci/
│   └── vars/                # Cluster variables
│       ├── cluster-settings.yaml      # ConfigMap
│       └── cluster-secrets.sops.yaml  # Encrypted Secret
└── templates/
    └── volsync/             # Reusable backup templates
```

## Key Namespaces

- `home` - Home automation (Home Assistant, Z2MQTT, ESPHome)
- `media` - Media services (Plex, Sonarr, Radarr, etc.)
- `monitoring` - Observability (Grafana, VictoriaMetrics)
- `network` - Networking (Cilium, external-dns, cloudflared)
- `security` - Secrets and backups (external-secrets, volsync)
- `database` - Data storage (Redis)
- `rook-ceph` - Distributed storage

## File Naming Conventions

- `ks.yaml` - Flux Kustomization resource
- `helmrelease.yaml` - HelmRelease resource
- `kustomization.yaml` - Kustomize aggregation
- `externalsecret.yaml` - External Secrets resource
- `*.sops.yaml` - SOPS-encrypted files

## Adding a New Application

1. Create directory: `kubernetes/apps/{namespace}/{app}/app/`

2. Create `ks.yaml` (Flux Kustomization):
```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app {app-name}
  namespace: flux-system
spec:
  targetNamespace: {namespace}
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  dependsOn:
    - name: rook-ceph-cluster
    - name: volsync
    - name: external-secrets-stores
  path: ./kubernetes/apps/{namespace}/{app}/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: home-kubernetes
  wait: false
  interval: 30m
  retryInterval: 1m
  timeout: 5m
  postBuild:
    substitute:
      APP: *app
      VOLSYNC_CLAIM: {app}-config
      VOLSYNC_CAPACITY: 1Gi
```

3. Create `app/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./helmrelease.yaml
  - ../../../../templates/volsync
```

4. Create `app/helmrelease.yaml` using app-template pattern (see examples below)

5. Add to namespace's `kustomization.yaml`:
```yaml
resources:
  - ./{app}/ks.yaml
```

## HelmRelease Pattern

All apps use the centralized app-template OCIRepository:

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
  # renovate: registryUrl=http://bjw-s.github.io/helm-charts/
  chartRef:
    kind: OCIRepository
    name: app-template
    namespace: flux-system
  install:
    remediation:
      retries: 3
  upgrade:
    cleanupOnFail: true
    remediation:
      strategy: rollback
      retries: 3
  values:
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
        pod:
          securityContext:
            runAsUser: 1000
            runAsGroup: 1000
            runAsNonRoot: true
            fsGroup: 1000
            fsGroupChangePolicy: OnRootMismatch
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
            namespace: kube-system
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

### Security Context (standard for all apps)
```yaml
pod:
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    runAsNonRoot: true
    fsGroup: 1000
    fsGroupChangePolicy: OnRootMismatch
containers:
  app:
    securityContext:
      allowPrivilegeEscalation: false
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

Available via postBuild substitution:
- `${APP}` - Application name
- `${VOLSYNC_CLAIM}` - PVC name for backups
- `${VOLSYNC_CAPACITY}` - Storage size
- `${APP_UID}` / `${APP_GID}` - User/group IDs (default: 1000)

Global variables from `flux/vars/`:
- `${SECRET_DOMAIN}` - Primary domain
- `${CLUSTER_CIDR}` - Cluster network CIDR
- Other cluster-wide settings

## Domain & Routes

- Internal apps: `{app}.nikola.wtf` via `internal` gateway in `kube-system`
- External apps: Use `external` gateway or LoadBalancer

## Important Notes

- Never commit unencrypted secrets - use `*.sops.yaml` files
- All apps should include volsync template for backups
- Use YAML anchors (`&app`, `&port`) for DRY configuration
- Timezone is `Europe/Belgrade`
- App-template chart is at v4.6.2 (centralized in flux-system)
- Standard dependencies: `rook-ceph-cluster`, `volsync`, `external-secrets-stores`

## Useful Commands

```bash
task kubernetes:kubeconform  # Validate manifests
task configure               # Render templates
flux reconcile ks {app}      # Force reconciliation
flux get ks -A               # List all Kustomizations
```
