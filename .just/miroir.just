set unstable
set quiet
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

root := justfile_directory()
legs := root / '.just/resources/miroir/legs.py'

[private]
default:
    @just --list miroir

# Leg distribution, volumes at risk, and stuck DRBD connections
[doc('Show replica placement and health')]
status:
    python3 {{ legs }} status

# Add a diskful leg on TO for volumes that have one on FROM. Volumes whose pod
# runs on FROM are skipped: that node is DRBD Primary with the disk open, so
# demoting it later would isolate the only up-to-date copy. Move the pod first.
[doc('Promote TO as a diskful leg for volumes on FROM')]
promote from to n='5':
    python3 {{ legs }} promote {{ from }} {{ to }} {{ n }}

# Remove FROM's diskful leg wherever both survivors are UpToDate, re-adding
# FROM as a diskless tie-breaker. Run repeatedly as syncs complete.
[doc('Demote FROM to a tie-breaker where syncs have finished')]
demote from:
    python3 {{ legs }} demote {{ from }}

# A demote can leave the LV behind, which blocks the tie-breaker config with
# "Can not drop the bitmap when both sides have a disk".
[doc('Clear legs stranded on NODE after a demote')]
fixup node:
    python3 {{ legs }} fixup {{ node }}

# Changing a replica's nodeID leaves peers with stale config, so the connection
# never forms and volumes report a phantom Degraded.
[doc('Re-establish stuck DRBD connections between two nodes')]
relink a b:
    python3 {{ legs }} relink {{ a }} {{ b }}

# LVs left behind by an earlier drain. They carry stale DRBD metadata, so a
# later promote fails with "Can only attach to the data we lost last".
[doc('List orphan LVs on NODE (add --delete to remove)')]
[script]
orphans node *args:
    python3 {{ legs }} orphans {{ node }} {{ args }}

# Full drain of one node, run in small batches. Deliberately not a single
# recipe that walks away: each round needs the syncs watched, and running it
# alongside a backup wave overloads the mover node.
[doc('Drain FROM onto TO in one batch, then report')]
[script]
drain from to n='5':
    python3 {{ legs }} promote {{ from }} {{ to }} {{ n }}
    echo "-- wait for syncs, then: just miroir demote {{ from }}"
    python3 {{ legs }} status

# Ceph replicated to all three OSDs, so a snapshot clone always seeded locally.
# At two legs a clone gets one leg on a node without the source data and DRBD
# fills it over the network first -- a full copy of every volume on every
# backup, which drove icarus002's etcd WAL fsync from 8ms to 164ms on
# 2026-08-31. A third leg removes the copy.
#
# Serialized and mover-aware on purpose: 57 simultaneous resyncs is the burst
# this exists to avoid. Idempotent -- rerun to continue where it stopped.
#
# Widen two-leg volumes to a third diskful leg, one at a time
widen n='5':
    @python3 {{ legs }} widen {{ n }}
