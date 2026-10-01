---
title: "Podman Watchguard: sistema embebido IoT para monitoreo y proteccion de red domestica"
author: "Proyecto de Sistemas Embebidos"
date: "4 de mayo de 2026"
lang: es
geometry: margin=2.5cm
---

# Podman Watchguard

## Resumen

Podman Watchguard es un sistema embebido IoT basado en Raspberry Pi Zero 2 W que monitorea un gabinete de red domestico o de pequeno comercio. El equipo combina sensores ambientales, sensor de apertura/tamper y medicion de consumo electrico con actuadores locales. Su objetivo es evitar fallas cotidianas en routers, modems y pequenos equipos de red: sobretemperatura, humedad elevada, apertura no autorizada, alimentacion inestable y necesidad de reinicio controlado.

El sistema tambien puede ejecutar servicios de infraestructura livianos en contenedores Podman: monitor IoT, WireGuard/DNS, honeypot SSH y una autoridad certificante pequena. Para la consigna, la parte central es la aplicacion IoT con sensores y actuadores; ademas se incluye un lazo de control simple para ventilacion.

## 1. Sistema implementado

Se elige la opcion **aplicacion de IoT con sensores y actuadores**. El sistema mide variables reales del entorno del gabinete, toma decisiones locales y publica telemetria. Tambien implementa un **lazo de control on/off con histeresis** para encender y apagar un ventilador segun la temperatura y la humedad.

### Sensores

| Sensor | Variable medida | Uso en el sistema |
|---|---:|---|
| DHT22 / AM2302 | Temperatura y humedad | Detectar sobretemperatura, humedad alta y decidir ventilacion. |
| INA219 | Voltaje, corriente y potencia DC | Detectar alimentacion baja, consumo anormal y estimar estado electrico del sistema. |
| Reed switch magnetico | Apertura/cierre | Detectar apertura del gabinete o manipulacion no autorizada. |

### Actuadores

| Actuador | Accion | Condicion de activacion |
|---|---|---|
| Ventilador 5 V | Extrae calor y humedad del gabinete | Temperatura o humedad por encima del umbral configurado, con histeresis (34/30 °C y 75/70 %). |
| LED RGB o LED tricolor | Estado visual local | Verde normal, azul ventilando, rojo alerta. |
| Buzzer activo 5 V | Alarma sonora local | Tamper, temperatura critica, humedad alta o energia anomala. |
| Rele 5 V optoaislado | Reinicio controlado de router/modem | Solo ante condicion electrica anomala configurada y con retardo de seguridad. |

## Problema que resuelve

En muchas casas, oficinas pequenas y laboratorios, el router, el modem, una mini UPS y otros equipos de red quedan guardados en un gabinete cerrado. Eso genera problemas comunes:

- El gabinete acumula calor y reduce la vida util de los equipos.
- La humedad puede afectar conectores, fuentes y placas.
- Nadie nota rapidamente si el gabinete fue abierto o manipulado.
- Las fuentes pequenas pueden entregar baja tension bajo carga.
- Cuando la red se cae, la solucion manual suele ser desconectar y volver a conectar el modem o router.

Podman Watchguard resuelve esos problemas con monitoreo local continuo, alertas y acciones automaticas. No reemplaza a un sistema industrial de seguridad, pero si cubre un problema cotidiano de bajo costo: cuidar y diagnosticar el punto de conectividad de una vivienda, aula, taller o pequeno comercio.

## Hardware propuesto

Precios estimados de referencia en USD consultados para mayo de 2026. En Argentina los valores finales pueden variar por importacion, stock, impuestos y tipo de cambio.

