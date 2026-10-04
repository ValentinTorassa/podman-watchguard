# Informe vs codigo

Fecha: 2026-10-01.

Este documento compara lo que afirman `docs/proyecto.md` y `docs/test-report.md` con lo que hace el codigo del repositorio. La primera version de este documento no modificaba el informe: cada correccion de texto quedaba propuesta para aplicarla a mano antes de CACIC. El mismo 2026-10-01 se aplicaron en `docs/proyecto.md` las que marca la columna "Aplicado" del resumen. Los numeros de linea citados abajo corresponden a la version del informe anterior a esos cambios.

Aviso que vale para todo lo que sigue: el backend de hardware (sensores y actuadores) esta probado solamente con dispositivos simulados. `make test` corre 51 pruebas con arboles sysfs falsos, un bus I2C falso y un modulo `gpiod` falso, y todas pasan; nada se ejecuto todavia en una Raspberry Pi con el circuito armado. El informe no debe presentar mediciones ni pruebas sobre hardware real.

## Resumen

| # | Lo que dice el informe | Estado en el codigo | Que hay que hacer | Aplicado (2026-10-01) |
|---|---|---|---|---|
| 1 | Raspberry Pi OS Lite Bookworm | No funciona: Bookworm trae Podman 4.3.1, sin Quadlet | Corregir el texto | Si |
| 2 | Ventilador, LED, buzzer y rele se accionan | Cierto en el codigo, sin probar en hardware | Nada obligatorio (ver 9) | No (no hacia falta) |
| 3 | Rele con retardo de seguridad | Cierto en el codigo; los valores los elige el repositorio | Opcional: dar los valores | No (sigue opcional) |
| 4 | Humedad alta enciende el ventilador | Cierto en el codigo, con histeresis | Opcional: ajustar la tabla de actuadores | Si, tambien en "Logica de control" |
| 5 | INA219 mide potencia | Cierto: se informa `power_mw` | Nada | No hacia falta |
| 6 | Riesgo: "el backend GPIO/I2C debe implementarse" | Desactualizado | Corregir el texto | Si |
| 7 | Actualizacion por imagenes/quadlets | A medias: imagenes automaticas, quadlets a mano | Corregir el texto | Si, con la aclaracion sobre `gitops-agent/` |
| 8 | Prueba con Podman | No habia corrido; corrio el 2026-10-01 dentro de un contenedor, fuera de la Pi | Correrla o corregir el texto | Si: se corrio y el texto lo registra |
| 9 | Buzzer con salida PWM | El codigo lo prende y apaga; el buzzer de la lista de materiales era pasivo | Cambiar la pieza o el texto | Si: buzzer activo |
| 10 | Energia anomala = baja tension y corriente alta | La alerta salta con cualquiera de las dos; el rele exige ambas | Ajustar el texto | Si |

Los puntos 1, 6, 7 y 8 son errores del texto y hay que corregirlos. Los puntos 9 y 10 aparecieron al revisar y tambien conviene corregirlos. Al 2026-10-01 estan aplicados 1, 4, 6, 7, 8, 9 y 10; quedan sin aplicar el 2 y el 3, y el 5 no necesitaba cambios.

## 1. Sistema operativo: Bookworm no sirve

**Informe:** `docs/proyecto.md`, linea 83: "Sistema operativo: Raspberry Pi OS Lite Bookworm."

**Realidad:** Debian 12 Bookworm trae Podman 4.3.1. Quadlet aparecio en Podman 4.4 y las unidades `.pod` en Podman 5.0, de modo que los archivos de `quadlets/` no se pueden usar en Bookworm. Hace falta Raspberry Pi OS basado en Debian 13 "trixie" (Podman 5.4). Ademas tiene que ser de 64 bits: las imagenes de Cowrie y WireGuard y los paquetes binarios de `gpiod` solo se publican para arm64. El `README.md` (seccion "Deploying on the Pi") ya lo dice.

**Correccion sugerida (linea 83):**

> 1. **Sistema operativo:** Raspberry Pi OS Lite de 64 bits basado en Debian 13 "trixie", que trae Podman 5.4. Bookworm no alcanza: su Podman 4.3.1 es anterior a Quadlet (4.4) y a las unidades `.pod` (5.0).

## 2. Actuadores: ahora se accionan

**Informe:** lineas 29 a 36 (tabla de actuadores), 100 a 108 (estados del equipo) y 126 a 129 (pines).

**Codigo:** el monitor maneja los cuatro actuadores como salidas GPIO con libgpiod v2, con la misma frontera de dependencias que los sensores: `gpiod` solo se importa en modo hardware.

