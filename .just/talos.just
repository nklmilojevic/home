set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

talos_dir := justfile_directory() / 'talos'

[private]
default:
    @just --list talos

# Render a node's Talos machine config (minijinja -> vals -> node patch)
[doc('Render Talos machine config for a node')]
[script]
render-config node:
    export CONTROLPLANE="$(just talos machine-controller {{ node }})"
    export SCHEMATIC="$(just talos schematic-id)"
    talosctl machineconfig patch <(just template "{{ talos_dir }}/machineconfig.yaml.j2") \
        -p @<(just template "{{ talos_dir }}/nodes/{{ node }}.yaml.j2")

# Apply Talos config to a node [node] [args...]
[confirm('Apply Talos config to the node? [y/N]')]
[doc('Apply Talos config to a node')]
[script]
apply-node node *args:
    just talos render-config "{{ node }}" \
        | talosctl --nodes "{{ node }}" apply-config --file /dev/stdin {{ args }}
    talosctl --nodes "{{ node }}" health --wait-timeout=10m --server=false

# Upgrade Talos on a single node [node] [args...]
[confirm('Upgrade Talos on the node? [y/N]')]
[doc('Upgrade Talos on a node')]
[script]
upgrade-node node *args:
    talosctl --nodes "{{ node }}" upgrade --image "$(just talos machine-image)" -m powercycle --timeout=10m {{ args }}
    talosctl --nodes "{{ node }}" health --wait-timeout=10m --server=false

# Upgrade Kubernetes across the cluster [version]
[confirm('Upgrade Kubernetes on the cluster? [y/N]')]
[doc('Upgrade Kubernetes')]
[script]
upgrade-k8s version:
    controller="$(talosctl config info --output json | jq -r '.endpoints[0]')"
    talosctl --nodes "${controller}" upgrade-k8s --to "{{ version }}"

# Reboot a node [node]
[confirm('Reboot the node? [y/N]')]
[doc('Reboot a node')]
reboot-node node:
    talosctl --nodes "{{ node }}" reboot --mode powercycle

# Shutdown a node [node]
[confirm('Shutdown the node? [y/N]')]
[doc('Shutdown a node')]
shutdown-node node *args:
    talosctl --nodes "{{ node }}" shutdown --force {{ args }}

# Reset a node back to maintenance mode (DESTRUCTIVE) [node]
[confirm('This resets the node to maintenance mode and wipes it. Continue? [y/N]')]
[doc('Reset a node to maintenance mode')]
reset-node node *args:
    talosctl --nodes "{{ node }}" reset \
        --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL \
        --graceful=false --reboot {{ args }}

# Resolve the Image Factory schematic ID for the current schematic.yaml.j2
[private]
schematic-id:
    curl -sX POST --data-binary @<(just template "{{ talos_dir }}/schematic.yaml.j2") "https://factory.talos.dev/schematics" | jq -r '.id'

# Is the given node a controlplane? (echoes "true" or empty)
[private]
[script]
machine-controller node:
    type="$(minijinja-cli "{{ talos_dir }}/nodes/{{ node }}.yaml.j2" | yq 'select(.machine) | .machine.type')"
    [[ "${type}" == "controlplane" ]] && echo true || true

# Resolve the installer image (schematic + Talos version) from machineconfig.yaml.j2
[private]
[script]
machine-image:
    export SCHEMATIC="$(just talos schematic-id)"
    minijinja-cli "{{ talos_dir }}/machineconfig.yaml.j2" | yq -e 'select(.machine) | .machine.install.image'
