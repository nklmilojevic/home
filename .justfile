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

# Render a template: minijinja then resolve ref+op:// via vals
[private]
template file *args:
    minijinja-cli "{{ file }}" {{ args }} | vals eval -f -
