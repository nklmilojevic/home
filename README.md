# Home

My home setup with all things needed for managing media, home automation and more.

## Setup overview

### Network setup

* Gateway - **Ubiquity UDM Pro**
* Switch - **USW Pro Max 24 PoE**
* Switch - **USW Flex 2.5G 8**
* Wireless AP - **U6 Enterprise**
* Wireless AP - **U6 Enterprise IW**

Network is divided into several VLANs for different purposes.

### NAS

As I really appreciate the silence, the goal was to build/get machines that are either passive or very silent.

* **UNYKAch 2128 19 2U Rack Case**
* No-name **N100** motherboard from Aliexpress
* **32GB SO-DIMM DDR5 RAM**
* 3x**WD Red 16TB Pro** in raidz1
* **Crucial P3 Plus 500 GB CT500P3PSSD8** - For TrueNAS Scale
* **FSP250 PSU**
* 3x**Noctua NF-A8 ULN**

NAS is TrueNAS Scale with raidz1.

### Talos

2x NiPoGi CK10 Mini PC and 1x Minisforum NAB6 Lite 32GB DDR4 RAM, i5-12450H and 1TB NVME for ceph are used for Talos nodes.

### Other

* **JetKVM + KVM switch**
* **APC Smart-UPS SC 450VA 230V**
* **SMLight SZLB-06M** as PoE Zigbee adaptter