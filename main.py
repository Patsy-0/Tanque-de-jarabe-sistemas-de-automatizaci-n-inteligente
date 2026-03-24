import gc
import json
import time
import network
import ubinascii

from secrets_der import DEVICE_CERT_DER_B64, PRIVATE_KEY_DER_B64, ROOT_CA_DER_B64

DEVICE_CERT = ubinascii.a2b_base64(DEVICE_CERT_DER_B64)
PRIVATE_KEY = ubinascii.a2b_base64(PRIVATE_KEY_DER_B64)
ROOT_CA = ubinascii.a2b_base64(ROOT_CA_DER_B64)

# ============================================================
# CONFIG GENERAL
# ============================================================
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASS = ""

AWS_ENDPOINT = "a149b4hefflb74-ats.iot.us-east-2.amazonaws.com"
THING_NAME = "equipo1_camara_fria_01"
CLIENT_ID = THING_NAME
MQTT_PORT = 8883
KEEPALIVE = 60

TOPIC_BASE = "equipo1/vacunas/camara_fria_01"
TOPIC_TELEMETRY = TOPIC_BASE + "/telemetry"
TOPIC_EVENT = TOPIC_BASE + "/event"

SHADOW_BASE = "$aws/things/{}/shadow".format(THING_NAME)
TOPIC_SHADOW_UPDATE = SHADOW_BASE + "/update"
TOPIC_SHADOW_GET = SHADOW_BASE + "/get"
TOPIC_SHADOW_DELTA = SHADOW_BASE + "/update/delta"
TOPIC_SHADOW_UPD_ACCEPTED = SHADOW_BASE + "/update/accepted"
TOPIC_SHADOW_UPD_REJECTED = SHADOW_BASE + "/update/rejected"
TOPIC_SHADOW_GET_ACCEPTED = SHADOW_BASE + "/get/accepted"
TOPIC_SHADOW_GET_REJECTED = SHADOW_BASE + "/get/rejected"

TICK_MS = 5000
IDEAL_MIN = 4.0
IDEAL_MAX = 5.0
RECOVERY_TARGET_C = 5.0   # salir de emergencia cuando T < 5°C
DOOR_WARN_S = 30
DOOR_AUTO_CLOSE_S = 45
EMERGENCY_THRESHOLD_C = 10.0

# ============================================================
# HARDWARE (WOKWI)
# ============================================================
PIN_DHT = 15
PIN_BUTTON_OPEN = 4
PIN_LED_GREEN = 18          # lock/light verde existente
PIN_LED_RED = 19            # lock/light rojo existente
PIN_LED_ALERT = 21          # alerta visual
PIN_LED_EMERGENCY = 22      # A/C de emergencia
PIN_LED_DOOR_OPEN = 23      # NUEVO: estado de puerta (ON=open, OFF=closed)
PIN_BUZZER = 25
PIN_ULTRASONIC_TRIG = 26
PIN_ULTRASONIC_ECHO = 27
PIN_LED_FILL_HIGH = 32    # LED1: 70-100% (vacía)
PIN_LED_FILL_MID  = 16    # LED3: 30-70%  (mitad)
PIN_LED_FILL_LOW  = 2    # LED2: 0-30%   (llena)
# ============================================================
# GLOBALES INICIALIZADAS TARDE
# ============================================================
Pin = None
PWM = None
DHT22 = None
MQTTClient = None
ssl = None
ntptime = None

sensor = None
button_open = None
led_green = None
led_red = None
led_alert = None
led_emergency = None
led_door_open = None
buzzer_pwm = None
ultrasonic_trig = None
ultrasonic_echo = None
led_fill_high = None
led_fill_mid  = None
led_fill_low  = None
mqtt = None

