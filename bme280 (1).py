"""
Компактен MicroPython драйвер за BME280 (температура, влажност, налягане).

Базиран на официалния Bosch BME280 datasheet (BST-BME280-DS002), използва
I2C интерфейс. Поддържа стандартните I2C адреси 0x76 и 0x77 (auto-detect).

Употреба:
    from bme280 import BME280
    bme = BME280(i2c)
    temp, hum, pres = bme.read()   # °C, %RH, hPa
"""

import time
from ustruct import unpack


class BME280:
    def __init__(self, i2c, address=None):
        self.i2c = i2c

        if address is None:
            # Auto-detect между двата стандартни адреса
            devices = i2c.scan()
            for addr in (0x76, 0x77):
                if addr in devices:
                    address = addr
                    break
            if address is None:
                raise OSError("BME280 не е намерен на I2C бус (нито 0x76, нито 0x77)")

        self.address = address
        self._load_calibration()

        # Конфигурация: humidity oversampling x1, после temp/pressure x1, normal mode
        self.i2c.writeto_mem(self.address, 0xF2, b"\x01")  # ctrl_hum: osrs_h = 1
        self.i2c.writeto_mem(self.address, 0xF4, b"\x27")  # ctrl_meas: osrs_t=1, osrs_p=1, mode=normal
        self.i2c.writeto_mem(self.address, 0xF5, b"\xA0")  # config: standby 1000ms, filter off
        time.sleep_ms(100)

    def _load_calibration(self):
        calib = self.i2c.readfrom_mem(self.address, 0x88, 24)
        (
            self.dig_T1, self.dig_T2, self.dig_T3,
            self.dig_P1, self.dig_P2, self.dig_P3,
            self.dig_P4, self.dig_P5, self.dig_P6,
            self.dig_P7, self.dig_P8, self.dig_P9,
        ) = unpack("<HhhHhhhhhhhh", calib)

        dig_h1 = self.i2c.readfrom_mem(self.address, 0xA1, 1)[0]
        self.dig_H1 = dig_h1

        # E1..E7 (7 байта, индекси 0-6):
        #   E1,E2 = dig_H2 (signed, LE)
        #   E3    = dig_H3
        #   E4    = dig_H4[11:4]
        #   E5    = dig_H4[3:0] (bits 0-3) | dig_H5[3:0] (bits 4-7)
        #   E6    = dig_H5[11:4]
        #   E7    = dig_H6 (signed)
        h_calib = self.i2c.readfrom_mem(self.address, 0xE1, 7)
        self.dig_H2 = unpack("<h", h_calib[0:2])[0]
        self.dig_H3 = h_calib[2]

        e4 = h_calib[3]
        e5 = h_calib[4]
        e6 = h_calib[5]

        self.dig_H4 = (e4 << 4) | (e5 & 0x0F)
        if self.dig_H4 > 2047:
            self.dig_H4 -= 4096
        self.dig_H5 = (e6 << 4) | (e5 >> 4)
        if self.dig_H5 > 2047:
            self.dig_H5 -= 4096

        dig_h6 = h_calib[6]
        if dig_h6 > 127:
            dig_h6 -= 256
        self.dig_H6 = dig_h6

    def read(self):
        """Връща (temperature_C, humidity_percent, pressure_hPa) като float."""
        data = self.i2c.readfrom_mem(self.address, 0xF7, 8)

        raw_press = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        raw_temp = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        raw_hum = (data[6] << 8) | data[7]

        # --- Температурна компенсация (Bosch datasheet формула) ---
        var1 = (raw_temp / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        var2 = (
            (raw_temp / 131072.0 - self.dig_T1 / 8192.0)
            * (raw_temp / 131072.0 - self.dig_T1 / 8192.0)
            * self.dig_T3
        )
        t_fine = var1 + var2
        temperature = t_fine / 5120.0

        # --- Налягане ---
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * self.dig_P6 / 32768.0
        var2 = var2 + var1 * self.dig_P5 * 2.0
        var2 = var2 / 4.0 + self.dig_P4 * 65536.0
        var1 = (self.dig_P3 * var1 * var1 / 524288.0 + self.dig_P2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * self.dig_P1

        if var1 == 0:
            pressure = 0.0
        else:
            p = 1048576.0 - raw_press
            p = (p - var2 / 4096.0) * 6250.0 / var1
            var1 = self.dig_P9 * p * p / 2147483648.0
            var2 = p * self.dig_P8 / 32768.0
            pressure = p + (var1 + var2 + self.dig_P7) / 16.0
            pressure = pressure / 100.0  # Pa -> hPa

        # --- Влажност ---
        h = t_fine - 76800.0
        h = (raw_hum - (self.dig_H4 * 64.0 + self.dig_H5 / 16384.0 * h)) * (
            self.dig_H2
            / 65536.0
            * (
                1.0
                + self.dig_H6 / 67108864.0 * h * (1.0 + self.dig_H3 / 67108864.0 * h)
            )
        )
        humidity = h * (1.0 - self.dig_H1 * h / 524288.0)

        if humidity > 100.0:
            humidity = 100.0
        elif humidity < 0.0:
            humidity = 0.0

        return round(temperature, 2), round(humidity, 2), round(pressure, 2)
