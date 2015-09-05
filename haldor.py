
import time, signal, subprocess, http.client, urllib, hmac, hashlib, re
#from functools import partial
import RPi.GPIO as GPIO
from daemon import Daemon

class Haldor(Daemon):
  """Watches the door and monitors various switches and motion via GPIO"""

  # TODO: enable these as configs passed to __init__
  version = "0.0.4"
  io_channels = [7, 8, 25, 11, 24]
  io_names = {'Front Door': 7, 'Main Door': 8, 'Office Motion': 25, 'Shop Motion': 11, 'Open Switch': 24}
  switch_channels = [7, 8, 24] # light switch and reed switch
  flip_channels = [24] # switch is flipped around (1 means closed, 0 means open)
  pir_channels = [25, 11] # pir receives and outputs 5v
  gpio_path = "/sys/class/gpio"
  secret_path = "/home/haldor/.open-sesame"
  ds18b20_path = "/sys/devices/w1_bus_master1/28-0000050585f4/w1_slave"
  host = "www.maglaboratory.org"
  use_ssl = True
  checkup_interval = 300
  
  # TODO: Check all the GPIOs
  # 7 -> Front Door
  # 8 - Main Door
  # 25 - Office Motion
  # 11 - Shop Motion
  # 24 - Switch? "plus30Mins" in old app...
  
  def get_secret(self):
    if len(self.secret) <= 0:
      file = open(Haldor.secret_path, 'rb')
      self.secret = file.read()
      file.close
    
    return self.secret
  
  def export_channels(self):
    GPIO.setmode(GPIO.BCM)
  
  def listen_channels(self):
    for chan in Haldor.switch_channels:
      GPIO.add_event_detect(chan, GPIO.BOTH, callback=self.event_checkup, bouncetime=500)
      
    for chan in Haldor.pir_channels:
      GPIO.add_event_detect(chan, GPIO.RISING, callback=self.event_checkup, bouncetime=800)
  
  def direct_channels(self):
    # pull up for switches
    # we'll need to flip it later
    for chan in Haldor.switch_channels:
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # For pir sensor, it'll be connected to the 5V (with a voltage divider)
    for chan in Haldor.pir_channels:
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
  
  def enable_gpio(self):
    self.export_channels()
    self.direct_channels()
  
  def notify_hash(self, body):
    hasher = hmac.new(self.get_secret(), body, hashlib.sha256)
    hasher.update(self.session)
    return hasher.hexdigest()
  
  def notify(self, path, params):
    # TODO: Check https certificate
    params['time'] = time.time()
    body = urllib.parse.urlencode(params).encode('utf-8')
    
    headers = {"Content-Type": "application/x-www-form-urlencoded",
      "Accept": "text/plain",
      "X-Haldor": Haldor.version,
      "X-Session": self.session,
      "X-Checksum": self.notify_hash(body)
      }
    conn = None
    if Haldor.use_ssl:
      conn = http.client.HTTPSConnection(Haldor.host)
    else:
      conn = http.client.HTTPConnection(Haldor.host)
    conn.request("POST", "/haldor/{0}".format(path), body, headers)
    print("Notified {0}".format(path))
    return conn.getresponse()
  
  def notify_bootup(self):
    uptime = ""
    uname = ""
    if_eth0 = ""
    therm = ""
  
    print("Bootup:")
    
    try:
      therm = subprocess.check_output(["cat", Haldor.ds18b20_path])
    except:
      print("\tw1 read error")
    
    try:
      uptime = subprocess.check_output("uptime")
      uname = subprocess.check_output(["uname", "-a"])
    except:
      print("\tuptime/uname read error")
    
    try:
      if_eth0 = subprocess.check_output(["/sbin/ifconfig", "eth0"])
    except:
      print("\teth0 read error")
      
  
    try:
      resp = self.notify('bootup', {'uptime': uptime, 'uname': uname, 'ifconfig_eth0': if_eth0, 'thermal': therm})
      self.session = resp.read()
      print("Bootup Complete: {0}".format(self.session))
    except:
      # TODO: Error handling
      print("Bootup ERROR")
      pass
  
  
  def bootup(self):
    print("Bootup.")
    self.secret = ""
    self.session = "".encode('utf-8')
    self.enable_gpio()
    self.notify_bootup()
  
  def read_gpio(self, chan):
    value = '-1'
    try:
      if chan in Haldor.flip_channels:
        # For door switch, active high (1) means we're in the OFF position
        # Flip it so a 1 means we're ON
        if GPIO.input(chan) == 0:
          value = 1
        else:
          value = 0
      else:
        value = GPIO.input(chan)
    except:
      value = '-2'

    return value
  
  def check_gpios(self, gpios):
    for name, chan in iter(Haldor.io_names.items()):
      gpios[name] = self.read_gpio(chan)
    
    return gpios
    
  def check_temp(self):
    value = ""
    try:
      temp = subprocess.check_output(["cat", Haldor.ds18b20_path])
      match = re.search('t=(\d+)', temp.decode('utf-8'))
      value = match.group(1)
    except:
      value = "--"
    
    return value
  
  def notify_checkup(self, checks):
    resp = self.notify('checkup', checks)
    print(resp.read())
  
  def checkup(self):
    print("Checkup.")
    checks = {}
    self.check_gpios(checks)
    checks['Temperature'] = self.check_temp()
    print(checks)
    self.notify_checkup(checks)
  
  def event_checkup(self, channel):
    print("Event caught for {0}".format(channel))
    self.checkup()
  
  def run(self):
    self.bootup()
    self.listen_channels();
    
    while True:
      self.checkup()
      time.sleep(Haldor.checkup_interval)
      # Threaded event detection will execute whenever it detects a change.
      # This main loop will sleep and send data every 5 minutes regardless of how often stuff changes
      
      
      
      
      
