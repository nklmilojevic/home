set unstable
set quiet
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

resources := source_directory() / 'resources/volsync'

[private]
default:
    @just --list volsync

# Unlock all restic source repos
# Declarative path: the operator runs `restic unlock` at the start of each
# source's NEXT sync. Nothing happens until that sync runs, and a repo whose
# lock is blocking a RESTORE is not covered — ReplicationDestination has no
# unlock field. Use `unlock-repo` for either of those cases.
[doc('Unlock all restic repos (takes effect on each app next sync)')]
[script]
unlock:
    kubectl get replicationsources -A --no-headers -o=jsonpath='{range .items[*]}{.metadata.namespace},{.metadata.name}{"\n"}{end}' \
        | while IFS=, read -r ns name; do
            kubectl -n "$ns" patch --field-manager=flux-client-side-apply replicationsources "$name" \
                --type merge --patch "{\"spec\":{\"restic\":{\"unlock\":\"$(date +%s)\"}}}"
        done

# Unlock one app's restic repo now [ns] [app] [repo=r2] [remove_all=0]
# Runs restic directly against the repo instead of waiting for a sync.
# repo picks which of the app's two repos to act on: r2 (offsite) or local
# (the NFS-backed gateway). The default removes only STALE locks and is safe
# to run any time. Pass remove_all=1 to also remove locks held by a RUNNING
# mover — only do that once you have confirmed no volsync-src/dst job for the
# app is alive.
[doc('Unlock a single app restic repo now (remove_all=1 also drops live locks)')]
[script]
unlock-repo ns app repo='r2' remove_all='0':
    export NS="{{ ns }}" APP="{{ app }}" REPO="{{ repo }}" REMOVE_ALL="{{ remove_all }}"
    job="volsync-unlock-${APP}-${REPO}"
    minijinja-cli "{{ resources }}/unlock.yaml.j2" | kubectl apply --server-side -f -
    until kubectl -n "${NS}" get "job/${job}" &>/dev/null; do sleep 5; done
    status=0
    kubectl -n "${NS}" wait "job/${job}" --for=condition=complete --timeout=10m || status=1
    stern -n "${NS}" "job/${job}" --no-follow
    kubectl -n "${NS}" delete "job/${job}"
    exit "${status}"

# List snapshots for an app [ns] [app] [repo=r2]
[doc('List VolSync snapshots for an app (repo: r2 or local)')]
[script]
list ns app repo='r2':
    export NS="{{ ns }}" APP="{{ app }}" REPO="{{ repo }}"
    job="list-snapshots-${APP}-${REPO}"
    minijinja-cli "{{ resources }}/list-snapshots.yaml.j2" | kubectl apply --server-side -f -
    until kubectl -n "${NS}" get "job/${job}" &>/dev/null; do sleep 5; done
    kubectl -n "${NS}" wait "job/${job}" --for=condition=complete --timeout=5m
    stern -n "${NS}" "job/${job}" --no-follow
    kubectl -n "${NS}" delete "job/${job}"

# Snapshot an app now [ns] [app] [repo=r2]
[doc('Trigger a VolSync snapshot for an app (repo: r2 or local)')]
[script]
snapshot ns app repo='r2':
    kubectl -n "{{ ns }}" patch replicationsources "{{ app }}-{{ repo }}" --type merge \
        -p "{\"spec\":{\"trigger\":{\"manual\":\"$(date +%s)\"}}}"
    until kubectl -n "{{ ns }}" get "job/volsync-src-{{ app }}-{{ repo }}" &>/dev/null; do sleep 5; done
    kubectl -n "{{ ns }}" wait "job/volsync-src-{{ app }}-{{ repo }}" --for=condition=complete --timeout=120m

# Restore an app from VolSync [ns] [app] [previous=0]
[confirm('This scales the app down and restores its PVC. Continue? [y/N]')]
[doc('Restore an app PVC from VolSync')]
[script]
restore ns app previous='0':
    export NS="{{ ns }}" APP="{{ app }}" PREVIOUS="{{ previous }}"
    controller="$(kubectl -n "${NS}" get deployment "${APP}" &>/dev/null && echo deployment || echo statefulset)"
    export CLAIM="$(kubectl -n "${NS}" get "replicationsources/${APP}-r2" -o=jsonpath='{.spec.sourcePVC}')"
    # accessModes comes from the target PVC: the volsync Component sets it on the
    # bootstrap ReplicationDestination, never on the ReplicationSource, so reading
    # it from the source renders `accessModes: null` and the CRD rejects the apply.
    export ACCESS_MODES="$(kubectl -n "${NS}" get "pvc/${CLAIM}" -o=jsonpath='{.spec.accessModes}')"
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
