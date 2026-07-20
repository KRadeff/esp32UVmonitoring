import machine
import time
import network
import usocket
import gc
from bh1750 import BH1750
from bme280 import BME280

# 1. Настройки
WIFI_SSID = ""
WIFI_PASS = ""
THINGSPEAK_API_KEY = ""
HTTP_TIMEOUT_S = 8  # максимално чакане за HTTPS заявка, преди да отказваме

# 2. Хардуер
i2c = machine.I2C(0, sda=machine.Pin(21), scl=machine.Pin(22))
sensor = BH1750(i2c)

try:
    bme = BME280(i2c)
    bme_available = True
except OSError as e:
    print("BME280 не е намерен на I2C бус:", e)
    bme = None
    bme_available = False

led = machine.Pin(2, machine.Pin.OUT)  # вграден LED на ESP32 DevKit (GPIO2)

# Собствена HTTPS заявка с явен socket timeout — стандартният urequests
# няма timeout параметър и сокетът му може да увисне неограничено
# при бавна/нестабилна мрежа, което причинява пропуски в графиката.
def request_with_timeout(method, url, timeout=8):
    """
    Минимална собствена имплементация на HTTPS GET с явен socket timeout.
    """
    proto, _, host, path = url.split("/", 3)
    path = "/" + path

    if ":" in host:
        host, port = host.split(":")
        port = int(port)
    else:
        port = 443 if proto == "https:" else 80

    addr = usocket.getaddrinfo(host, port)[0][-1]
    s = usocket.socket()
    s.settimeout(timeout)

    try:
        s.connect(addr)
        if proto == "https:":
            try:
                import ssl as _ssl_module
            except ImportError:
                import ussl as _ssl_module
            s = _ssl_module.wrap_socket(s, server_hostname=host)
            try:
                s.settimeout(timeout)  # не всички версии пренасят timeout-а след wrap
            except AttributeError:
                pass

        request = "{} {} HTTP/1.0\r\nHost: {}\r\nUser-Agent: ESP32\r\nConnection: close\r\n\r\n".format(
            method, path, host
        )
        s.write(request.encode("utf8"))

        raw = b""
        while True:
            chunk = s.read(512)
            if not chunk:
                break
            raw += chunk

    finally:
        s.close()

    header_end = raw.find(b"\r\n\r\n")
    header_text = raw[:header_end].decode("utf8")
    body = raw[header_end + 4:].decode("utf8")
    status_line = header_text.split("\r\n")[0]
    status_code = int(status_line.split(" ")[1])

    return status_code, body


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


def send_to_thingspeak(lux, w_m2, temp=None, hum=None, pres=None):
    """Изпраща данните към ThingSpeak през HTTPS (ThingSpeak вече изисква TLS)."""
    url = "https://api.thingspeak.com/update?api_key={}&field1={}&field2={}".format(
        THINGSPEAK_API_KEY, lux, w_m2
    )
    if temp is not None:
        url += "&field3={}".format(temp)
    if hum is not None:
        url += "&field4={}".format(hum)
    if pres is not None:
        url += "&field5={}".format(pres)

    print("Изпращане през HTTPS...")
    status_code, body = request_with_timeout("GET", url, timeout=HTTP_TIMEOUT_S)
    body = body.strip()
    print("Отговор от сървъра (status {}): {}".format(status_code, body))

    # ThingSpeak връща Entry ID (число > 0) при успешен запис, "0" при грешка
    if status_code == 200 and body != "0":
        print("УСПЕХ! Точката е изчертана на графиката (Entry ID: {}).".format(body))
    else:
        print("Внимание: записът не е успешен.")


def wifi_hard_reset():
    """
    Пълен reset на Wi-Fi интерфейса. Нужен е, когато wlan.isconnected()
    показва True, но DNS/gateway реално не отговарят (грешки -202,
    ETIMEDOUT) - обикновен disconnect()/connect() не помага, защото
    физическата асоциация с рутера остава "жива", докато проблемът
    е на по-високо ниво (DHCP lease, DNS кеш на рутера и т.н.).
    """
    wlan = network.WLAN(network.STA_IF)
    led.value(0)
    try:
        wlan.disconnect()
    except OSError:
        pass
    wlan.active(False)
    time.sleep(1)
    wlan.active(True)
    time.sleep(1)
    connect_wifi()


# 3. Стартова връзка
connect_wifi()

# 4. Главен цикъл
wlan = network.WLAN(network.STA_IF)
consecutive_failures = 0
HARD_RESET_THRESHOLD = 5  # след толкова поредни неуспеха -> пълен WLAN reset

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

        temp = hum = pres = None
        if bme_available:
            try:
                temp, hum, pres = bme.read()
            except OSError as e:
                # BME280 грешка не трябва да блокира изпращането на lux/W/m2 данните
                print("Грешка при четене на BME280:", e)

        if bme_available and temp is not None:
            print("\nОсветеност: {} lx | Радиация: {} W/m² | Темп: {}°C | Влажност: {}% | Налягане: {} hPa".format(
                lux, w_m2, temp, hum, pres
            ))
        else:
            print("\nОсветеност: {} lx | Радиация: {} W/m²".format(lux, w_m2))

        send_to_thingspeak(lux, w_m2, temp, hum, pres)
        consecutive_failures = 0  # успешна заявка -> нулираме брояча

    except OSError as e:
        # Грешки от сокета/мрежата (вкл. timeout, connection reset, DNS -202 и др.)
        print("Проблем с мрежата/сокета:", e)
        led_blink_problem()  # мига 1s + свети 2s, докато се опитва да се възстанови
        consecutive_failures += 1

        if consecutive_failures >= HARD_RESET_THRESHOLD:
            # wlan.isconnected() може да показва True, докато DNS/gateway
            # реално не отговарят (грешки -202, ETIMEDOUT) - обикновен
            # disconnect()/connect() не помага в този случай, затова
            # нулираме целия Wi-Fi интерфейс.
            print("Множество поредни грешки -> пълен reset на Wi-Fi интерфейса.")
            wifi_hard_reset()
            consecutive_failures = 0
        else:
            if not connect_wifi():
                time.sleep(2)

    except MemoryError as e:
        # TLS handshake-ът отнема доста RAM - при недостиг е по-добре да
        # освободим паметта и да изчакаме, вместо да продължаваме веднага.
        print("Недостатъчна памет за HTTPS заявка:", e)
        gc.collect()
        led_blink_problem()
        consecutive_failures += 1

    except Exception as e:
        # Всякакви други неочаквани грешки (напр. сензор, TLS handshake проблем)
        print("Неочаквана грешка:", e)
        led_blink_problem()
        consecutive_failures += 1

    gc.collect()  # TLS обектите фрагментират паметта - чистим след всеки цикъл

    time.sleep(30)  # ThingSpeak (безплатен план) изисква минимум 15s между записи
