#!/usr/bin/env -S just --justfile

set unstable
set quiet
set shell := ['bash', '-euo', 'pipefail', '-c']

[group: 'Bootstrap']
mod bootstrap '.just/bootstrap.just'
[group: 'Kube']
mod kube '.just/kubernetes.just'
[group: 'Talos']
mod talos '.just/talos.just'
[group: 'Rook']
mod rook '.just/rook.just'
[group: 'VolSync']
mod volsync '.just/volsync.just'

[private]
default:
    @just --list

# Structured log line (used by recipes)
[private]
log lvl msg *args:
    gum log -t rfc3339 -s -l "{{ lvl }}" "{{ msg }}" {{ args }}

# Render a template: minijinja then resolve ref+op:// via vals. The 1Password
# service-account token is fetched inline here (and in the bootstrap module) so
# it's only pulled when a recipe that actually resolves op:// refs runs.
[private]
template file *args:
    minijinja-cli "{{ file }}" {{ args }} \
        | OP_SERVICE_ACCOUNT_TOKEN="$(op read --account my.1password.eu "op://homecluster/op service token/token")" vals eval -f -
