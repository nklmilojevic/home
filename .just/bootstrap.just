set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

root := justfile_directory()
talos_dir := root / 'talos'
talosconfig := root / 'talosconfig'
helmfile := root / 'bootstrap/helmfile.yaml'
kustomize_apps := root / 'bootstrap/kustomize/apps'

[private]
default:
    @just --list bootstrap

# Bootstrap a Talos + Kubernetes + Flux cluster end-to-end
[confirm('Bootstrap the cluster end-to-end? [y/N]')]
[doc('Bootstrap Talos + Kubernetes + Flux end-to-end')]
cluster: nodes talosconfig k8s kubeconfig secrets apps
    @just log info "Bootstrap complete — flux-instance sync is now reconciling the repo"

# Render + apply native Talos config to all nodes (insecure / maintenance mode)
[private]
[script]
nodes:
    just log info "Applying Talos config to nodes (insecure)"
    for file in "{{ talos_dir }}"/nodes/*.yaml.j2; do
        node="$(basename "${file}" .yaml.j2)"
        just log info "Applying config" node "${node}"
        just talos render-config "${node}" \
            | talosctl --nodes "${node}" apply-config --insecure --file /dev/stdin
    done

# Mint a client talosconfig from the 1Password secrets bundle (vals)
[private]
[script]
talosconfig:
    just log info "Generating talosconfig from 1Password"
    talosctl gen config home-kubernetes https://10.40.0.3:6443 \
        --with-secrets <(just template "{{ talos_dir }}/talsecret.yaml.j2") \
        --output-types talosconfig --output "{{ talosconfig }}" --force
    nodes=()
    for file in "{{ talos_dir }}"/nodes/*.yaml.j2; do nodes+=("$(basename "${file}" .yaml.j2)"); done
    talosctl --talosconfig "{{ talosconfig }}" config node "${nodes[@]}"
    talosctl --talosconfig "{{ talosconfig }}" config endpoint "${nodes[@]}"

# Bootstrap Kubernetes (etcd) on a controlplane node
[private]
[script]
k8s:
    node="$(basename "$(ls "{{ talos_dir }}"/nodes/*.yaml.j2 | head -1)" .yaml.j2)"
    just log info "Bootstrapping Kubernetes (retry until ready)" node "${node}"
    until talosctl --nodes "${node}" bootstrap; do sleep 10; done

# Fetch the kubeconfig to the repo root
[private]
[script]
kubeconfig:
    node="$(basename "$(ls "{{ talos_dir }}"/nodes/*.yaml.j2 | head -1)" .yaml.j2)"
    just log info "Fetching kubeconfig"
    until talosctl --nodes "${node}" kubeconfig --force "{{ root }}"; do sleep 10; done

# Inject the 1Password-backed bootstrap secrets (op-credentials, tunnel-id) via vals.
# Requires OP_SERVICE_ACCOUNT_TOKEN in the environment.
[private]
[script]
secrets:
    just log info "Waiting for nodes to register"
    until kubectl wait --for=condition=Ready=False nodes --all --timeout=600s; do sleep 10; done
    just log info "Injecting bootstrap secrets from 1Password (vals)"
    kustomize build "{{ kustomize_apps }}" | vals eval -f - | kubectl apply --server-side --field-manager=kustomize-controller -f -

# Install the pre-Flux Helm releases + Flux Operator/Instance via helmfile
[private]
[script]
apps:
    just log info "Applying base Helm releases (cilium → coredns → spegel → flux-operator → flux-instance)"
    helmfile --file "{{ helmfile }}" sync --hide-notes
    until kubectl wait --for=condition=Ready nodes --all --timeout=600s; do sleep 10; done
