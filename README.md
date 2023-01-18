# home

**README is WIP**

My home cluster with all things needed for managing media, home automation and more.

Core software:

- [flux](https://toolkit.fluxcd.io/) - GitOps operator for managing Kubernetes clusters from a Git repository
- [kube-vip](https://kube-vip.io/) - Load balancer for the Kubernetes control plane nodes
- [metallb](https://metallb.universe.tf/) - Load balancer for Kubernetes services
- [cert-manager](https://cert-manager.io/) - Operator to request SSL certificates and store them as Kubernetes resources
- [calico](https://www.tigera.io/project-calico/) - Container networking interface for inter pod and service networking
- [external-dns](https://github.com/kubernetes-sigs/external-dns) - Operator to publish DNS records to Cloudflare (and other providers) based on Kubernetes ingresses
- [k8s_gateway](https://github.com/ori-edge/k8s_gateway) - DNS resolver that provides local DNS to your Kubernetes ingresses
- [ingress-nginx](https://kubernetes.github.io/ingress-nginx/) - Kubernetes ingress controller used for a HTTP reverse proxy of Kubernetes ingresses
- [local-path-provisioner](https://github.com/rancher/local-path-provisioner) - provision persistent local storage with Kubernetes
- [onepassword-connect](https://github.com/1Password/connect) - access secrets for cluster apps
- [external-secrets](https://github.com/external-secrets/external-secrets) - connects to onepassword-connect and provisions k8s secrets
- [nfs-subdir-external-provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) - PVCs on NFS
- [reloader](https://github.com/stakater/Reloader) - reloads apps on specific triggers (configmap, secret change, etc)

Home automation:

- [home-assistant](https://github.com/home-assistant/core) - Best home automation tool out there
- [mosquitto](https://github.com/eclipse/mosquitto) - MQTT broker
- [zigbee2mqtt](https://github.com/Koenkk/zigbee2mqtt) - [Sonoff Zigbee stick](https://www.amazon.de/-/en/gp/product/B09KXTCMSC/ref=ppx_yo_dt_b_search_asin_title?ie=UTF8&psc=1) plugged into k3s node
- [zwavejs](https://github.com/zwave-js/zwave-js-ui) - For [Aeotec Z-Stick](https://aeotec.com/products/aeotec-z-stick-gen5/)

Media:

- [bazarr]()
- [jellyfin]()
- [lidarr]()
- [overseerr]()
- [plex]()
- [prowlarr]()
- [qbittorrent]()
- [radarr]()
- [sabnzbd]()
