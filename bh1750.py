import time

class BH1750:
    def __init__(self, i2c, addr=0x23):
        self.i2c = i2c
        self.addr = addr
        try:
            # Активиране на непрекъснат режим с висока резолюция
            self.i2c.writeto(self.addr, b'\x10')
        except:
            print("Хардуерна грешка: Сензорът не реагира на I2C адреса!")
        time.sleep_ms(180)

    def read(self):
        try:
            data = self.i2c.readfrom(self.addr, 2)
            # Правилно събиране на High и Low байт за MicroPython
            lux = (data[0] << 8 | data[1]) / 1.2
            return round(lux, 2)
        except Exception as e:
            print("Грешка при четене от сензора:", e)
            return 0.0