| Componente | Cantidad | Precio unitario estimado | Subtotal | Justificacion breve |
|---|---:|---:|---:|---|
| Raspberry Pi Zero 2 W | 1 | USD 15.00 | USD 15.00 | Computadora embebida con WiFi, Bluetooth, GPIO y potencia suficiente para Linux y contenedores livianos. |
| microSD 32 GB clase A1 | 1 | USD 7.00 | USD 7.00 | Almacena Raspberry Pi OS, contenedores, logs y configuracion. |
| Fuente micro USB 5 V 2.5 A | 1 | USD 8.00 | USD 8.00 | Margen de corriente para placa, sensores y pequenos actuadores. |
| DHT22 / AM2302 | 1 | USD 8.95 | USD 8.95 | Sensor digital de temperatura/humedad con mejor precision que DHT11. |
| INA219 | 1 | USD 9.95 | USD 9.95 | Mide tension y corriente por I2C sin usar dos multimetros externos. |
| Reed switch magnetico | 1 | USD 3.95 | USD 3.95 | Sensor simple y barato para detectar apertura del gabinete. |
| Ventilador 5 V 40 mm | 1 | USD 3.95 | USD 3.95 | Actuador de refrigeracion de bajo consumo para gabinetes chicos. |
| Rele 5 V de 1 canal optoaislado | 1 | USD 6.99 | USD 6.99 | Permite cortar/restaurar alimentacion de un equipo externo con aislamiento. |
| Buzzer activo 5 V | 1 | USD 0.95 | USD 0.95 | Alarma sonora local de bajo costo; trae oscilador interno, asi que suena con solo alimentarlo y alcanza con prenderlo y apagarlo desde un GPIO, sin PWM. |
| LED RGB o 3 LEDs + resistencias | 1 | USD 1.00 | USD 1.00 | Indicacion visual inmediata sin depender de red. |
| Transistor/MOSFET, diodo flyback, resistencias, borneras y cables | 1 lote | USD 5.00 | USD 5.00 | Etapa de potencia segura para ventilador, buzzer y rele. |
| Gabinete impreso en 3D | 1 | USD 6.00 | USD 6.00 | Caja ajustada al montaje, con ventilacion y soportes. |

**Costo total estimado:** USD 76.74.

## Justificacion del hardware

La Raspberry Pi Zero 2 W se elige porque combina GPIO, WiFi integrado, Linux y capacidad para ejecutar Podman en un formato pequeno. Segun la documentacion oficial de Raspberry Pi, la placa incluye CPU Arm Cortex-A53 de cuatro nucleos a 1 GHz, 512 MB de SDRAM, WiFi 2.4 GHz, Bluetooth 4.2/BLE y cabecera GPIO de 40 pines. Esto es mas que suficiente para una aplicacion IoT liviana, telemetria local y contenedores pequenos.

El DHT22 se selecciona porque mide temperatura y humedad con salida digital y rango adecuado para gabinetes cerrados. El INA219 se utiliza porque permite medir corriente y tension DC por I2C con medicion en el lado alto, evitando modificar la referencia de masa del circuito. El reed switch es robusto, barato y apropiado para una puerta o tapa. Los actuadores son simples y de bajo consumo: ventilador para control termico, LED para estado, buzzer para alarma y rele para una accion fisica controlada.

La decision de usar Podman se justifica por aislamiento de servicios, despliegue reproducible a partir de unidades quadlet versionadas en Git y actualizacion automatica de imagenes con `podman auto-update`, sin requerir un orquestador pesado. Los cambios en los quadlets se aplican por ahora a mano (`git pull`, copia de los archivos y `systemctl --user daemon-reload`). En un equipo embebido pequeno, esto permite separar el monitor IoT de servicios como VPN, DNS o honeypot.

## Software tentativo

El software se organiza en capas:

1. **Sistema operativo:** Raspberry Pi OS Lite de 64 bits basado en Debian 13 "trixie", que trae Podman 5.4. Bookworm no alcanza: su Podman 4.3.1 es anterior a Quadlet (4.4) y a las unidades `.pod` (5.0).
2. **Runtime de contenedores:** Podman rootless con quadlets systemd.
3. **Monitor IoT:** aplicacion Python incluida en `gitops-agent/watchguard_monitor.py`. Pese al nombre, la carpeta `gitops-agent/` contiene solo el monitor, no un agente GitOps.
4. **Configuracion:** archivo JSON con umbrales de temperatura, humedad y energia.
5. **Telemetria:** salida JSON por stdout; en una version desplegada puede enviarse a MQTT, Prometheus node exporter textfile o syslog.
6. **Servicios adicionales:** WireGuard/DNS para acceso seguro, Cowrie como honeypot opcional y step-ca para certificados internos.

### Logica de control

El lazo de control del ventilador es on/off con histeresis:

