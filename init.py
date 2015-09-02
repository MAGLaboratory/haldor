#!/usr/bin/env python
 
import sys, os
from haldor import Haldor

if __name__ != "__main__":
  print("This must be executed directly.")
  sys.exit(3)

if None != os.getenv('HALDOR_HOST'):
  Haldor.host = os.getenv('HALDOR_HOST')

if None != os.getenv('HALDOR_GPIO_PATH'):
  Haldor.gpio_path = os.getenv('HALDOR_GPIO_PATH')

if None != os.getenv('HALDOR_SECRET_PATH'):
  Haldor.secret_path = os.getenv('HALDOR_SECRET_PATH')

if None != os.getenv('HALDOR_NOSSL'):
  Haldor.use_ssl = False
  
if None != os.getenv('HALDOR_DS18B20'):
  Haldor.ds18b20_path = os.getenv('HALDOR_DS18B20')


daemon = Haldor('/tmp/haldor.pid')
if len(sys.argv) == 2:
  if 'start' == sys.argv[1]:
    print("Starting...")
    daemon.start()
  elif 'stop' == sys.argv[1]:
    print("Stopping...")
    daemon.stop()
  elif 'restart' == sys.argv[1]:
    print("Restarting...")
    daemon.restart()
  elif 'testrun' == sys.argv[1]:
    daemon.run()
  else:
    print("Unknown command")
    sys.exit(2)
  sys.exit(0)
else:
  print("usage: %s start|stop|restart" % sys.argv[0])
  sys.exit(2)
