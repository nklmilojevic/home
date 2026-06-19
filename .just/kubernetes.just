set unstable := true
set quiet := true
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

root := justfile_directory()

[private]
default:
    @just --list kube

# Force Flux to pull in changes from Git
[doc('Reconcile the cluster from Git')]
reconcile:
    flux --namespace flux-system reconcile kustomization cluster-apps --with-source

# Apply a local Flux Kustomization [ns] [app]
[doc('Apply a local Flux Kustomization')]
[script]
apply-ks ns app:
    flux build ks {{ app }} \
        --kustomization-file "{{ root }}/kubernetes/apps/{{ ns }}/{{ app }}/ks.yaml" \
        --path "{{ root }}/kubernetes/apps/{{ ns }}/{{ app }}" \
        | yq -y 'if .kind == "Kustomization" then .metadata.namespace = "{{ ns }}" else . end' \
        | kubectl apply --server-side --field-manager=kustomize-controller -f -

# Gather common resources, useful when asking for support
[doc('Dump common cluster resources')]
[script]
resources:
    for r in nodes gitrepositories kustomizations helmrepositories helmreleases \
             certificates certificaterequests httproutes pods; do
        echo "# $r"; kubectl get "$r" -A
    done

# Delete pods in Failed, Pending, or Succeeded phase
[doc('Prune Failed/Pending/Succeeded pods')]
[script]
prune-pods:
    for phase in Failed Pending Succeeded; do
        kubectl delete pods -A --field-selector status.phase="$phase" --ignore-not-found=true
    done

# Force a sync of all resources of a kind (hr|ks|gitrepo|ocirepo|es)
[arg('resource', pattern='hr|ks|gitrepo|ocirepo|es')]
[doc('Force-sync all resources of a kind')]
[script]
sync resource:
    kubectl get {{ resource }} --no-headers -A | while read -r ns name _; do
        kubectl -n "$ns" annotate --field-manager flux-client-side-apply --overwrite {{ resource }} "$name" \
            "reconcile.fluxcd.io/requestedAt=$(date +%s)"
    done

# Open a shell into a PVC
[doc('Browse a PVC')]
browse-pvc ns claim:
    kubectl run -n {{ ns }} browse-pvc-{{ claim }} -it --rm --restart=Never \
        --image=mirror.gcr.io/alpine:latest \
        --overrides='{"spec":{"containers":[{"name":"browse","image":"mirror.gcr.io/alpine:latest","stdin":true,"tty":true,"command":["/bin/sh"],"workingDir":"/mnt","volumeMounts":[{"mountPath":"/mnt","name":"data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"{{ claim }}"}}]}}'