- Si la temperatura es mayor o igual a 34 °C, el ventilador se enciende; la temperatura deja de pedirlo cuando baja a 30 °C o menos.
- Si la humedad es mayor o igual a 75 %, el ventilador tambien se enciende; la humedad deja de pedirlo cuando baja a 70 % o menos.
- El ventilador queda encendido mientras alguna de las dos variables lo pida.
- Si la temperatura llega a 42 °C, se activa alerta critica con buzzer y LED rojo.

La histeresis evita que el ventilador prenda y apague muchas veces cuando la temperatura queda cerca del umbral.

### Estados del equipo

| Estado | Condicion | Actuadores |
|---|---|---|
| Normal | Temperatura, humedad y energia dentro de rango; gabinete cerrado | LED verde |
| Ventilando | Temperatura sobre umbral de ventilacion | Ventilador encendido, LED azul |
| Alerta ambiental | Temperatura critica o humedad alta | Ventilador, LED rojo, buzzer |
| Tamper | Reed switch abierto | LED rojo, buzzer, evento de telemetria |
| Energia anomala | Baja tension o corriente alta | LED rojo, buzzer; si ambas coinciden, reinicio por rele con retardo |

## Circuito y conexionado

La Raspberry Pi trabaja con GPIO de 3.3 V. Por eso los actuadores de 5 V no deben alimentarse directamente desde un pin GPIO: se controlan con transistor/MOSFET, resistencia de base/gate y diodo flyback cuando hay cargas inductivas.

| Componente | Pin Raspberry Pi sugerido | Conexion |
|---|---|---|
| DHT22 VCC | Pin 1, 3.3 V | Alimentacion del sensor. |
| DHT22 DATA | GPIO4, pin 7 | Datos con resistencia pull-up de 4.7 kOhm a 3.3 V. |
| DHT22 GND | Pin 6, GND | Masa comun. |
| INA219 VCC | Pin 1, 3.3 V | Alimentacion del modulo. |
| INA219 GND | Pin 9, GND | Masa comun. |
| INA219 SDA | GPIO2, pin 3 | Bus I2C SDA. |
| INA219 SCL | GPIO3, pin 5 | Bus I2C SCL. |
| INA219 VIN+ | Positivo de fuente medida | Entrada de medicion de corriente. |
| INA219 VIN- | Positivo hacia la carga | Salida hacia carga medida. |
| Reed switch | GPIO17, pin 11 | Entrada digital con pull-up interno; el sensor cierra a GND. |
| LED verde/azul/rojo | GPIO22/23/24 | Cada canal con resistencia limitadora. |
| Buzzer | GPIO18, pin 12 | Salida digital on/off mediante transistor; buzzer activo, con oscilador interno. |
| Ventilador 5 V | GPIO27, pin 13 | Control por MOSFET; fan alimentado desde 5 V; GND comun. |
| Rele 5 V | GPIO25, pin 22 | Modulo optoaislado; alimentacion 5 V separada si es posible. |

### Diagrama textual

```text
Fuente 5 V ----+---------------- Raspberry Pi 5 V
               +---------------- Ventilador 5 V ---- MOSFET ---- GND
               +---------------- Modulo rele 5 V ---- GPIO25

Raspberry Pi 3.3 V ---- DHT22 VCC
Raspberry Pi GPIO4 ----- DHT22 DATA ---- 4.7 kOhm ---- 3.3 V
Raspberry Pi GND ------- DHT22 GND

Raspberry Pi I2C SDA/SCL ---- INA219 SDA/SCL
Fuente medida + ---- INA219 VIN+ / VIN- ---- carga medida +

GPIO17 ---- Reed switch ---- GND
GPIO18 ---- transistor ---- buzzer activo ---- 5 V/GND
GPIO22/23/24 ---- resistencias ---- LED RGB ---- GND
```

## Gabinete

Si, el gabinete conviene hacerlo con impresora 3D para adaptar la caja al montaje real. El diseno propuesto es una caja de PLA/PETG con:

- Separadores para Raspberry Pi Zero 2 W.
- Soporte para ventilador de 40 mm con rejilla.
- Ranuras de entrada/salida de aire para flujo cruzado.
- Ventana o perforacion para LED de estado.
- Orificio lateral para cables USB, Ethernet y alimentacion.
- Alojamiento para el reed switch en la tapa.
- Espacio para borneras y alivio mecanico de cables.

