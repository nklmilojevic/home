#!/usr/bin/env python3
"""Move miroir diskful replica legs between nodes, and repair the states that
stops in.

miroir has no rebalancer: MiroirVolume.spec.replicas is an explicit placement
list and nothing moves it after provisioning (autoEvictAfter only rebuilds legs
off a node it considers dead). Draining a node is therefore a manual role swap,
which this script automates along with the four failure modes it runs into.

Subcommands:
  status                     leg distribution, at-risk volumes, stuck links
  promote FROM TO [N]        add a diskful leg on TO for up to N volumes that
                             have one on FROM (skips volumes whose pod runs on
                             FROM -- see the note in drain())
  demote FROM                remove FROM's leg wherever the two survivors are
                             both UpToDate, re-adding FROM as a tie-breaker
  fixup NODE                 detach + lvremove legs stranded on NODE after a
                             demote, then reconnect peers that gave up
  relink A B                 re-establish stuck A<->B connections
  orphans NODE [--delete]    LVs on NODE that no volume claims as diskful
  widen [N]                  give up to N two-leg volumes a third diskful leg,
                             one at a time, waiting for each resync to finish

Every replica this script adds gets an explicit nodeID. Omitting it defaults to
0, which collides with an existing implicit-0 replica; because drbdadm parses
every .res in drbd.d, one such collision makes every DRBD command fail on every
node and the whole fleet reports Degraded.
"""
import json
import subprocess
import sys
import time

NODES = ("icarus001", "icarus002", "icarus003")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def kget(args, retries=6):
    """kubectl get -o json, retried: the apiserver drops requests when a node's
    etcd disk stalls, and a half-finished batch is worse than a slow one."""
    for _ in range(retries):
        r = sh(f"kubectl get {args} -o json")
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            try:
                return json.loads(r.stdout)
            except ValueError:
                pass
        time.sleep(3)
    raise SystemExit(f"could not read: kubectl get {args}")


def volumes():
    # len(replicas) < 2 filters out kopiur staging clones, which are
    # single-replica miroir-local volumes and never need draining.
    return [d for d in kget("miroirvolume")["items"] if len(d["spec"]["replicas"]) > 1]


def agents():
    return {
        p["spec"]["nodeName"]: p["metadata"]["name"]
        for p in kget("pods -n miroir-system")["items"]
        if "agent" in p["metadata"]["name"] and p["status"]["phase"] == "Running"
    }


def addresses(vols):
    """Node -> replication address, taken from existing replica entries rather
    than hardcoded, so this keeps working if the network is renumbered."""
    out = {}
    for d in vols:
        for r in d["spec"]["replicas"]:
            if r.get("address"):
                out[r["node"]] = r["address"]
    return out


def pod_nodes():
    """(namespace, claim) -> node running the pod that mounts it."""
    out = {}
    for p in kget("pods -A")["items"]:
        node = p["spec"].get("nodeName")
        for v in p["spec"].get("volumes") or []:
            claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
            if claim:
                out[(p["metadata"]["namespace"], claim)] = node
    return out


def claims():
    """volumeName -> (namespace, claim)"""
    return {
        p["spec"]["volumeName"]: (p["metadata"]["namespace"], p["metadata"]["name"])
        for p in kget("pvc -A")["items"]
        if p["spec"].get("volumeName")
    }


def diskful(d, node):
    return any(r["node"] == node and not r.get("diskless") for r in d["spec"]["replicas"])


def disk_states(d):
    return {k: v.get("diskState") for k, v in (d.get("status", {}).get("perNode") or {}).items()}


def free_node_id(replicas):
    used = {(r.get("nodeID") if r.get("nodeID") is not None else 0) for r in replicas}
    return next(i for i in range(0, 8) if i not in used)


def patch(name, replicas):
    body = json.dumps({"spec": {"replicas": replicas}})
    return "patched" in sh(f"kubectl patch miroirvolume {name} --type=merge -p '{body}'").stdout


