# home

My home setup with all things needed for managing media, home automation and more.

## Setup overview

### Network setup

* [Mikrotik RB4011](https://mikrotik.com/product/rb4011igs_rm) is the main router which holds all VLANs.
* [TP-Link TL-SG2428P](https://www.tp-link.com/us/business-networking/omada-sdn-switch/tl-sg2428p/) PoE switch for all devices in the house, modded with two [Noctua NF-A4x20 5v](https://www.amazon.de/-/en/gp/product/B071W6JZV8/ref=ppx_yo_dt_b_search_asin_title?ie=UTF8&psc=1) for silence.
* [EAP660HD](https://www.tp-link.com/us/business-networking/ceiling-mount-access-point/eap660-hd/), and 2x [EAP615-Wall](https://www.tp-link.com/us/business-networking/omada-sdn-access-point/eap615-wall/) are in charge of WiFi(6) in all the rooms. They also provide seamless roaming when you walk from room to room.

### NAS

As I really appreciate the silence, the goal was to build/get machines that are either passive or very silent.

* [UNYKAch 2128 19 2U Rack Case](https://www.amazon.de/-/en/gp/product/B079XY9SMQ/ref=ppx_yo_dt_b_search_asin_title?ie=UTF8&psc=1)
* [ASRock J5005-ITX](https://www.asrock.com/mb/Intel/J5005-ITX/index.asp)
* 8GB SO-DIMM DDR4 RAM
* 3x [WD Red 8TB](https://www.amazon.com/Western-Digital-Plus-Internal-Drive/dp/B08TZT47VT)
* [Intel 300GB SSD](https://www.amazon.com/Intel-SSDSC2BB300G4-S3500-300-Ssd/dp/B00EE8D3KS)
* [FSP250 PSU](https://www.fsp-group.com/en/product/PCPSU/1595227515-999.html)
* 3x [Noctua NF-A8 ULN](https://www.amazon.de/dp/B00NEMGCRQ?psc=1&ref=ppx_yo2ov_dt_b_product_details)

NAS is running Ubuntu 22.04. [Snapraid](https://www.snapraid.it/) is used for software raid, and [mergerfs](https://github.com/trapexit/mergerfs) is used to have unified filesystem for disks. One disk is parity drive and the other two are in mergerfs pool which in total gives out around 15TB of space. The setup for storage server is in [ansible part](https://github.com/nklmilojevic/home/tree/main/ansible/roles) of this repo.

### k3s

[This small and very silent machine](https://www.amazon.de/-/en/gp/product/B09QPGP86K/ref=ppx_yo_dt_b_search_asin_title?ie=UTF8&psc=1) is currently a single k3s node. It has 24GB RAM and i5-8259U which is more than enough for all the things that I'm running.

#### Core software:

* [flux](https://toolkit.fluxcd.io/) - GitOps operator for managing Kubernetes clusters from a Git repository
* [kube-vip](https://kube-vip.io/) - Load balancer for the Kubernetes control plane nodes
* [metallb](https://metallb.universe.tf/) - Load balancer for Kubernetes services
* [cert-manager](https://cert-manager.io/) - Operator to request SSL certificates and store them as Kubernetes resources
* [calico](https://www.tigera.io/project-calico/) - Container networking interface for inter pod and service networking
* [external-dns](https://github.com/kubernetes-sigs/external-dns) - Operator to publish DNS records to Cloudflare (and other providers) based on Kubernetes ingresses
* [k8s_gateway](https://github.com/ori-edge/k8s_gateway) - DNS resolver that provides local DNS to your Kubernetes ingresses
* [ingress-nginx](https://kubernetes.github.io/ingress-nginx/) - Kubernetes ingress controller used for a HTTP reverse proxy of Kubernetes ingresses
* [local-path-provisioner](https://github.com/rancher/local-path-provisioner) - provision persistent local storage with Kubernetes
* [onepassword-connect](https://github.com/1Password/connect) - access secrets for cluster apps
* [external-secrets](https://github.com/external-secrets/external-secrets) - connects to onepassword-connect and provisions k8s secrets
* [nfs-subdir-external-provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) - PVCs on NFS
* [reloader](https://github.com/stakater/Reloader) - reloads apps on specific triggers (configmap, secret change, etc)

#### Home automation:

* [home-assistant](https://github.com/home-assistant/core) - Best home automation tool out there
* [mosquitto](https://github.com/eclipse/mosquitto) - MQTT broker
* [zigbee2mqtt](https://github.com/Koenkk/zigbee2mqtt) - [Sonoff Zigbee stick](https://www.amazon.de/-/en/gp/product/B09KXTCMSC/ref=ppx_yo_dt_b_search_asin_title?ie=UTF8&psc=1) plugged into k3s node
* [zwavejs](https://github.com/zwave-js/zwave-js-ui) - For [Aeotec Z-Stick](https://aeotec.com/products/aeotec-z-stick-gen5/)

#### Media:

* [bazarr](https://www.bazarr.media/)
* [jellyfin](https://jellyfin.org/)
* [lidarr](https://lidarr.audio/)
* [overseerr](https://overseerr.dev/)
* [plex](https://www.plex.tv/)
* [prowlarr](https://github.com/Prowlarr/Prowlarr)
* [qbittorrent](https://www.qbittorrent.org/)
* [radarr](https://radarr.video/)
* [sabnzbd](https://sabnzbd.org/)

#### Network

* [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) network-wide adblocking
* [omada-controller](https://github.com/mbentley/docker-omada-controller) for controlling APs

### Other

* [Geekworm PiKVM with Raspberry Pi 4](https://www.amazon.de/dp/B0B5QVFYMB?psc=1&ref=ppx_yo2ov_dt_b_product_details) for the devices that do not have builtin IPMI/IDRAC/etc
* [APC Smart-UPS SC 450VA 230V](https://www.apc.com/shop/hr/en/products/APC-Smart-UPS-SC-450VA-230V-1U-Rackmount-Tower/P-SC450RMI1U)

Everything is plugged into APC UPS, including router and switch. Total power used for all devices is 96W, with peaks up to 150W in full load.

[Rack picture](https://i.imgur.com/7Qq3AfM.jpg)