- `gitops-agent/watchguard_hardware.py`: `GpiodOutputs` (driver), `DEFAULT_OUTPUT_LINES` (pines del informe: ventilador GPIO27, buzzer GPIO18, LED verde/azul/rojo GPIO22/23/24, rele GPIO25) y `build_gpio_outputs` (lee `hardware.actuators` de la configuracion).
- `gitops-agent/watchguard_monitor.py`: `output_levels` traduce la decision a niveles de salida, `run_cycle` lee, decide, acciona y emite un evento por ciclo, y `main` apaga todo al salir.
- Estado seguro: todas las salidas apagadas (rele en reposo). Las lineas se piden ya apagadas, se apagan antes de liberarlas al salir (tambien ante el SIGTERM de `podman stop`; antes el proceso terminaba muerto por SIGKILL sin limpiar nada) y se apagan cuando falla una escritura. Si falla la lectura de sensores, el rele se libera, el ventilador queda encendido hasta que una lectura valida confirme que el gabinete se enfrio, el LED queda rojo y el buzzer callado.
- Pruebas: `tests/test_hardware.py` (`test_gpiod_outputs_*`, `test_monitor_reads_hardware_end_to_end`, `test_monitor_goes_safe_on_sigterm`) y `tests/test_control.py` (`test_cycle_*`, `test_sensor_fault_*`, `test_monitor_exits_cleanly_on_sigterm`).

No hace falta corregir el texto, salvo lo del buzzer (punto 9). Si se quiere mencionar el comportamiento ante fallas, una frase posible para "Logica de control":

> Ante una falla, el sistema queda en estado seguro: al detenerse el monitor se apagan todas las salidas y el rele queda en reposo, y si no hay lectura valida de sensores el rele no actua y el ventilador queda encendido hasta recuperar una lectura.

## 3. Rele con retardo de seguridad: implementado

**Informe:** linea 36 ("Solo ante condicion electrica anomala configurada y con retardo de seguridad") y linea 108 ("posible rele con retardo"). El informe no da valores.

**Codigo:** `RelaySequencer` en `gitops-agent/watchguard_monitor.py`, configurado en la seccion `relay` de `config/watchguard.example.json`. Los valores son una eleccion del repositorio, porque el informe no los especifica:

- `trigger_delay_seconds: 30`: la baja tension y la corriente alta tienen que aparecer juntas en todas las lecturas durante 30 s antes de que el rele actue. Una sola lectura normal reinicia la cuenta.
- `power_off_seconds: 10`: el rele corta la alimentacion 10 s, aunque las lecturas cambien (al cortar, la corriente cae).
- `cooldown_seconds: 300`: despues de devolver la alimentacion, el rele no vuelve a actuar por 5 min, para que el router termine de arrancar. Esto tambien evita un ciclo de reinicios.

El evento informa la fase en `actuators.relay_phase` (`idle`, `armed`, `power_cut` o `cooldown`). Pruebas: `tests/test_control.py`, `test_relay_*`.

**Correccion opcional (agregar a "Logica de control", despues de la linea 96):**

> - El rele solo actua si la baja tension y la corriente alta se mantienen juntas durante 30 s seguidos; corta la alimentacion del router 10 s y despues no vuelve a actuar durante 5 min, para dar tiempo a que el equipo arranque. El router se conecta por el contacto normalmente cerrado del rele, de modo que con el rele en reposo, o con la Raspberry Pi apagada, el router queda alimentado.

## 4. Humedad alta enciende el ventilador: implementado

**Informe:** linea 106 ("Alerta ambiental | Temperatura critica o humedad alta | Ventilador, LED rojo, buzzer"). La linea 33 contradice a la 106, porque dice que el ventilador solo depende de la temperatura.

**Codigo:** `Controller.decide` en `gitops-agent/watchguard_monitor.py`. El ventilador se enciende a 34 °C o con 75 % de humedad, y se apaga recien cuando la temperatura baja a 30 °C y la humedad a 70 % (`humidity.fan_off_percent`, nuevo en la configuracion, que por defecto queda 5 puntos debajo del umbral de alerta). Entre 70 % y 75 % la alerta ya no suena, pero el ventilador sigue encendido y el LED queda azul. Pruebas: `tests/test_control.py`, `test_high_humidity_turns_fan_on_with_hysteresis` y `test_fan_stays_on_while_either_variable_needs_it`.

**Correccion opcional (linea 33):**

> | Ventilador 5 V | Extrae calor y humedad del gabinete | Temperatura o humedad por encima del umbral configurado, con histeresis (34/30 °C y 75/70 %). |

**Aplicado (2026-10-01):** la fila de la linea 33 quedo con este texto. Para que "Logica de control" no contradiga la tabla, ahi se agrego la histeresis de humedad (75/70 %) y la regla de que el ventilador queda encendido mientras la temperatura o la humedad lo pida; la linea 19 dice ahora "segun la temperatura y la humedad".