def status():
    vols = volumes()
    per_node, pairs, at_risk, syncing = {}, {}, [], []
    cl = claims()
    for d in vols:
        legs = sorted(r["node"] for r in d["spec"]["replicas"] if not r.get("diskless"))
        for n in legs:
            per_node[n] = per_node.get(n, 0) + 1
        pairs["+".join(x[-3:] for x in legs)] = pairs.get("+".join(x[-3:] for x in legs), 0) + 1
        st = disk_states(d)
        good = [k for k, v in st.items() if v == "UpToDate"]
        label = "/".join(cl.get(d["metadata"]["name"], ("", d["metadata"]["name"])))
        if len(good) < 2:
            at_risk.append((label, st))
        elif d.get("status", {}).get("phase") != "Ready":
            syncing.append((label, st))
    print(f"  volumes: {len(vols)}")
    for n in NODES:
        print(f"   {n}: {per_node.get(n, 0)} diskful legs")
    print(f"   pairs: {pairs}")
    print(f"  syncing / not Ready but safe: {len(syncing)}")
    for s in syncing:
        print(f"   {s[0][:46]:46} {s[1]}")
    print(f"  AT RISK (<2 UpToDate copies): {len(at_risk)}")
    for s in at_risk:
        print(f"   {s[0][:46]:46} {s[1]}")
    ag = agents()
    for node, pod in sorted(ag.items()):
        out = sh(f"kubectl exec -n miroir-system {pod} -c agent -- drbdadm status").stdout
        stuck = out.count("connection:Connecting") + out.count("connection:StandAlone")
        if stuck:
            print(f"  {node}: {stuck} stuck DRBD connections -- see `just miroir relink`")


def promote(src, dst, limit):
    vols = volumes()
    addr = addresses(vols)
    pods = pod_nodes()
    cl = claims()
    todo = []
    for d in vols:
        if not diskful(d, src) or diskful(d, dst):
            continue
        if d.get("status", {}).get("phase") != "Ready":
            continue
        key = cl.get(d["metadata"]["name"])
        # A volume whose pod runs on src has DRBD role:Primary there with the
        # disk open. Demoting that node later isolates the only up-to-date copy
        # and leaves the others Outdated, so refuse it here and let the operator
        # move the pod first.
        if key and pods.get(key) == src:
            print(f"   SKIP {('/'.join(key))[:46]}: pod runs on {src}, move it first")
            continue
        todo.append(d["metadata"]["name"])
    batch = todo[: int(limit)]
    print(f"  candidates={len(todo)} promoting={len(batch)}")
    for name in batch:
        d = kget(f"miroirvolume {name}")
        reps = [dict(r) for r in d["spec"]["replicas"]]
        existing = [r for r in reps if r["node"] == dst]
        if existing:
            for r in reps:
                if r["node"] == dst:
                    r.pop("diskless", None)
                    r["backend"] = "lvmthin"
                    r["pool"] = "nvme"
                    # fullSync skips the day0 GI seed so DRBD full-syncs this
                    # leg from an existing replica instead of assuming parity.
                    r["fullSync"] = True
                    if r.get("nodeID") is None:
                        r["nodeID"] = free_node_id([x for x in reps if x["node"] != dst])
        else:
            reps.append({
                "address": addr.get(dst), "node": dst, "backend": "lvmthin",
                "pool": "nvme", "fullSync": True, "nodeID": free_node_id(reps),
            })
        print(f"   promote {name[:30]}: {'ok' if patch(name, reps) else 'FAIL'}")
        time.sleep(1)


