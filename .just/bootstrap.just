set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

root := justfile_directory()
talos_dir := root / 'kubernetes/bootstrap/talos'
config := talos_dir / 'talconfig.yaml'
secret := talos_dir / 'talsecret.sops.yaml'
clusterconfig := talos_dir / 'clusterconfig'
helmfile := root / 'kubernetes/bootstrap/helmfile.yaml'

[private]
default:
    @just --list bootstrap

# NOTE: the Flux/operator bootstrap is built in the bootstrap-restructure step.
# This brings up Talos + the pre-Flux Helm releases (cilium/coredns/spegel).

# Bootstrap Talos and the base Helm releases
[confirm('Bootstrap the Talos cluster? [y/N]')]
[doc('Bootstrap Talos + base Helm releases')]
[script]
talos:
    if [[ ! -f "{{ secret }}" ]]; then
        just log info "Generating Talos secrets"
        talhelper gensecret > "{{ secret }}"
        sops --encrypt --in-place "{{ secret }}"
    fi
    just log info "Generating Talos config"
    talhelper genconfig --config-file "{{ config }}" --secret-file "{{ secret }}" --out-dir "{{ clusterconfig }}"
    just log info "Applying Talos config to nodes (insecure)"
    talhelper gencommand apply --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" --extra-flags="--insecure" | bash
    just log info "Bootstrapping Kubernetes (retry until ready)"
    until talhelper gencommand bootstrap --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" | bash; do sleep 10; done
    just log info "Fetching kubeconfig"
    until talhelper gencommand kubeconfig --config-file "{{ config }}" --out-dir "{{ clusterconfig }}" --extra-flags="{{ root }} --force" | bash; do sleep 10; done
    just bootstrap apps
    talosctl health --server=false

# Apply the pre-Flux Helm releases via helmfile
[private]
[script]
apps:
    just log info "Waiting for nodes to register"
    until kubectl wait --for=condition=Ready=False nodes --all --timeout=600s; do sleep 10; done
    just log info "Applying base Helm releases"
    helmfile --file "{{ helmfile }}" apply --skip-diff-on-install --suppress-diff
    until kubectl wait --for=condition=Ready nodes --all --timeout=600s; do sleep 10; done
