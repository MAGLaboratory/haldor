#!/usr/bin/env python



# TODO: Check all the GPIOs
# 7 -> Front Door
# 8 - Main Door
# 25 - Office Motion
# 11 - Shop Motion
# 24 - Switch? "plus30Mins" in old app...

io_channels = [7, 8, 25, 11, 24]

GPIO.setup(io_channels, GPIO.IN) #, pull_up_down=GPIO.PUD_UP/DOWN





def my_callback(channel):
    if var == 1:
        sleep(1.5)  # confirm the movement by waiting 1.5 sec 
        if GPIO.input(7): # and check again the input
            print("Movement!")
            captureImage()

            # stop detection for 20 sec
            GPIO.remove_event_detect(7)
            sleep(60)
            GPIO.add_event_detect(7, GPIO.RISING, callback=my_callback, bouncetime=300)

GPIO.add_event_detect(7, GPIO.RISING, callback=my_callback, bouncetime=300)




def cleanup_handler(signum, frame):
  GPIO.cleanup()
  
signal.signal(signal.SIGTERM, cleanup_handler)