def demote(src):
    vols = volumes()
    addr = addresses(vols)
    ready, waiting = [], []
    for d in vols:
        if not diskful(d, src):
            continue
        others = [n for n in NODES if n != src]
        if not all(diskful(d, n) for n in others):
            continue
        st = disk_states(d)
        # Safe once BOTH survivors are UpToDate. src may be Outdated -- it is
        # leaving anyway.
        if all(st.get(n) == "UpToDate" for n in others):
            ready.append(d["metadata"]["name"])
        else:
            waiting.append((d["metadata"]["name"], st))
    print(f"  ready={len(ready)} still syncing={len(waiting)}")
    for name, st in waiting:
        print(f"   syncing {name[:30]} {st}")
    for name in ready:
        d = kget(f"miroirvolume {name}")
        keep = [dict(r) for r in d["spec"]["replicas"] if r["node"] != src]
        for r in keep:
            r.pop("fullSync", None)
        if not patch(name, keep):
            print(f"   {name[:30]} drop FAILED")
            continue
        # Removing then re-adding is required: a completed replica's pool is
        # immutable, so converting diskful -> diskless in place is rejected.
        time.sleep(6)
        d = kget(f"miroirvolume {name}")
        reps = [dict(r) for r in d["spec"]["replicas"]]
        reps.append({"address": addr.get(src), "node": src, "diskless": True,
                     "nodeID": free_node_id(reps)})
        print(f"   demoted {name[:30]}: {'ok' if patch(name, reps) else 'RE-ADD FAILED'}")
        time.sleep(2)


def fixup(node):
    """A demote can leave the LV behind on the old node. DRBD then refuses the
    tie-breaker config with 'Can not drop the bitmap when both sides have a
    disk' and the peer may give up into StandAlone."""
    ag = agents()
    pod = ag[node]
    lvs = {
        l.split()[0]
        for l in sh(f"kubectl exec -n miroir-system {pod} -c agent -- "
                    f"lvs --noheadings -o lv_name vg-miroir-nvme").stdout.splitlines()
        if l.strip()
    }
    stranded = [
        d["metadata"]["name"] for d in volumes()
        if any(r["node"] == node and r.get("diskless") for r in d["spec"]["replicas"])
        and d["metadata"]["name"] in lvs
    ]
    print(f"  legs stranded on {node}: {len(stranded)}")
    for name in stranded:
        sh(f"kubectl exec -n miroir-system {pod} -c agent -- drbdadm detach {name}")
        time.sleep(2)
        r = sh(f"kubectl exec -n miroir-system {pod} -c agent -- "
               f"lvremove -f vg-miroir-nvme/{name}")
        for peer, peer_pod in ag.items():
            if peer == node:
                continue
            out = sh(f"kubectl exec -n miroir-system {peer_pod} -c agent -- "
                     f"drbdadm status {name}").stdout.replace("\n", " ")
            if f"{node} connection:StandAlone" in out:
                sh(f"kubectl exec -n miroir-system {peer_pod} -c agent -- "
                   f"drbdadm connect {name}:{node}")
        ok = "successfully removed" in r.stdout
        print(f"   {name[:30]} lvremove={'ok' if ok else 'skipped'}")
        time.sleep(1)


def relink(a, b):
    """Changing a replica's nodeID leaves peers with stale config, so the
    connection never forms and the volume reports a phantom Degraded."""
    ag = agents()
    pa, pb = ag[a], ag[b]
    fixed = 0
    for d in volumes():
        name = d["metadata"]["name"]
        out = sh(f"kubectl exec -n miroir-system {pa} -c agent -- "
                 f"drbdadm status {name}").stdout.replace("\n", " ")
        if f"{b} connection:Connecting" not in out and f"{b} connection:StandAlone" not in out:
            continue
        for pod in (pb, pa):
            sh(f"kubectl exec -n miroir-system {pod} -c agent -- drbdadm adjust {name}")
        sh(f"kubectl exec -n miroir-system {pb} -c agent -- drbdadm connect {name}:{a}")
        sh(f"kubectl exec -n miroir-system {pa} -c agent -- drbdadm connect {name}:{b}")
        fixed += 1
        print(f"   relinked {name[:30]}")
        time.sleep(1)
    print(f"  relinked {fixed} volumes between {a} and {b}")


