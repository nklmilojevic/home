set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

root := justfile_directory()
talos_dir := root / 'kubernetes/bootstrap/talos'
config := talos_dir / 'talconfig.yaml'
secret := talos_dir / 'talsecret.sops.yaml'
clusterconfig := talos_dir / 'clusterconfig'
helmfile := root / 'bootstrap/helmfile.yaml'
kustomize_apps := root / 'bootstrap/kustomize/apps'

[private]
default:
    @just --list bootstrap

# Bootstrap a Talos + Kubernetes + Flux cluster end-to-end
[confirm('Bootstrap the cluster end-to-end? [y/N]')]
[doc('Bootstrap Talos + Kubernetes + Flux end-to-end')]
cluster: nodes k8s kubeconfig secrets apps
    @just log info "Bootstrap complete — flux-instance sync is now reconciling the repo"

# Generate + apply Talos config to all nodes (insecure)
[private]
[script]
nodes:
    if [[ ! -f "{{ secret }}" ]]; then
        just log info "Generating Talos secrets"
        talhelper gensecret > "{{ secret }}"
        sops --encrypt --in-place "{{ secret }}"
    fi
    just log info "Generating Talos config"
    talhelper genconfig --config-file "{{ config }}" --secret-file "{{ secret }}" --out-dir "{{ clusterconfig }}"
    just log info "Applying Talos config to nodes (insecure)"
    talhelper gencommand apply --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" --extra-flags="--insecure" | bash

# Bootstrap Kubernetes (etcd) on a controlplane node
[private]
[script]
k8s:
    just log info "Bootstrapping Kubernetes (retry until ready)"
    until talhelper gencommand bootstrap --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" | bash; do sleep 10; done

# Fetch the kubeconfig to the repo root
[private]
[script]
kubeconfig:
    just log info "Fetching kubeconfig"
    until talhelper gencommand kubeconfig --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" --extra-flags="{{ root }} --force" | bash; do sleep 10; done

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