# ============================================================
# ESTADO DEL SISTEMA
# ============================================================
state = {
    "telemetry": {
        "ts": 0,
        "temperature_c": 4.5,
        "humidity_rh": 76.0,
        "distance_cm":0.0,
        "door": "CLOSED",
        "door_open_s": 0,
        "phase": "NORMAL",
    },
    "actuators": {
        "door_lock": False,
        "door_light": "GREEN",
        "door_open_led": False,
        "emergency_ac": False,
        "alert_led": False,
        "led_high":False,
        "led_mid": False,
        "led_low":False,
        "buzzer": False,
    },
    "shadow": {
        "reported": {
            "door": "CLOSED",
            "door_lock": False,
            "door_light": "GREEN",
            "emergency_ac": False,
            "alert_led": False,
            "valvula_dsminuir_vol":False,
            "valvula_cerrada": False,
            "valvula_agregar_vol":False,
            "buzzer": False,
        },
        "commanded": {
            "door_lock": False,
        },
        "aws": {
            "lastDeltaVersion": None,
            "lastAcceptedVersion": None,
            "lastRejected": None,
        },
    },
    "plant": {
        "door_opened_at_ms": None,
        "pending_open_request": False,
        "warn_notified": False,
        "auto_close_notified": False,
        "emergency_notified": False,
        "emergency_latched": False,
    },
    "last_event": {
        "key": None,
        "ts": 0,
    },
    "pending_event": None,
    "publish_now": False,
    "last_button": 1,
    "last_shadow_sent": None,
}

# ============================================================
# HELPERS
# ============================================================
def now_ms():
    return time.ticks_ms()


