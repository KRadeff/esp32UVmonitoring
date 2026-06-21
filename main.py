import machine
import time
import network
import urequests
from bh1750 import BH1750

# 1. Настройки
WIFI_SSID = ""
WIFI_PASS = ""
THINGSPEAK_API_KEY = ""

# 2. Хардуер
i2c = machine.I2C(0, sda=machine.Pin(21), scl=machine.Pin(22))
sensor = BH1750(i2c)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Свързване с Wi-Fi...")
        wlan.connect(WIFI_SSID, WIFI_PASS)

        timeout = 20  # ~10 секунди (20 x 0.5s)
        while not wlan.isconnected() and timeout > 0:
            time.sleep(0.5)
            timeout -= 1

        if not wlan.isconnected():
            print("Грешка: неуспешно свързване с Wi-Fi (timeout).")
            return False

    print("Физическа връзка ОК. Изчакване на мрежов шлюз...")
    time.sleep(3)
    print("Конфигурация:", wlan.ifconfig())
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
while True:
    try:
        lux = sensor.read()
        w_m2 = round(lux * 0.0079, 2)

        print("\nОсветеност: {} lx | Радиация: {} W/m²".format(lux, w_m2))

        send_to_thingspeak(lux, w_m2)

    except OSError as e:
        # Грешки от сокета/мрежата (вкл. timeout, connection reset и др.)
        print("Проблем с мрежата/сокета:", e)
        connect_wifi()

    except Exception as e:
        # Всякакви други неочаквани грешки (напр. сензор)
        print("Неочаквана грешка:", e)

    time.sleep(30)  # ThingSpeak (безплатен план) изисква минимум 15s между записи
