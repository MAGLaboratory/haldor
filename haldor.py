
import time, signal, subprocess, http.client, urllib, hmac, hashlib, re
#from functools import partial
import RPi.GPIO as GPIO
from daemon import Daemon
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List, Dict

@dataclass_json
@dataclass
class Haldor(Daemon):
  """Watches the door and monitors various switches and motion via GPIO"""

  # dataclass variable declaration
  version = '2020'
  name: str
  description: str
  io_channels: List[int]
  io_names: Dict[str, int]
  switch_channels: List[int]
  flip_channels: List[int]
  pir_channels: List[int]
  gpio_path: str
  secret_path: str
  ds18b20_path: str
  host: str
  use_ssl: bool
  checkup_interval: int
  
  # TODO: Check all the GPIOs
  # 7 -> Front Door
  # 8 - Main Door
  # 25 - Office Motion
  # 11 - Shop Motion
  # 24 - Switch? "plus30Mins" in old app...
  
  def get_secret(self):
    if len(self.secret) <= 0:
      file = open(self.secret_path, 'rb')
      self.secret = file.read()
      file.close
    
    return self.secret
  
  def export_channels(self):
    GPIO.setmode(GPIO.BCM)
  
  def listen_channels(self):
    for chan in self.switch_channels:
      GPIO.add_event_detect(chan, GPIO.BOTH, callback=self.event_checkup, bouncetime=500)
      
    for chan in self.pir_channels:
      GPIO.add_event_detect(chan, GPIO.RISING, callback=self.event_checkup, bouncetime=800)
  
  def direct_channels(self):
    # pull up for switches
    # we'll need to flip it later
    for chan in self.switch_channels:
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # For pir sensor, it'll be connected to the 5V (with a voltage divider)
    for chan in self.pir_channels:
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)
  
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
      "X-Haldor": self.version,
      "X-Session": self.session,
      "X-Checksum": self.notify_hash(body)
      }
    conn = None
    if self.use_ssl:
      conn = http.client.HTTPSConnection(self.host)
    else:
      conn = http.client.HTTPConnection(self.host)
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
      therm = subprocess.check_output(["cat", self.ds18b20_path])
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
      my_ip = subprocess.check_output(["/usr/bin/curl", "-s", "http://whatismyip.akamai.com/"])
    except:
      print("\tmy ip read error")
    
    try:
      local_ip = subprocess.check_output(["/home/brandon/haldor/local_ip.sh"])
    except:
      print("\tlocal ip read error")

    boot_checks = {}
    boot_checks['uptime'] = uptime
    boot_checks['uname'] = uname
    boot_checks['ifconfig_eth0'] = if_eth0
    boot_checks['thermal'] = therm
    boot_checks['my_ip'] = my_ip
    boot_checks['local_ip'] = local_ip
  
    try:
      resp = self.notify('bootup', boot_checks)
      self.session = resp.read()
      print("Bootup Complete: {0}".format(self.session))
    except:
      # TODO: Error handling
      print("Bootup ERROR".format(self.session))
      pass
  
  
  def bootup(self):
    print("Bootup sequence called.")
    # invert dictionary for reporting
    self.name_ios = {v: k for k, v in self.io_names.items()}
    self.secret = ""
    self.session = "".encode('utf-8')
    self.enable_gpio()
    self.notify_bootup()
    self.pings = 0
  
  def read_gpio(self, chan):
    value = '-1'
    try:
      if chan in self.flip_channels:
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
    for name, chan in iter(self.io_names.items()):
      gpios[name] = self.read_gpio(chan)
    
    return gpios
    
  def check_temp(self):
    value = ""
    try:
      temp = subprocess.check_output(["cat", self.ds18b20_path])
      match = re.search('t=(\d+)', temp.decode('utf-8'))
      value = match.group(1)
    except:
      value = "--"
    
    return value
  
  def checkup(self):
    print("Checkup.")
    checks = {}
    
    self.pings+=1
    if(self.pings % 100 == 0):
      self.pings = 0
      try:
        my_ip = subprocess.check_output(["/usr/bin/curl", "-s", "http://whatismyip.akamai.com/"])
        checks['my_ip'] = my_ip
      except:
        print("\tmy ip read error")
      
      try:
        local_ip = subprocess.check_output(["/home/brandon/haldor/local_ip.sh"])
        checks['local_ip'] = local_ip
      except:
        print("\tlocal ip read error")
    
    self.check_gpios(checks)
    checks['Temperature'] = self.check_temp()
    print(checks)
    resp = self.notify('checkup', checks)
    print(resp.read())
  
  def event_checkup(self, channel):
    checks = {}
    print("Event caught for {0}".format(self.name_ios[channel]))
    checks[self.name_ios[channel]] = self.read_gpio(channel)
    resp = self.notify('checkup', checks)
    print(resp.read())
  
  def run(self):
    self.bootup()
    self.listen_channels();
    
    while True:
      self.checkup()
      time.sleep(self.checkup_interval)
      # Threaded event detection will execute whenever it detects a change.
      # This main loop will sleep and send data every 5 minutes regardless of how often stuff changes
      
      
      
      
      