## 5. Potencia del INA219: implementado

**Informe:** linea 26 ("Voltaje, corriente y potencia DC").

**Codigo:** cada lectura incluye `power_mw`, calculado en `RaspberryPiBackend.read` (y en la simulacion) como tension de bus por corriente. Es el mismo producto que guarda el registro de potencia del INA219; se calcula porque el driver `smbus` no escribe la calibracion del chip. Prueba: `tests/test_hardware.py`, `test_raspberry_pi_backend_composes_reading`.

No hace falta corregir el texto.

## 6. El riesgo "backend pendiente" quedo viejo

**Informe:** linea 186: "El backend GPIO/I2C real debe implementarse y calibrarse sobre la Raspberry Pi fisica."

**Realidad:** el backend esta implementado (sensores y actuadores) y probado con dispositivos simulados. Falta correrlo y calibrarlo en la placa.

**Correccion sugerida (linea 186):**

> - El backend GPIO/I2C de sensores y actuadores esta implementado sobre interfaces estandar de Linux (IIO, I2C y libgpiod) y probado con dispositivos simulados; falta ejecutarlo y calibrarlo sobre la Raspberry Pi fisica con el circuito armado.

## 7. Actualizacion: las imagenes si, los quadlets no

**Informe:** linea 77: "despliegue reproducible y actualizacion por imagenes/quadlets sin requerir un orquestador pesado". La carpeta `gitops-agent/` sugiere ademas un agente GitOps.

**Realidad:** `podman auto-update` actualiza las imagenes (`AutoUpdate=registry` en WireGuard, Cowrie y step-ca; `AutoUpdate=local` en el monitor). No existe ningun agente que traiga los cambios del repositorio y aplique los quadlets: se copian a mano (`git pull`, copia a `~/.config/containers/systemd/`, `systemctl --user daemon-reload`). La carpeta `gitops-agent/` solo contiene el monitor. El `README.md` (seccion "Updates: what is GitOps today") ya lo explica.

**Correccion sugerida (linea 77):**

> La decision de usar Podman se justifica por aislamiento de servicios, despliegue reproducible a partir de unidades quadlet versionadas en Git y actualizacion automatica de imagenes con `podman auto-update`, sin requerir un orquestador pesado. Los cambios en los quadlets se aplican por ahora a mano (`git pull`, copia de los archivos y `systemctl --user daemon-reload`).

Si el informe nombra la carpeta `gitops-agent/`, conviene aclarar que contiene el monitor y no un agente GitOps.

**Aplicado (2026-10-01):** la linea 77 quedo con el texto sugerido, y en la linea 85, que nombra `gitops-agent/watchguard_monitor.py`, se agrego: "Pese al nombre, la carpeta `gitops-agent/` contiene solo el monitor, no un agente GitOps."

## 8. La prueba con Podman no habia corrido (corrio el 2026-10-01)

**Informe:** linea 182: "La prueba con Podman construye la imagen `podman-watchguard-monitor:local` y ejecuta tres iteraciones del monitor con sensores simulados."

**Realidad:** `docs/test-report.md` (lineas 56 a 72) registra que en macOS la maquina virtual de `podman machine` no termino de descargarse y que `podman-smoke-test.sh` fallo con "unable to connect to Podman socket". La imagen nunca se construyo ni se ejecuto. Ademas, el reporte (y la lista de las lineas 166 a 172) solo cubre `tests/test_project.py`; hoy tambien existen `tests/test_hardware.py` y `tests/test_control.py`.

**Lo mejor:** correr `make podman-build` y `make podman-smoke` en una maquina Linux con Podman (no necesita maquina virtual), registrar la salida en `docs/test-report.md` y actualizar ahi la lista de pruebas. Si no se llega, corregir el texto.

**Correccion sugerida (linea 182), si la prueba no se corre:**

> El script de prueba con Podman construye la imagen `podman-watchguard-monitor:local` y ejecuta tres iteraciones del monitor con sensores simulados. En la corrida registrada en `docs/test-report.md` (macOS, Podman 5.8.2) no llego a ejecutarse porque la maquina virtual de `podman machine` no termino de descargarse, asi que la prueba del flujo con contenedores sigue pendiente.

**Correccion sugerida (linea 166):**

> El repositorio incluye pruebas locales en `tests/` (`make test`): verificaciones del repositorio en `tests/test_project.py`, pruebas del backend de hardware con dispositivos simulados en `tests/test_hardware.py` y pruebas de la logica de control en `tests/test_control.py`:

