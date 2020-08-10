#!/bin/bash

/sbin/ifconfig eth0 | grep inet | awk '{ print $2 }' | cut -d: -f2 | tr -d '\n'