def orphans(node, delete=False):
    """LVs left behind when a node was drained. They hold stale DRBD metadata,
    so a later promote fails with 'Can only attach to the data we lost last',
    and they inflate the node's reported allocation."""
    ag = agents()
    pod = ag[node]
    lvs = [
        l.split()[0]
        for l in sh(f"kubectl exec -n miroir-system {pod} -c agent -- "
                    f"lvs --noheadings -o lv_name vg-miroir-nvme").stdout.splitlines()
        if l.strip()
    ]
    vols = volumes()
    keep = {d["metadata"]["name"] for d in vols if diskful(d, node)}
    by_name = {d["metadata"]["name"]: d for d in vols}
    snap_live = {s["metadata"]["name"] for s in kget("volumesnapshotcontent").get("items", [])}
    pvc_orphans, snap_orphans, unsafe = [], [], []
    for lv in lvs:
        if lv.startswith("miroir-snapshot-"):
            if not snap_live:
                snap_orphans.append(lv)
            continue
        if not lv.startswith("pvc-") or lv in keep:
            continue
        d = by_name.get(lv)
        if d is None:
            pvc_orphans.append(lv)          # volume itself is gone
            continue
        st = disk_states(d)
        good = [k for k, v in st.items() if v == "UpToDate" and k != node]
        # Only safe if the volume is healthy on OTHER nodes.
        (pvc_orphans if d.get("status", {}).get("phase") == "Ready" and len(good) >= 2
         else unsafe).append(lv)
    print(f"  {node}: {len(lvs)} LVs, {len(keep)} claimed as diskful")
    print(f"   orphan pvc-*:          {len(pvc_orphans)}")
    print(f"   orphan snapshot LVs:   {len(snap_orphans)} (live VolumeSnapshotContents: {len(snap_live)})")
    print(f"   NOT safe to remove:    {len(unsafe)}")
    for u in unsafe:
        print(f"    {u}")
    if not delete:
        print("  dry run -- pass --delete to remove the safe ones")
        return
    ok = 0
    for lv in pvc_orphans + snap_orphans:
        r = sh(f"kubectl exec -n miroir-system {pod} -c agent -- lvremove -f vg-miroir-nvme/{lv}")
        ok += "successfully removed" in r.stdout
        time.sleep(0.4)
    print(f"  removed {ok} of {len(pvc_orphans) + len(snap_orphans)}")



# Ceph replicated every volume to all three OSDs, so a pod on any node read
# locally and a snapshot clone seeded locally. miroir at 2 legs does not: a
# clone always gets one leg on a node without the source data, which DRBD then
# fills over the network before the mover can mount it. That is a full copy of
# every volume, every backup, and it is what drove icarus002's etcd WAL p99
# fsync from 8ms to 164ms on 2026-08-31. A third leg removes the copy entirely.
#
# Serialized deliberately: 57 volumes resyncing at once is precisely the burst
# this is meant to avoid. One leg at a time, each waited to UpToDate, and it
# holds off while a kopiur mover is running so a backup never races a resync.
# Idempotent -- rerun it to continue where it stopped.
# etcd's WAL shares the same NVMe as the miroir pool, so a resync lands directly
# on top of it: widening pushed icarus002's p99 fsync from 8ms to 164ms on
# 2026-08-31, which produced two leader elections and a kustomization status
# write failing with "etcdserver: request timed out". The k8s-stack ships
# etcdHighFsyncDurations but its threshold sits above that, so nothing fired.
# Gate each volume on the real number instead of trusting the alert.
ETCD_FSYNC_LIMIT_MS = 50.0
VM_URL = "https://vm.nikola.wtf/prometheus/api/v1/query"
FSYNC_Q = ("histogram_quantile(0.99, sum(rate("
           "etcd_disk_wal_fsync_duration_seconds_bucket[2m])) by (instance,le))")