**Aplicado (2026-10-01): la prueba se corrio.** Podman no esta instalado en las maquinas Linux disponibles, asi que se ejecuto dentro de un contenedor: imagen `quay.io/podman/stable` (Podman 5.8.7 sobre Fedora 44), con `--privileged`, sobre Docker 29.8.1 rootless, en una PC de escritorio x86_64 con Debian 13 (kernel 6.12). Adentro del contenedor Podman corrio como root, con almacenamiento overlay y cgroups v2. Se usaron los comandos de los objetivos `podman-build` y `podman-smoke` del `Makefile`:

- `podman build -t podman-watchguard-monitor:local -f containers/monitor/Containerfile .`: los 7 pasos terminaron bien (instalo `gpiod` 2.5.0 y `smbus2` 0.6.1), imagen de 133 MB, codigo de salida 0.
- `podman run --rm -v ./config:/config:ro podman-watchguard-monitor:local --config /config/watchguard.example.json --iterations 3`: tres eventos JSON, uno cada 2 s, y codigo de salida 0. Con la simulacion de la configuracion de ejemplo (36.2 a 36.7 °C, 60.5 a 61.9 % de humedad, 5.03 a 5.05 V, 422 a 435 mA) los tres muestran ventilador encendido, LED azul, buzzer y rele apagados, y ninguna alerta.

Esto prueba la imagen y el flujo con contenedores, no el hardware ni el despliegue con quadlets: no corrio en la Raspberry Pi, ni sobre arm64, ni con Podman rootless. En `docs/proyecto.md` la linea 182 ahora registra esta corrida, y se aplico la correccion de la linea 166, con dos items nuevos en la lista para las pruebas de hardware y de control. `docs/test-report.md` se actualizo el 2026-10-04: registra esta corrida y las 51 pruebas de las tres suites, y conserva el registro del 2026-05-04 en macOS, correcto para esa fecha.

## 9. Buzzer: el codigo no genera PWM y el buzzer de la lista era pasivo (resuelto: buzzer activo)

**Informe:** linea 127 ("Salida PWM mediante transistor"), linea 64 de la lista de materiales y referencia de la linea 202 (Adafruit 160, USD 1.50).

**Realidad:** el codigo prende y apaga GPIO18 como salida digital (`GpiodOutputs`), sin generar un tono. El Adafruit 160 (PS1240) es un piezo pasivo, que necesita una onda cuadrada: con tension continua solo hace un clic. Hay dos salidas:

- Cambiar la pieza por un buzzer activo (con oscilador interno), que suena con tension continua y funciona con el codigo actual. Hay que actualizar la referencia y el precio en la lista de materiales y en `docs/bill_of_materials.csv`.
- Mantener el piezo pasivo e implementar PWM por hardware en GPIO18 (`dtoverlay=pwm` y `/sys/class/pwm`). Eso no esta hecho y requiere darle al contenedor rootless escritura sobre sysfs.

**Correccion sugerida (linea 127), con buzzer activo:**

> | Buzzer | GPIO18, pin 12 | Salida digital on/off mediante transistor; buzzer activo, con oscilador interno. |

**Aplicado (2026-10-01): se eligio el buzzer activo** y el codigo no cambio. En `docs/proyecto.md`, la linea 127 quedo con el texto sugerido; el actuador de la linea 35 y la pieza de la linea 64 pasaron a "Buzzer activo 5 V"; el diagrama dice "buzzer activo"; y la referencia de la linea 202 es ahora el Adafruit 1536 ("Buzzer 5V - Breadboard friendly", piezo con oscilador interno de 2 kHz, de 3 a 5 V, USD 0.95, precio consultado el 2026-10-01). `docs/bill_of_materials.csv` tiene la misma pieza y el mismo precio. Al recalcular el total aparecio un error previo: las partidas sumaban USD 77.29, no los USD 78.29 que decia el informe. Con el buzzer nuevo el total es USD 76.74, en el informe y en el CSV.

## 10. Energia anomala: alerta con una condicion, rele con las dos

**Informe:** linea 108 ("Energia anomala | Baja tension y corriente alta | LED rojo, buzzer, posible rele con retardo") y linea 35.

**Codigo:** el LED rojo y el buzzer se activan con baja tension (4.75 V o menos) o con corriente alta (950 mA o mas) por separado, y el evento informa cual en `alerts` (`low_voltage`, `high_current`). El rele solo arma la secuencia cuando las dos coinciden. Este comportamiento ya existia y se mantuvo. Prueba: `tests/test_control.py`, `test_power_alerts_and_reset_request`.

**Correccion sugerida (linea 108):**

> | Energia anomala | Baja tension o corriente alta | LED rojo, buzzer; si ambas coinciden, reinicio por rele con retardo |
