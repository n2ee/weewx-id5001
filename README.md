weewx-id5001

Copyright 2017 Mark A. Matthews

Distributed under terms of the GPLv3

This is a weewx driver that connects to the venerable Heathkit ID5001 weather
station via it's serial interface option.

Description:

This driver communicates with the ID5001 weather station via a convient serial
port. The serial port is configured as 9600 bps, 8 data bits, 1 stop bit, no
parity. No flow control is used. This matches the defualt serial port
configuration on the ID5001.

Installation:

(wee_extension installation to be supplied)

For now, copy the file id5001.py to weewx/bin/user/id5001.py.

Add this section to your weewx config file:

[ID5001]

    # The serial port where the station is attached.
    port = /dev/path-to-your-serial-port

    model = ID5001

    # Interval between pollings of the station.
    loop_interval = 1.0

    # The driver to use:
    driver = user.id5001

In the [Station] section of the config file, do

    station_type = ID5001


