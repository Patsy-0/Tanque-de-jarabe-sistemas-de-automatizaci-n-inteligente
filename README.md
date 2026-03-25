# Simulación IoT: Control y Monitoreo de Contenedor de Jarabe Farmacéutico

Este repositorio contiene la arquitectura, código y configuración para un sistema de automatización inteligente aplicado a un contenedor de jarabe farmacéutico. 

El proyecto simula el control de variables físicas, gestión de actuadores mediante **AWS Device Shadow**, registro de métricas en **DynamoDB**, alertas por **SNS**, y telemetría visualizada en un Dashboard de **Node-RED**, usando una **ESP32 simulada en Wokwi** como el borde (Edge).

## Arquitectura del Sistema

1. **Hardware / Edge (Wokwi - ESP32 con MicroPython):** Simula los sensores del contenedor y controla los actuadores (LEDs indicadores, Buzzer, relés virtuales) reaccionando al estado reportado (`reported`) del Device Shadow y a las reglas locales.
2. **Bróker y Lógica en la Nube (AWS IoT Core):**
   * **MQTT Topics:** Recepción de telemetría y eventos.
   * **Device Shadow:** Sincronización de estado (`desired` → `delta` → `reported`) para controlar enfriadores, calentadores, válvulas y bombas.
   * **AWS Rules:** Enrutamiento de datos hacia la base de datos y alertas.
3. **Almacenamiento y Notificaciones (AWS DynamoDB & SNS):**
   * **DynamoDB:** Guarda un registro histórico de todos los eventos de cambio de estado.
   * **SNS:** Envía correos electrónicos inmediatos al personal cuando se activa una emergencia.
4. **Controlador, UI y Simulación Física (Node-RED):** Injecta variables físicas para la simulación

---

## 📋 Tabla de Eventos (Diseño y Clasificación)

El sistema maneja eventos divididos en dos categorías, cumpliendo con la regla de desduplicación (dedupe) para no generar spam: solo se dispara la acción y el registro cuando el estado cambia (ej. de `OK` a `ALERTA`).

| ID | Nombre del Evento | Tipo de Evento | Condición de Disparo | Acción del Sistema (Shadow / Emergencia) | Registro (DynamoDB / SNS) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **EV1** | **Temperatura ALTA** | Shadow (Control) | Temp > 25°C | `desired` enfriamiento: ON. | DynamoDB: Sí |
| **EV2** | **Temperatura BAJA** | Shadow (Control) | Temp < 20°C | `desired` calentamiento: ON. | DynamoDB: Sí |
| **EV3** | **Sobrellenado** | Shadow (Control) | Volumen > 85% | `desired` válvula_salida: ON (Drenaje). | DynamoDB: Sí |
| **EV4** | **Concentración Anómala** | Shadow (Control) | Brix < 64 o > 67 | `desired` bomba_mezcla: ON, cambia LED. | DynamoDB: Sí |
| **EV5** | **Fuga Crítica (Volumen Bajo)**| Emergencia Inmediata| Volumen < 20% | Publica en topic de emergencia / Buzzer ON. | DynamoDB: Sí / SNS: Email |
| **EV6** | **Fallo de Sensor/Sistema** | Emergencia Inmediata| Botón manual presionado en Wokwi/Dashboard | Publica alerta de paro de emergencia. | DynamoDB: Sí / SNS: Email |

*(Nota para el equipo: Si son 3 integrantes usen 5 eventos; si son 4, dejen los 6 o agreguen un séptimo como "Sobrecalentamiento Crítico > 30°C").*

---

## 🛠️ Robustez Técnica y Manejo de Estados

* **Dedupe (Prevención de Spam):** La lógica en Node-RED (y en MicroPython) incluye nodos y variables de estado que memorizan si una alerta ya fue enviada. Hasta que la variable no regresa a un estado "Estable/Normal", no se vuelve a enviar un correo de SNS ni se duplican registros en DynamoDB.
* **Flujo del Device Shadow:**
  1. Node-RED detecta un problema (ej. Temp Alta) y publica un cambio en `shadow/update` como estado **`desired`** (enfriador = true).
  2. AWS IoT detecta la diferencia y publica en el topic **`delta`**.
  3. La ESP32 en Wokwi recibe el `delta`, enciende el LED correspondiente (actuador) y publica de vuelta un estado **`reported`** confirmando que el enfriador está encendido.

---

## 🚀 Instrucciones de Ejecución

### 1. Requisitos Previos en AWS
* Tener configurado un **Thing** en AWS IoT Core con sus certificados.
* Tener una tabla en **DynamoDB** con una Partition Key válida (ej. `id_evento` o `timestamp`).
* Tener un **Topic SNS** creado con una suscripción de correo electrónico confirmada.
* Tener las **Reglas (Rules)** de AWS IoT configuradas para enrutar los mensajes desde el topic `equipo1/+/event` hacia DynamoDB y SNS.

### 2. Configuración del Hardware (Wokwi)
1. Abrir el entorno Wokwi para ESP32 con MicroPython.
2. Cargar la configuración de pines desde `diagram.json`.
3. Subir `main.py` y `secrets_der.py`.
   * *⚠️ Asegurarse de que `secrets_der.py` contiene los certificados reales del equipo (no incluidos en este repo público por seguridad).*
4. Ejecutar la simulación. La consola mostrará la conexión WiFi y la suscripción exitosa a los topics MQTT.

### 3. Configuración de la Lógica (Node-RED)
1. Instalar el paquete `@flowfuse/node-red-dashboard` en Node-RED.
2. Importar el archivo `flows_ajustes_inercia.json`.
3. Configurar los nodos MQTT (`mqtt in` / `mqtt out`) con el Endpoint de AWS y los certificados TLS.
4. Presionar **Deploy** y abrir el Dashboard en `http://localhost:1880/dashboard`.

### 4. Pruebas y Evidencia (Troubleshooting)
Para demostrar el funcionamiento durante la evaluación:
1. Usa el Dashboard de Node-RED para inyectar una falla (ej. *Subir Temperatura*).
2. Observa el Dashboard cómo cambia de estado.
3. Verifica en Wokwi que el LED del enfriador se encienda (Shadow *delta* → *reported*).
4. Entra a DynamoDB y verifica que el evento se haya guardado con sus campos.
5. Fuerza el "Volumen Bajo" y revisa tu bandeja de entrada para validar el correo de SNS.
