set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

talos_dir := justfile_directory() / 'kubernetes/bootstrap/talos'
config := talos_dir / 'talconfig.yaml'
secret := talos_dir / 'talsecret.sops.yaml'
clusterconfig := talos_dir / 'clusterconfig'

[private]
default:
    @just --list talos

# Generate Talos machine config with talhelper
[doc('Generate Talos config')]
generate-config:
    talhelper genconfig --config-file {{ config }} --secret-file {{ secret }} --out-dir {{ clusterconfig }}

# Apply Talos config to a node [node] [mode=auto]
[confirm('Apply Talos config to the node? [y/N]')]
[doc('Apply Talos config to a node')]
[script]
apply-node node mode='auto':
    cluster="$(yq -r '.clusterName' {{ config }})"
    talosctl --nodes {{ node }} apply-config --mode={{ mode }} --file "{{ clusterconfig }}/${cluster}-{{ node }}.yaml"
    talosctl --nodes {{ node }} health --wait-timeout=10m --server=false

# Upgrade Talos on a single node [node]
[confirm('Upgrade Talos on the node? [y/N]')]
[doc('Upgrade Talos on a node')]
[script]
upgrade-node node:
    just talos down
    schematic="$(kubectl get node {{ node }} -o jsonpath='{.metadata.annotations.extensions\.talos\.dev/schematic}')"
    version="$(yq -r '.talosVersion' {{ config }})"
    talosctl --nodes {{ node }} upgrade --image="factory.talos.dev/installer/${schematic}:${version}" --timeout=10m
    talosctl --nodes {{ node }} health --wait-timeout=10m --server=false
    just talos up

# Upgrade Kubernetes across the cluster
[confirm('Upgrade Kubernetes on the cluster? [y/N]')]
[doc('Upgrade Kubernetes')]
[script]
upgrade-k8s:
    controller="$(talosctl config info --output json | jq -r '.endpoints[]' | shuf -n 1)"
    version="$(yq -r '.kubernetesVersion' {{ config }})"
    talosctl --nodes "${controller}" upgrade-k8s --to "${version}"

# Reset nodes back to maintenance mode (DESTROYS the cluster)
[confirm('This DESTROYS the cluster and resets nodes to maintenance mode. Continue? [y/N]')]
[doc('Reset all nodes to maintenance mode')]
reset:
    talhelper gencommand reset --config-file {{ config }} --out-dir {{ clusterconfig }} \
        --extra-flags="--reboot --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL --graceful=false --wait=false" | bash

[private]
down:
    flux --namespace flux-system suspend kustomization --all

[private]
up:
    flux --namespace flux-system resume kustomization --all
