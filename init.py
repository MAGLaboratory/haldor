#!/usr/bin/env python
 
import sys, os
from h_datacollector import HDCDaemon
from gpio_listener import GpioListener

if __name__ != "__main__":
  print("This must be executed directly.")
  sys.exit(3)

config = open("hdc_config.json", "r")
daemon = HDCDaemon.from_json(config.read())
config.close()

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
  elif 'listenall' == sys.argv[1]:
    la_daemon = GpioListener('/tmp/listenall.pid')
    la_daemon.run()
  elif 'nodaemon' == sys.argv[1]:
    daemon.run()
  else:
    print("Unknown command")
    sys.exit(2)
  sys.exit(0)
else:
  print("usage: %s start|stop|restart" % sys.argv[0])
  sys.exit(2)
