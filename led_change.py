#!/usr/bin/env python3
import logging
import json
import paho.mqtt.publish as publish
import paho.mqtt.client as mqtt
from maglab_crypto import MAGToken

class LED_RELAY(mqtt.Client):
    "Main class which toggles LEDs when PIR modules detect motion"
    def __init__(self, token):
        self.log = logging.getLogger(__name__)
        self.token = token

        mqtt.Client.__init__(self, mqtt.CallbackAPIVersion.VERSION2)

    def on_log(self, _, __, level, buff):
        """ Overloaded MQTT log function """
        if level == mqtt.MQTT_LOG_DEBUG:
            self.log.debug(f"PAHO: {buff}")
        elif level == mqtt.MQTT_LOG_INFO:
            self.log.info(f"PAHO: {buff}")
        elif level == mqtt.MQTT_LOG_NOTICE:
            self.log.info(f"PAHO: {buff}")
        else:
            self.log.error(f"PAHO: {buff}")

    def on_connect(self, _, __, ___, reason, ____):
        self.log.info(f"MQTT connected: {reason}")
        self.subscribe("secmon00/+")

    def on_message(self, _, __, msg):
        if msg.topic.startswith("secmon00/"):
            out_d = {}
            decoded = msg.payload.decode('utf-8')
            self.log.debug(f"Motion message received: {decoded}")
            try: 
                data = json.loads(decoded)
                for i in range(5):
                    if f"TestPIR{i}" in data:
                        out_d.update({f"LEDPIR{i}" : data[f"TestPIR{i}"]})
                if len(out_d) > 0:
                    cmd_msg = str(MAGToken.cmd_msg_gen(out_d, self.token))
                    self.publish("secmon00/cmd", cmd_msg)
            except json.JSONDecodeError as exc:
                self.log.info(str(exc))

    def main(self):
        self.connect("hal.maglab", 1883, 60)
        self.loop_forever()


if __name__ == "__main__":
    logging.basicConfig(level="DEBUG")
    token = "magls_NXQmv+RixRJnH3gbUq2Ttp/85Zd9qantr7DrZQV6DMWw"
    token = MAGToken.token_decode("magls_", token)

    relay = LED_RELAY(token)
    relay.main()
