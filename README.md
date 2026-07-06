# home

My homelab, as code. Everything I need to run media, home automation, storage and a pile of self-hosted apps - set up so I can rebuild the whole thing from a git clone and a few `just` recipes.

It's a [Talos Linux](https://www.talos.dev/) Kubernetes cluster driven by [Flux](https://fluxcd.io/). I push to `main`, Flux applies it. That's the whole workflow.

[![Talos](https://img.shields.io/badge/Talos-FF7300?style=for-the-badge)](https://www.talos.dev/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![Flux](https://img.shields.io/badge/Flux-5468FF?style=for-the-badge)](https://fluxcd.io/)
[![Renovate](https://img.shields.io/badge/Renovate-1A1F6C?style=for-the-badge&logo=renovatebot&logoColor=white)](https://www.mend.io/renovate/)
[![Nix](https://img.shields.io/badge/Nix-5277C3?style=for-the-badge&logo=nixos&logoColor=white)](https://nixos.org/)

## What's running

Around 40 apps across ~15 namespaces. The parts I actually care about:

- **home** - Home Assistant, Zigbee2MQTT, ESPHome, go2rtc, mosquitto - the whole home automation side
- **media** - Plex, Sonarr, Radarr, SABnzbd, qBittorrent, the usual suspects
- **monitoring** - VictoriaMetrics, VictoriaLogs, Vector, Grafana, gatus
- **misc** - Paperless, Forgejo, n8n, Manyfold, Stirling-PDF and a few others

Networking, storage, secrets and backups all run in-cluster too - see below.

## How it works

- **OS** - Talos Linux. Immutable, API-driven, no SSH to babysit.
- **GitOps** - Flux v2 (Flux Operator + FluxInstance). Git is the source of truth, full stop.
- **Apps** - bjw-s [app-template](https://github.com/bjw-s-labs/helm-charts) charts, one OCIRepository per app, Renovate keeps the versions current.
- **Networking** - Cilium, Envoy Gateway, external-dns, cloudflared, multus. Internal apps land on `*.nikola.wtf`.
- **Storage** - Rook-Ceph for distributed, OpenEBS for local, NFS for shared media.
- **Secrets** - External Secrets + 1Password Connect at runtime, `vals` + `ref+op://` refs for bootstrap. No secrets in git, ever.
- **Backups** - VolSync to Cloudflare R2, wired in as a shared Kustomize Component.
- **Dev shell** - Nix flake. `nix develop` and every tool I need is right there.

Day to day it's all `just`:

```bash
just                          # list every recipe
just kube reconcile           # force a reconcile from git
just talos render-config <node>
just volsync snapshot <ns> <app>
```

## Hardware

### UniFi stack

All UniFi, running across three apps - Network, Protect and Drive.

| Kind    | Device                        |
| ------- | ----------------------------- |
| Gateway | UDM SE                        |
| Switch  | USW Pro Max 24 PoE            |
| Switch  | USW Flex 2.5G 8 PoE           |
| Wi-Fi   | 2x U6 Enterprise IW (in-wall) |
| UPS     | UniFi UPS 2U                  |
| Cameras | G4 Instant, G5 Turret Ultra   |
| NAS     | UNAS Pro                      |

The network's split into a few VLANs - trusted, IoT, guest and the rest.

### Compute

Three mini PCs run the Talos cluster:

- 2x NiPoGi CK10
- 1x Minisforum NAB6 Lite - 32GB DDR4, i5-12450H, 1TB NVMe (that one carries the Ceph OSD)

### Odds and ends

- JetKVM + a KVM switch, for when a node needs hands-on rescue
- SMLight SZLB-06M - PoE Zigbee coordinator

## Cloud bits

Not everything's on the metal:

- **Cloudflare** - DNS, tunnels (cloudflared) and R2 for backups
- **1Password** - every secret, via Connect + External Secrets

## Layout

```
kubernetes/
├── apps/           # apps by namespace
├── components/     # reusable Kustomize Components (volsync)
└── flux/           # root Flux Kustomization + HelmRelease defaults
bootstrap/          # pre-Flux bootstrap (helmfile + kustomize)
talos/              # Talos node config templates
```

Bootstrapping a fresh cluster lives in `bootstrap/` and the `just bootstrap` recipes. Everything after that is Flux.

## License

MIT - see [LICENSE](./LICENSE).

---

Inspired by [onedr0p/cluster-template](https://github.com/onedr0p/cluster-template) and the wider k8s-at-home crowd. Thanks, folks.
