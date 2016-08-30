import RPi.GPIO as GPIO
from daemon import Daemon
import time

class GpioListener(Daemon):
  gpio = [2,3,17,27,22,10,9,11,14,15,18,23,24,25,8,7]
  # 4 is bad?
  
  def check_gpios(self, gpios):
    for chan in iter(GpioListener.gpio.items()):
      gpios[chan] = GPIO.input(chan)
    
    return gpios
  
  def event_checkup(self, channel):
    print("Event caught for {0}".format(channel))
    checks = {}
    self.check_gpios(checks)
    print(checks)
    
  
  def run(self):
    GPIO.setmode(GPIO.BCM)
    
    for chan in GpioListener.gpio:
      print(chan)
      GPIO.setup(chan, GPIO.IN)
      GPIO.add_event_detect(chan, GPIO.BOTH, callback=self.event_checkup, bouncetime=500)
    
    while True:
      time.sleep(300)
