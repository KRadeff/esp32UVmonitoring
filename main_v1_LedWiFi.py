import machine
import time
import network
import urequests
from bh1750 import BH1750

# 1. Настройки
WIFI_SSID = "DLink 25"
WIFI_PASS = "0123456789."
THINGSPEAK_API_KEY = "K1DOT3XEJOQXWKN1"

# 2. Хардуер
i2c = machine.I2C(0, sda=machine.Pin(21), scl=machine.Pin(22))
sensor = BH1750(i2c)
led = machine.Pin(2, machine.Pin.OUT)  # вграден LED на ESP32 DevKit (GPIO2)


def led_blink_problem():
    """Модел при проблем: мига 1 сек, после свети постоянно 2 сек (неблокиращо извън себе си)."""
    led.value(1)
    time.sleep(1)
    led.value(0)
    time.sleep(1)
    led.value(1)
    time.sleep(2)
    led.value(0)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        led.value(1)
        return True

    # Ако вече тече опит за свързване от преди (state machine на ESP32
    # не позволява нов connect() върху "висящ" connect()), първо го прекратяваме.
    try:
        wlan.disconnect()
    except OSError:
        pass
    time.sleep(0.5)

    print("Свързване с Wi-Fi...")
    try:
        wlan.connect(WIFI_SSID, WIFI_PASS)
    except OSError as e:
        print("Грешка при стартиране на connect():", e)
        return False

    timeout = 20  # ~10 секунди (20 x 0.5s)
    while not wlan.isconnected() and timeout > 0:
        # Мигане през 1 секунда, докато търси/свързва мрежата
        led.value(1)
        time.sleep(0.5)
        led.value(0)
        time.sleep(0.5)
        timeout -= 1

    if not wlan.isconnected():
        print("Грешка: неуспешно свързване с Wi-Fi (timeout).")
        led.value(0)
        # Прекратяваме "висящия" опит, за да не блокира следващия connect()
        try:
            wlan.disconnect()
        except OSError:
            pass
        return False

    print("Физическа връзка ОК. Изчакване на мрежов шлюз...")
    time.sleep(3)
    print("Конфигурация:", wlan.ifconfig())

    led.value(1)  # успешна връзка -> LED свети постоянно
    return True


def send_to_thingspeak(lux, w_m2):
    """Изпраща данните към ThingSpeak през HTTPS (ThingSpeak вече изисква TLS)."""
    url = "https://api.thingspeak.com/update?api_key={}&field1={}&field2={}".format(
        THINGSPEAK_API_KEY, lux, w_m2
    )

    print("Изпращане през HTTPS...")
    response = urequests.get(url)
    try:
        body = response.text.strip()
        print("Отговор от сървъра (status {}): {}".format(response.status_code, body))

        # ThingSpeak връща Entry ID (число > 0) при успешен запис, "0" при грешка
        if response.status_code == 200 and body != "0":
            print("УСПЕХ! Точката е изчертана на графиката (Entry ID: {}).".format(body))
        else:
            print("Внимание: записът не е успешен.")
    finally:
        response.close()  # важно — освобождава сокета/паметта


# 3. Стартова връзка
connect_wifi()

# 4. Главен цикъл
wlan = network.WLAN(network.STA_IF)

while True:
    try:
        if not wlan.isconnected():
            print("Връзката е прекъсната.")
            led_blink_problem()
            if not connect_wifi():
                time.sleep(2)  # пауза преди следващ опит, за да не претоварва WiFi стека
            continue

        lux = sensor.read()
        w_m2 = round(lux * 0.0079, 2)

        print("\nОсветеност: {} lx | Радиация: {} W/m²".format(lux, w_m2))

        send_to_thingspeak(lux, w_m2)

    except OSError as e:
        # Грешки от сокета/мрежата (вкл. timeout, connection reset и др.)
        print("Проблем с мрежата/сокета:", e)
        led_blink_problem()  # мига 1s + свети 2s, докато се опитва да се възстанови
        if not connect_wifi():
            time.sleep(2)

    except Exception as e:
        # Всякакви други неочаквани грешки (напр. сензор)
        print("Неочаквана грешка:", e)

    time.sleep(30)  # ThingSpeak (безплатен план) изисква минимум 15s между записи