def _etcd_fsync_max():
    """Worst p99 WAL fsync across members, in ms. None if VM is unreachable --
    the guard is advisory, a monitoring outage must not block a migration."""
    r = subprocess.run(["curl", "-sS", "--max-time", "10", "-G", VM_URL,
                        "--data-urlencode", f"query={FSYNC_Q}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        res = json.loads(r.stdout).get("data", {}).get("result", [])
    except json.JSONDecodeError:
        return None
    vals = []
    for x in res:
        try:
            vals.append(float(x["value"][1]) * 1000.0)
        except (KeyError, ValueError, TypeError):
            continue
    return max(vals) if vals else None


def _wait_for_etcd(polls=45):
    for _ in range(polls):
        ms = _etcd_fsync_max()
        if ms is None or ms <= ETCD_FSYNC_LIMIT_MS:
            return ms
        print(f"   waiting: etcd p99 fsync {ms:.0f}ms > {ETCD_FSYNC_LIMIT_MS:.0f}ms")
        time.sleep(20)
    return _etcd_fsync_max()


SKIP_CLAIM = ("-src", "kopia-cache", "-populate-", "prime-")


def _movers_running():
    out = sh("kubectl get pods -A -l app.kubernetes.io/managed-by=kopiur "
             "--no-headers 2>/dev/null || true").stdout
    return sum(1 for l in out.splitlines()
               if l.strip() and (" Running " in l or " Pending " in l))


def widen(limit=5):
    vols = volumes()
    addr = addresses(vols)
    cl = claims()
    todo = []
    for d in vols:
        reps = d["spec"]["replicas"]
        df = [r["node"] for r in reps if not r.get("diskless")]
        if len(df) != 2:
            continue                      # 1 leg = miroir-local; 3 = already done
        if d.get("status", {}).get("phase") != "Ready":
            continue
        st = disk_states(d)
        if [v for v in st.values() if v != "UpToDate"]:
            continue                      # never widen a volume that is not clean
        label = "/".join(cl.get(d["metadata"]["name"], ("", d["metadata"]["name"])))
        if any(k in label for k in SKIP_CLAIM):
            continue                      # ephemeral staging clones and caches
        missing = [n for n in NODES if n not in df]
        if len(missing) != 1:
            continue
        todo.append((d["metadata"]["name"], label, missing[0]))

    print(f"  two-leg volumes eligible: {len(todo)}  (widening up to {limit})")
    done = 0
    for name, label, dst in todo:
        if done >= int(limit):
            break
        for _ in range(60):
            if _movers_running() == 0:
                break
            print("   waiting: kopiur movers running")
            time.sleep(20)
        ms = _wait_for_etcd()
        if ms is not None:
            print(f"   etcd p99 fsync {ms:.0f}ms")
        d = kget(f"miroirvolume {name}")
        reps = [dict(r) for r in d["spec"]["replicas"]]
        if any(r["node"] == dst and not r.get("diskless") for r in reps):
            continue
        existing = [r for r in reps if r["node"] == dst]
        if existing:
            for r in reps:
                if r["node"] == dst:
                    r.pop("diskless", None)
                    r["backend"] = "lvmthin"
                    r["pool"] = "nvme"
                    r["fullSync"] = True
                    if r.get("nodeID") is None:
                        r["nodeID"] = free_node_id([x for x in reps if x["node"] != dst])
        else:
            reps.append({
                "address": addr.get(dst), "node": dst, "backend": "lvmthin",
                "pool": "nvme", "fullSync": True, "nodeID": free_node_id(reps),
            })
        ok = patch(name, reps)
        print(f"   widen {label[:44]:44} +{dst}: {'ok' if ok else 'FAIL'}")
        if not ok:
            continue
        done += 1
        # wait for the new leg to catch up before touching the next volume
        for _ in range(180):
            time.sleep(10)
            st = disk_states(kget(f"miroirvolume {name}"))
            good = [k for k, v in st.items() if v == "UpToDate"]
            if len(good) >= 3:
                print(f"     synced: {st}")
                break
            bad = {k: v for k, v in st.items() if v != "UpToDate"}
            if bad:
                print(f"     syncing: {bad}")
        else:
            print("     TIMEOUT waiting for UpToDate -- stopping here")
            break
    print(f"  widened: {done}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "status":
        status()
    elif cmd == "promote":
        promote(args[0], args[1], args[2] if len(args) > 2 else 5)
    elif cmd == "demote":
        demote(args[0])
    elif cmd == "fixup":
        fixup(args[0])
    elif cmd == "relink":
        relink(args[0], args[1])
    elif cmd == "orphans":
        orphans(args[0], "--delete" in args)
    elif cmd == "widen":
        widen(args[0] if args else 5)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
