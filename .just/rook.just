set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

resources := source_directory() / 'resources/rook'

[private]
default:
    @just --list rook

# Wipe all rook-ceph remnants from a disk on a node [node] [disk]
[confirm('This DESTROYS all data on the disk. Continue? [y/N]')]
[doc('Wipe a disk of rook-ceph remnants')]
[script]
wipe-disk node disk:
    export NODE="{{ node }}" DISK="{{ disk }}"
    kubectl wait nodes -l kubernetes.io/hostname="${NODE}" --timeout=0 --for=condition=Ready
    minijinja-cli "{{ resources }}/wipe-rook.yaml.j2" | kubectl apply --server-side -f -
    until kubectl -n default get "job/wipe-rook-${NODE}" &>/dev/null; do sleep 5; done
    kubectl -n default wait "job/wipe-rook-${NODE}" --for=condition=complete --timeout=5m
    stern -n default "job/wipe-rook-${NODE}" --no-follow
    kubectl -n default delete "job/wipe-rook-${NODE}"
