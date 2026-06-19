set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

resources := source_directory() / 'resources/volsync'

[private]
default:
    @just --list volsync

# Unlock all restic source repos
[doc('Unlock all restic repos')]
[script]
unlock:
    kubectl get replicationsources -A --no-headers -o=jsonpath='{range .items[*]}{.metadata.namespace},{.metadata.name}{"\n"}{end}' \
        | while IFS=, read -r ns name; do
            kubectl -n "$ns" patch --field-manager=flux-client-side-apply replicationsources "$name" \
                --type merge --patch "{\"spec\":{\"restic\":{\"unlock\":\"$(date +%s)\"}}}"
        done

# List snapshots for an app [ns] [app]
[doc('List VolSync snapshots for an app')]
[script]
list ns app:
    export NS="{{ ns }}" APP="{{ app }}"
    job="list-snapshots-${APP}-r2"
    minijinja-cli "{{ resources }}/list-snapshots.yaml.j2" | kubectl apply --server-side -f -
    until kubectl -n "${NS}" get "job/${job}" &>/dev/null; do sleep 5; done
    kubectl -n "${NS}" wait "job/${job}" --for=condition=complete --timeout=5m
    stern -n "${NS}" "job/${job}" --no-follow
    kubectl -n "${NS}" delete "job/${job}"

# Snapshot an app now [ns] [app]
[doc('Trigger a VolSync snapshot for an app')]
[script]
snapshot ns app:
    kubectl -n "{{ ns }}" patch replicationsources "{{ app }}-r2" --type merge \
        -p "{\"spec\":{\"trigger\":{\"manual\":\"$(date +%s)\"}}}"
    until kubectl -n "{{ ns }}" get "job/volsync-src-{{ app }}-r2" &>/dev/null; do sleep 5; done
    kubectl -n "{{ ns }}" wait "job/volsync-src-{{ app }}-r2" --for=condition=complete --timeout=120m

# Restore an app from VolSync [ns] [app] [previous=0]
[confirm('This scales the app down and restores its PVC. Continue? [y/N]')]
[doc('Restore an app PVC from VolSync')]
[script]
restore ns app previous='0':
    export NS="{{ ns }}" APP="{{ app }}" PREVIOUS="{{ previous }}"
    controller="$(kubectl -n "${NS}" get deployment "${APP}" &>/dev/null && echo deployment || echo statefulset)"
    export CLAIM="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.sourcePVC}')"
    export ACCESS_MODES="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.restic.accessModes}')"
    export STORAGE_CLASS_NAME="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.restic.storageClassName}')"
    export PUID="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.restic.moverSecurityContext.runAsUser}')"
    export PGID="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.restic.moverSecurityContext.runAsGroup}')"
    # Suspend (Kustomization now lives in the app namespace)
    flux -n "${NS}" suspend kustomization "${APP}"
    flux -n "${NS}" suspend helmrelease "${APP}"
    kubectl -n "${NS}" scale "${controller}/${APP}" --replicas 0
    kubectl -n "${NS}" wait pod --for=delete --selector="app.kubernetes.io/name=${APP}" --timeout=5m
    # Restore
    minijinja-cli "{{ resources }}/replicationdestination.yaml.j2" | kubectl apply --server-side -f -
    until kubectl -n "${NS}" get "job/volsync-dst-${APP}-manual" &>/dev/null; do sleep 5; done
    kubectl -n "${NS}" wait "job/volsync-dst-${APP}-manual" --for=condition=complete --timeout=120m
    kubectl -n "${NS}" delete replicationdestination "${APP}-manual"
    # Resume
    flux -n "${NS}" resume kustomization "${APP}"
    flux -n "${NS}" resume helmrelease "${APP}"
    flux -n "${NS}" reconcile helmrelease "${APP}" --force
    kubectl -n "${NS}" wait pod --for=condition=ready --selector="app.kubernetes.io/name=${APP}" --timeout=5m
