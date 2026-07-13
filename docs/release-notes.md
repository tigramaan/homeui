# Release Notes

## 2.238.2

WB8/Bullseye migrations that install HomeUI and upgrade `wb-mqtt-serial` must not keep
`libwbmqtt1-5` older than `5.6.0-wb102`. HomeUI now declares a `Breaks` relationship for
older `libwbmqtt1-5` packages, so APT upgrades the ABI library together with the serial
service instead of leaving a service-start failure that requires manual repair.
