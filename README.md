# HALDOR (**HAL**: **D**o**OR**, **HAL**: **D**ata collect**OR**)
*Thor's Stone*

HALDOR collects data from *doors*, *PIR*, and one-wire *Temperature Sensors.*

## Description
HALDOR collects data from the sources listed above.  The collected data is reported via MQTT in two different ways: interrupt and checkup.  Interrupts are posted on `/event` and trigger automatically when any door or PIR changes state.  Checkups are requested via `reporter/checkup_req` and include all sensors when posted on `/checkup`.  On a configurable number of checkups, there is a long checkup which includes a configurable system information report.

## Getting Started
### Dependencies
#### Hardware
* SBC with compatible with the an IO library
* Glue and protection board
* DS18B20 one-wire thermistors
#### Software
* Python
 * multitimer
 * dataclasses_json
 * paho mqtt
 * OPi.GPIO

### Installing
* If needed follow the OPi.GPIO guide on non-root access of the gpios
* Clone from github.
* link start.sh in /etc/init.d/
* use your Linux-distribution-provided utilities to configure HALDOR to start at system startup
* maybe install a crontab to check if HALDOR is running.  It's kind of buggy at this point.

### Configuration
Example configurations are provided as `hdc_config.example*`.  Please use the examples to aid in your efforts in configuring HALDOR to your needs.

### Execution
If you installed HALDOR correctly, it should start by itself.

If you prefer to test things yourself (perhaps for debugging), you can run it in test mode using `./start.sh nodaemon` or `./start.sh testrun`.

## Help
Submit a bug report on the `MAGLaboratory/haldor` github or email somebody at maglaboratory.  Try the webdev at maglaboratory.org.

## Authors
The project originally started as somebody's senior design project.  The current author does not know their name.

@blu006
@kiafaldorius

## Version History
No version numbers used.  Here are some git hashes.

* ae1c0b: A working commit
* 79d905: MQTT and JSON implemented
 * d5b533: Bugfixes and cleanup
* c8667b: Temperature sensor power restart implemented

## License
Public domain

## Acknowledgements
Raspberry pi 
Dallas / Maxim Semiconductor
Authors of the supporting libraries -- this is Python after all.
MAG Laboratory