def safe_json(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def json_bytes(obj):
    return json.dumps(obj).encode("utf-8")


def dedup_event_key(event_type, text):
    return "{}|{}".format(event_type, text)


def queue_event(event_type, text, beep=False, times_=2, force=False):
    key = dedup_event_key(event_type, text)
    t = time.time() if hasattr(time, "time") else 0
    last = state["last_event"]

    if not force and last["key"] == key and (t - last["ts"]) < 3:
        return

    state["pending_event"] = {
        "text": text,
        "type": event_type,
        "beep": bool(beep),
        "times": times_,
        "eventKey": key,
        "ts": t,
        "source": "esp32_micropython_wokwi",
    }
    state["last_event"] = {"key": key, "ts": t}
    state["publish_now"] = True


def wifi_connect():
    print("Connecting to WiFi", end="")
    sta_if = network.WLAN(network.STA_IF)
    sta_if.active(True)
    sta_if.connect(WIFI_SSID, WIFI_PASS)
    while not sta_if.isconnected():
        print(".", end="")
        time.sleep(0.1)
    print(" Connected!")
    print("WiFi OK:", sta_if.ifconfig())
    return sta_if


def late_init():
    global Pin, PWM, DHT22, MQTTClient, ssl, ntptime
    global sensor, button_open, led_green, led_red, led_alert, led_emergency, led_door_open, buzzer_pwm
    global ultrasonic_trig,ultrasonic_echo,led_fill_high, led_fill_mid, led_fill_low  
    gc.collect()
    print("mem libre antes de imports pesados:", gc.mem_free())

    from machine import Pin, PWM, time_pulse_us
    from dht import DHT22

    try:
        import ntptime
    except ImportError:
        ntptime = None

    try:
        from umqtt.robust import MQTTClient
    except ImportError:
        from umqtt.simple import MQTTClient

    try:
        import ssl
    except ImportError:
        ssl = None

    print("CERT DER OK:", len(DEVICE_CERT))
    print("KEY DER OK:", len(PRIVATE_KEY))
    print("CA DER OK:", len(ROOT_CA))

    sensor = DHT22(Pin(PIN_DHT))
    button_open = Pin(PIN_BUTTON_OPEN, Pin.IN, Pin.PULL_UP)
    state["last_button"] = button_open.value()

    led_green = Pin(PIN_LED_GREEN, Pin.OUT)
    led_red = Pin(PIN_LED_RED, Pin.OUT)
    led_alert = Pin(PIN_LED_ALERT, Pin.OUT)
    led_emergency = Pin(PIN_LED_EMERGENCY, Pin.OUT)
    led_door_open = Pin(PIN_LED_DOOR_OPEN, Pin.OUT)
    buzzer_pwm = PWM(Pin(PIN_BUZZER), freq=2000, duty=0)
    ultrasonic_trig = Pin(PIN_ULTRASONIC_TRIG, Pin.OUT)
    ultrasonic_echo = Pin(PIN_ULTRASONIC_ECHO, Pin.IN)
    led_fill_high = Pin(PIN_LED_FILL_HIGH, Pin.OUT)
    led_fill_mid  = Pin(PIN_LED_FILL_MID,  Pin.OUT)
    led_fill_low  = Pin(PIN_LED_FILL_LOW,  Pin.OUT)
    gc.collect()
    print("mem libre después de imports pesados:", gc.mem_free())


def sync_time():
    if ntptime is None:
        print("ntptime no disponible; continúo sin sincronizar RTC")
        return
    try:
        ntptime.settime()
        print("RTC sincronizado por NTP")
    except Exception as e:
        print("No se pudo sincronizar NTP:", e)


def build_ssl_params(with_ca=True):
    params = {
        "cert": DEVICE_CERT,
        "key": PRIVATE_KEY,
        "server_hostname": AWS_ENDPOINT,
    }
    if with_ca and ssl is not None:
        params["cert_reqs"] = ssl.CERT_REQUIRED
        params["cadata"] = ROOT_CA
    return params


def make_client(with_ca=True):
    params = build_ssl_params(with_ca=with_ca)
    client = MQTTClient(
        client_id=CLIENT_ID,
        server=AWS_ENDPOINT,
        port=MQTT_PORT,
        keepalive=KEEPALIVE,
        ssl=True,
        ssl_params=params,
    )
    client.set_callback(on_mqtt_message)
    return client


def mqtt_connect():
    global mqtt
    mqtt = make_client(with_ca=True)
    mqtt.connect(False)
    print("MQTT TLS conectado")


def mqtt_subscribe_all():
    mqtt.subscribe(TOPIC_SHADOW_DELTA, qos=0)
    mqtt.subscribe(TOPIC_SHADOW_UPD_ACCEPTED, qos=0)
    mqtt.subscribe(TOPIC_SHADOW_UPD_REJECTED, qos=0)
    mqtt.subscribe(TOPIC_SHADOW_GET_ACCEPTED, qos=0)
    mqtt.subscribe(TOPIC_SHADOW_GET_REJECTED, qos=0)
    print("Suscripciones shadow OK")


def mqtt_sync_shadow():
    mqtt.publish(TOPIC_SHADOW_GET, b"", qos=0)
    print("Shadow GET enviado")


def merge_desired_into_commanded(desired_obj):
    if not isinstance(desired_obj, dict):
        return

    if "door_lock" in desired_obj:
        value = desired_obj["door_lock"]
        if value is None:
            return

        if isinstance(value, bool):
            state["shadow"]["commanded"]["door_lock"] = value
            print("desired.door_lock ->", value)
            state["publish_now"] = True
        else:
            queue_event(
                "info",
                "Desired inválido: door_lock debe ser boolean",
                beep=False,
                times_=1,
            )


def on_mqtt_message(topic, msg):
    if isinstance(topic, bytes):
        topic = topic.decode("utf-8")

    payload = safe_json(msg)
    print("MQTT RX:", topic)

    if topic == TOPIC_SHADOW_DELTA and payload:
        state["shadow"]["aws"]["lastDeltaVersion"] = payload.get("version")
        merge_desired_into_commanded(payload.get("state", {}))
        state["publish_now"] = True

    elif topic == TOPIC_SHADOW_GET_ACCEPTED and payload:
        desired = payload.get("state", {}).get("desired", {})
        merge_desired_into_commanded(desired)
        state["publish_now"] = True

    elif topic == TOPIC_SHADOW_UPD_ACCEPTED and payload:
        state["shadow"]["aws"]["lastAcceptedVersion"] = payload.get("version")

    elif topic == TOPIC_SHADOW_UPD_REJECTED and payload:
        err = payload.get("message") or payload.get("error") or str(payload)
        state["shadow"]["aws"]["lastRejected"] = err
        queue_event("warn", "Shadow update rejected: {}".format(err), beep=True, times_=2)

    elif topic == TOPIC_SHADOW_GET_REJECTED and payload:
        err = payload.get("message") or payload.get("error") or str(payload)
        queue_event("warn", "Shadow get rejected: {}".format(err), beep=True, times_=2)

def drive_fill_leds():
    act = state["actuators"]
    ULTRASONIC_MAX_CM = 400.0
    if led_fill_high is None:
        return
    dist = state["telemetry"].get("distance_cm", 0.0)
    pct = min(100.0, max(0.0, (dist / ULTRASONIC_MAX_CM) * 100.0))

    led_fill_low.value(1 if pct >= 70 else 0)  
    led_fill_mid.value( 1 if 30 <= pct < 70 else 0)  
    led_fill_high.value( 1 if pct < 30 else 0)   
    act["led_high"] = bool(led_fill_high.value())
    act["led_mid"]  = bool(led_fill_mid.value())
    act["led_low"]  = bool(led_fill_low.value())
    controlar_valvulas()

def drive_outputs():
    if led_green is None:
        return

    rep = state["shadow"]["reported"]

    led_green.value(1 if rep["door_light"] == "GREEN" else 0)
    led_red.value(1 if rep["door_light"] == "RED" else 0)
    led_alert.value(1 if rep["alert_led"] else 0)
    led_emergency.value(1 if rep["emergency_ac"] else 0)
    led_door_open.value(1 if rep["door"] == "OPEN" else 0)
    
    if rep["buzzer"]:
        buzzer_pwm.freq(2000)
        buzzer_pwm.duty(512)
    else:
        buzzer_pwm.duty(0)
    drive_fill_leds()
    

def button_edge_detect():
    if button_open is None:
        return

    current = button_open.value()
    previous = state["last_button"]
    state["last_button"] = current

    # Pull-up: reposo=1, presionado=0
    if previous == 1 and current == 0:
        state["plant"]["pending_open_request"] = True
        print("Botón OPEN presionado")


def update_sensor_values():
    if sensor is None:
        return

    try:
        sensor.measure()
        temp_c = round(float(sensor.temperature()), 2)
        hum_rh = round(float(sensor.humidity()), 1)
        state["telemetry"]["temperature_c"] = temp_c
        state["telemetry"]["humidity_rh"] = hum_rh
        dist = read_distance_cm()
        if dist is not None:
            state["telemetry"]["distance_cm"] = dist
    except Exception as e:
        print("Lectura DHT22 falló, conservo último valor:", e)
    
    
def read_distance_cm():
    from machine import time_pulse_us

    if ultrasonic_trig is None or ultrasonic_echo is None:
        return None
    ultrasonic_trig.value(0)
    time.sleep_us(2)
    ultrasonic_trig.value(1)
    time.sleep_us(10)
    ultrasonic_trig.value(0)
    duration = time_pulse_us(ultrasonic_echo, 1, 30000)
    if duration < 0:
        return None
    return round(duration / 58.0, 1)

def controlar_valvulas():
    rep = state["shadow"]["reported"]
    act = state["actuators"]
    
    # Usamos act["led_high"] que ya actualizamos en la función anterior
    if act["led_high"]:
        rep["valvula_disminuir_vol"] = True
        rep["valvula_cerrada"] = False
        rep["valvula_agregar_vol"] = False
        
    elif act["led_mid"]:
        rep["valvula_disminuir_vol"] = False
        rep["valvula_cerrada"] = True 
        rep["valvula_agregar_vol"] = False
        
    elif act["led_low"]:
        rep["valvula_disminuir_vol"] = False
        rep["valvula_cerrada"] = False
        rep["valvula_agregar_vol"] = True
    else:
         rep["valvula_disminuir_vol"] = False
         rep["valvula_cerrada"] = True 
         rep["valvula_agregar_vol"] = False


def apply_commanded_to_reported():
    cmd = state["shadow"]["commanded"]
    rep = state["shadow"]["reported"]
    if isinstance(cmd.get("door_lock"), bool):
        rep["door_lock"] = cmd["door_lock"]
        


def handle_open_request():
    if not state["plant"]["pending_open_request"]:
        return

    state["plant"]["pending_open_request"] = False
    rep = state["shadow"]["reported"]
    plant = state["plant"]
    temp = state["telemetry"]["temperature_c"]
    temp_ideal = IDEAL_MIN <= temp <= IDEAL_MAX

    # No se puede abrir durante emergencia enclavada
    can_open = (not rep["door_lock"]) and (not plant["emergency_latched"]) and temp_ideal

    if can_open and rep["door"] != "OPEN":
        rep["door"] = "OPEN"
        plant["door_opened_at_ms"] = now_ms()
        state["telemetry"]["door"] = "OPEN"
        state["telemetry"]["door_open_s"] = 0
        plant["warn_notified"] = False
        plant["auto_close_notified"] = False
        queue_event("info", "Puerta abierta por botón local", beep=False, times_=1)
        state["publish_now"] = True
    else:
        if plant["emergency_latched"]:
            msg = "Puerta bloqueada: recuperación térmica en curso"
        else:
            msg = "Puerta bloqueada: espera 4-5°C y sin emergencia"
        queue_event("info", msg, beep=False, times_=1)
        state["publish_now"] = True


def recompute_logic():
    rep = state["shadow"]["reported"]
    tel = state["telemetry"]
    plant = state["plant"]

    temp_c = tel["temperature_c"]
    temp_ideal = IDEAL_MIN <= temp_c <= IDEAL_MAX
    recovered_from_emergency = temp_c < RECOVERY_TARGET_C

    if rep["door"] == "OPEN" and plant["door_opened_at_ms"] is not None:
        open_s = time.ticks_diff(now_ms(), plant["door_opened_at_ms"]) // 1000
    else:
        open_s = 0

    tel["door_open_s"] = int(open_s)
    tel["door"] = rep["door"]

    over_warn = rep["door"] == "OPEN" and open_s > DOOR_WARN_S
    over_auto = rep["door"] == "OPEN" and open_s > DOOR_AUTO_CLOSE_S
    emergency_trigger = temp_c > EMERGENCY_THRESHOLD_C

    apply_commanded_to_reported()

    # Entrada a emergencia: se enclava al superar 10°C
    if emergency_trigger and not plant["emergency_latched"]:
        plant["emergency_latched"] = True

    # Si hay emergencia enclavada, manda la física por encima de todo
    if plant["emergency_latched"]:
        if rep["door"] != "CLOSED":
            rep["door"] = "CLOSED"
        plant["door_opened_at_ms"] = None
        tel["door_open_s"] = 0
        rep["door_lock"] = True
        rep["emergency_ac"] = True
        rep["alert_led"] = True
        rep["buzzer"] = True
        rep["door_light"] = "RED"
        tel["phase"] = "EMERGENCY_COOLING"

        if not plant["emergency_notified"]:
            queue_event(
                "warn",
                "EMERGENCIA: temperatura crítica > {}°C".format(EMERGENCY_THRESHOLD_C),
                beep=True,
                times_=2,
            )
            plant["emergency_notified"] = True

        # Salida de emergencia SOLO cuando T < 5°C
        if recovered_from_emergency:
            plant["emergency_latched"] = False
            plant["emergency_notified"] = False

            rep["door"] = "CLOSED"
            rep["door_lock"] = False
            rep["emergency_ac"] = False
            rep["alert_led"] = False
            rep["buzzer"] = False
            rep["door_light"] = "GREEN" if temp_ideal else "RED"
            tel["door_open_s"] = 0
            tel["phase"] = "NORMAL"

            queue_event(
                "ok",
                "Recuperado: temperatura < {}°C, A/C OFF, alertas OFF y puerta desbloqueada".format(RECOVERY_TARGET_C),
                beep=False,
                times_=1,
            )

    else:
        # Operación normal: una puerta abierta no puede estar bloqueada
        if rep["door"] == "OPEN":
            rep["door_lock"] = False

        if over_auto:
            if rep["door"] != "CLOSED":
                rep["door"] = "CLOSED"
                plant["door_opened_at_ms"] = None
                tel["door_open_s"] = 0
            rep["door_lock"] = True

        rep["emergency_ac"] = False
        rep["alert_led"] = True if over_warn else False
        rep["buzzer"] = True if over_warn else False

        if rep["door"] == "OPEN":
            rep["door_light"] = "RED"
            tel["phase"] = "DOOR_OPEN"
        else:
            rep["door_light"] = "GREEN" if temp_ideal else "RED"
            tel["phase"] = "NORMAL"

    tel["ts"] = time.time() if hasattr(time, "time") else 0
    tel["door"] = rep["door"]

    if over_auto and not plant["auto_close_notified"] and not plant["emergency_latched"]:
        queue_event(
            "warn",
            "ACTUADOR: puerta abierta > {}s, se cierra y bloquea".format(DOOR_AUTO_CLOSE_S),
            beep=True,
            times_=2,
        )
        plant["auto_close_notified"] = True
    elif not over_auto:
        plant["auto_close_notified"] = False

    if over_warn and not plant["warn_notified"] and not plant["emergency_latched"]:
        queue_event(
            "warn",
            "ALERTA: puerta abierta > {}s".format(DOOR_WARN_S),
            beep=True,
            times_=2,
        )
        plant["warn_notified"] = True
    elif not over_warn:
        plant["warn_notified"] = False

    state["actuators"] = {
        "door_lock": rep["door_lock"],
        "door_light": rep["door_light"],
        "door_open_led": True if rep["door"] == "OPEN" else False,
        "emergency_ac": rep["emergency_ac"],
        "alert_led": rep["alert_led"],
        "buzzer": rep["buzzer"],
    }


def build_telemetry_payload():
    return {
        "cfg": {
            "thingName": THING_NAME,
            "topicBase": TOPIC_BASE,
            "idealMin": IDEAL_MIN,
            "idealMax": IDEAL_MAX,
            "doorWarnSeconds": DOOR_WARN_S,
            "doorAutoCloseSeconds": DOOR_AUTO_CLOSE_S,
            "emergencyThresholdC": EMERGENCY_THRESHOLD_C,
            "recoveryTargetC": RECOVERY_TARGET_C,
        },
        "telemetry": state["telemetry"],
        "actuators": state["actuators"],
        "shadow": {
            "reported": state["shadow"]["reported"],
            "commanded": state["shadow"]["commanded"],
            "aws": state["shadow"]["aws"],
        },
    }


def publish_telemetry():
    payload = build_telemetry_payload()
    mqtt.publish(TOPIC_TELEMETRY, json_bytes(payload), qos=0)
    print("TX telemetry:", payload)


def publish_shadow_reported(force=False):
    rep = state["shadow"]["reported"]
    current = json.dumps(rep)

    if (not force) and (current == state["last_shadow_sent"]):
        return

    payload = {
        "state": {
            "reported": rep
        }
    }

    mqtt.publish(TOPIC_SHADOW_UPDATE, json_bytes(payload), qos=0)
    state["last_shadow_sent"] = current
    print("TX shadow reported:", payload)


def publish_pending_event():
    evt = state.get("pending_event")
    if not evt:
        return

    mqtt.publish(TOPIC_EVENT, json_bytes(evt), qos=0)
    print("EVENT JSON:", json.dumps(evt))
    state["pending_event"] = None


def flush_snapshot(force_shadow=False):
    publish_telemetry()
    publish_shadow_reported(force=force_shadow)
    publish_pending_event()


def keep_mqtt_alive():
    try:
        mqtt.check_msg()
    except Exception as e:
        print("MQTT check_msg falló, reconectando:", e)
        reconnect_all()


def reconnect_all():
    global mqtt
    try:
        if mqtt is not None:
            mqtt.disconnect()
    except Exception:
        pass

    wifi_connect()
    sync_time()
    mqtt_connect()
    mqtt_subscribe_all()
    mqtt_sync_shadow()
    recompute_logic()
    drive_outputs()
    publish_shadow_reported(force=True)
    publish_telemetry()


def main():
    print("=== ESP32 MicroPython + AWS IoT Core + Shadow ===")

    wifi_connect()
    late_init()
    sync_time()
    mqtt_connect()
    mqtt_subscribe_all()
    mqtt_sync_shadow()

    update_sensor_values()
    recompute_logic()
    drive_outputs()
    flush_snapshot(force_shadow=True)

    last_tick = now_ms()

    while True:
        keep_mqtt_alive()
        button_edge_detect()
        handle_open_request()
        recompute_logic()
        drive_outputs()

        # Publicación inmediata cuando hay evento local o cambio por shadow
        if state["publish_now"]:
            flush_snapshot(force_shadow=False)
            state["publish_now"] = False

        # Telemetría periódica cada 5 s
        if time.ticks_diff(now_ms(), last_tick) >= TICK_MS:
            last_tick = now_ms()
            update_sensor_values()
            recompute_logic()
            drive_outputs()
            flush_snapshot(force_shadow=False)
            gc.collect()

        time.sleep_ms(100)


try:
    main()
except Exception as e:
    print("Fallo fatal:", e)
    while True:
        time.sleep(1)
