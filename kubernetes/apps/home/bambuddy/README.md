# Bambuddy + headless Bambu Studio

- UI: <https://bambuddy.nikola.wtf> (internal gateway).
- Bambuddy IoT interface: `10.50.0.243/24`, MAC `02:50:00:00:00:f3`.
- Bambu Studio API: `http://bambuddy-slicer.home.svc.cluster.local:3000`.
  ClusterIP only; deliberately no HTTPRoute or LoadBalancer for the slicer.
- Both images are pinned to the matching stable Bambuddy 1.2.5.5 release.
  Bambu Studio runs in a separate amd64-only pod; no desktop, GPU, Docker
  socket, or privileged container is needed.

## First-time setup

1. Commit/push the manifests and run `just kube reconcile`. Wait for the
   `home/bambuddy` Flux Kustomization and HelmRelease to become ready.
2. Open the UI and enable authentication/create the administrator account
   before adding the printer. Internal routing alone is not authentication.
3. Add the P1S using the existing **bambu-p1s** 1Password item:
   `PRINTER_IP`, `SERIAL_NUMBER`, and `ACCESS_CODE`. These are entered once in
   the UI and stored in Bambuddy's database, not in Git or a redundant
   ExternalSecret. Do not change printer firmware or LAN mode without
   checking the impact on Bambu Handy/cloud and existing HA controls.
4. In **Settings → Workflow → Slicer**, select **Bambu Studio**, enable
   **Use Slicer API**, and set the Bambu Studio sidecar URL to
   `http://bambuddy-slicer.home.svc.cluster.local:3000`.
   Port 3001 in upstream Compose examples is a host-port mapping; the
   container and this Kubernetes Service use port **3000**.
5. Import your Bambu Studio preset bundle if desired. Upload a small STL,
   choose the P1S/nozzle, process, and filament, and run **Slice**. Check the
   generated file before printing. Slicing is safe to test without starting
   a print; a healthy HTTP endpoint alone does not prove slicing works.
6. Start the first print manually only after checking the build plate and
   filament. Keep household notifications and smart-plug automations in HA
   to avoid duplicate control.

## Storage and operations

`bambuddy-config` is a 20Gi miroir PVC for `/app/data`, including the database,
printer configuration and print archive. The shared kopiur components provide
hourly local and nightly R2 backups with the standard hashed schedules.
Treat backups as sensitive because the database includes printer credentials.

Logs, temporary files, and the slicer's `/app/data` work directory are
`emptyDir` volumes. Slice inputs/outputs in that work directory are scratch;
completed results are saved to Bambuddy's persistent library. Pod replacement
can interrupt an active slice. Slicer scratch is capped at 10Gi; its memory
limit is 4Gi, which may need increasing for particularly complex models.

Automatic discovery/virtual-printer emulation is not required for the
browser-to-printer workflow. Add the P1S by IP. The dedicated IoT interface
provides direct printer access; the slicer needs no IoT interface.

Upstream main-branch documentation can describe features newer than the
pinned stable release. Keep the application and slicer images compatible
when updating, and verify an actual slice after upgrades.

References:

- <https://wiki.bambuddy.cool/features/slicer-api/>
- <https://github.com/maziggy/bambuddy/blob/v1.2.5.5/slicer-api/docker-compose.yml>
