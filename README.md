# Home

My home setup with all things needed for managing media, home automation and more.

## Setup overview

### Network setup

![alt text](https://i.imgur.com/jCNhjFC.png)

I moved from Mikrotik to Unifi, from Unifi back to Mikrotik and Omada access points, and this year I moved fully to Omada.

Omada setup:

* Gateway - **ER7412-M2 v1.20**
* Switch - **SG3210XHP-M2 v3.0** - 2.5GBit switch. Talos nodes, EAP660HD and NAS are connected to this switch.
* Switch - **SG2428P v5.20**
* Switch - **SG2008P v3.20** - additional switch for Livingroom
* Wireless AP - **EAP660HD** - main WiFi AP
* Wireless AP - 2x**EAP615-Wall** - additional WiFi AP for the rest of the apartment

Network is divided into several VLANs for different purposes.

### NAS

As I really appreciate the silence, the goal was to build/get machines that are either passive or very silent.

* **UNYKAch 2128 19 2U Rack Case**
* No-name **N100** motherboard from Aliexpress
* **32GB SO-DIMM DDR5 RAM**
* 3x**WD Red 16TB Pro**
* **Patriot P300 128GB NVME** - for TrueNAS Scale
* **Crucial P3 Plus 500 GB CT500P3PSSD8** - For SLOG
* **FSP250 PSU**
* 3x**Noctua NF-A8 ULN**

NAS is TrueNAS Scale with raidz1 and SLOG.

### Talos

3x NiPoGi CK10 Mini PC 32GB DDR4 RAM, I5-12450H and 1TB NVME for ceph are used for Talos nodes.

### Other

* **Geekworm PiKVM with Raspberry Pi 4 + KVM switch**
* **APC Smart-UPS SC 450VA 230V**
* **SMLight SZLB-06M** as PoE Zigbee adaptter


[Rack picture](https://i.imgur.com/fxvBthR.jpeg)