Se recomienda PETG antes que PLA si el gabinete puede calentarse o quedar cerca de una fuente. El PLA es suficiente para prototipo de aula, pero PETG resiste mejor temperatura y deformacion. Para una version final, la tapa deberia usar tornillos M2.5/M3 y dejar accesible la microSD.

## Pruebas previstas

El repositorio incluye pruebas locales en `tests/` (`make test`): verificaciones del repositorio en `tests/test_project.py`, pruebas del backend de hardware con dispositivos simulados en `tests/test_hardware.py` y pruebas de la logica de control en `tests/test_control.py`:

- Verificacion de archivos requeridos.
- Validacion del JSON de configuracion.
- Parseo de quadlets como archivos INI.
- Comprobacion de que este documento responde la consigna.
- Ejecucion del monitor en modo simulacion y validacion de JSON emitido.
- Lectura de sensores y manejo de actuadores contra arboles sysfs falsos, un bus I2C falso y un modulo `gpiod` falso.
- Histeresis del ventilador, alertas, retardo del rele y estado seguro ante fallas de lectura o al detener el monitor.

Tambien se incluyen scripts para el ciclo solicitado con Podman en macOS:

```sh
./scripts/install-podman-macos.sh
./scripts/podman-smoke-test.sh
./scripts/uninstall-podman-macos.sh
```

La prueba con Podman construye la imagen `podman-watchguard-monitor:local` y ejecuta tres iteraciones del monitor con sensores simulados. Esto permite probar el flujo sin tener todavia la Raspberry Pi ni los sensores conectados. En macOS no llego a correr, porque la maquina virtual de `podman machine` no termino de descargarse. El 1 de octubre de 2026 se ejecutaron los comandos de `make podman-build` y `make podman-smoke` con Podman 5.8.7, dentro de un contenedor (imagen `quay.io/podman/stable` sobre Docker rootless) en una PC de escritorio x86_64 con Debian 13: la imagen se construyo y el monitor completo las tres iteraciones con sensores simulados, emitiendo un evento JSON por iteracion, y termino con codigo de salida 0. Esa corrida no fue en la Raspberry Pi: falta repetirla en la placa, sobre arm64 y con Podman rootless.

## Riesgos y mejoras futuras

- El backend GPIO/I2C de sensores y actuadores esta implementado sobre interfaces estandar de Linux (IIO, I2C y libgpiod) y probado con dispositivos simulados; falta ejecutarlo y calibrarlo sobre la Raspberry Pi fisica con el circuito armado.
- El rele no debe usarse con tensiones de red sin gabinete, fusible y aislamiento adecuados.
- Para notificaciones remotas conviene agregar MQTT con TLS.
- Para produccion se debe limitar escritura en microSD y rotar logs.
- El gabinete debe probarse termicamente con el router real dentro o cerca del montaje.

## Referencias

- Raspberry Pi, "Raspberry Pi Zero 2 W", especificaciones oficiales y precio base de USD 15: <https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/>
- Raspberry Pi, anuncio oficial de Zero 2 W con CPU, RAM, WiFi y precio: <https://www.raspberrypi.com/news/new-raspberry-pi-zero-2-w-2/>
- Adafruit, guia INA219: medicion high-side por I2C, hasta 26 V y hasta +/-3.2 A: <https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout>
- Adafruit, producto INA219, precio de referencia USD 9.95: <https://www.adafruit.com/product/904>
- CRCibernetica, DHT22, precio de referencia USD 8.95 y especificaciones: <https://www.crcibernetica.com/dht-22-digital-temperature-humidity-sensor-module/>
- Adafruit, magnetic contact switch, precio de referencia USD 3.95: <https://www.adafruit.com/product/375>
- Midland Electronics, ventilador 5 V 40 mm, precio de referencia USD 3.95: <https://midlandelectronics.com/product/5v-brushless-dc-cooling-fan-40mm/>
- Adeept, modulo rele 5 V 1 canal, precio de referencia USD 6.99: <https://www.adeept.com/1ch-relay_p0058.html>
- Adafruit, buzzer activo 5 V con oscilador interno (2 kHz), precio de referencia USD 0.95: <https://www.adafruit.com/product/1536>
