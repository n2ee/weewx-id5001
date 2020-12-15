#
#    Copyright (c) 2009-2015 Tom Keffer <tkeffer@gmail.com>
#
#    Copyright 2017 Mark A. Matthews <markm@xmission.com>
#
#    See the file LICENSE.txt for your full rights.
#
# Derived from simulator.py and my previous id5001 driver written for
# wview (though never integrated into the repository).
#
"""Driver for Heathkit ID-5001 Weather Computer.

This driver sets up the ID-5001 to respond to polls from weewx when generating
the loop packet. The ID-5001 serial protocol very much resembles the
Hayes AT modem commands. The serial port is set to 9600 bps, 8 data bits,
1 stop bit, no parity. No flow control is used.

"""


import serial
import syslog
import time

import weewx.drivers
from weewx.units import INHG_PER_MBAR, kph_to_mph, CtoF, CM_PER_INCH
from weeutil.weeutil import timestamp_to_string

MILE_PER_KNOT = 1.15078

DRIVER_NAME = 'ID5001'
DRIVER_VERSION = "0.1"

def loader(config_dict, _):
    return ID5001Driver(**config_dict[DRIVER_NAME])

def confeditor_loader():
    return ID5001ConfEditor()


def logmsg(level, msg):
    syslog.syslog(level, 'id-5001: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)


def _fmt(x):
    return ' '.join(["%0.2X" % ord(c) for c in x])


class ID5001Driver(weewx.drivers.AbstractDevice):
    """weewx driver that communicates with Heathkit ID-5001 Weather Station
    
    port - serial port
    [Required. Default is /dev/ttyUSB0]

    max_tries - how often to retry serial communication before giving up
    [Optional. Default is 5] - note that this retry is for the entire
    data-gathering communication, not individual commands to the station.
    """

    def __init__(self, **stn_dict):
        self.model = stn_dict.get('model', 'ID5001')
        self.port = stn_dict.get('port', Station.DEFAULT_PORT)
        self.max_tries = int(stn_dict.get('max_tries', 5))
        self.retry_wait = int(stn_dict.get('retry_wait', 5))
        self.loop_interval = float(stn_dict.get('loop_interval', 5.0))
        debug_serial = int(stn_dict.get('debug_serial', 0))
        self.last_rain = None

        loginf('driver version is %s' % DRIVER_VERSION)
        loginf('using serial port %s' % self.port)
        self.station = Station(self.port, self.loop_interval, debug_serial=debug_serial)
        self.station.open()

    def closePort(self):
        if self.station is not None:
            self.station.close()
            self.station = None

    @property
    def hardware_name(self):
        return self.model

    def getTime(self):
        return self.station.get_time()

    def setTime(self):
        self.station.set_time(int(time.time()))

    def genLoopPackets(self):

        the_time = time.time()

        while True:
            # Wait for loop_interval to pass before getting the
            # next reading.
            sleep_time = the_time + self.loop_interval - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)

            readings = self.station.get_readings(self.max_tries,
                                                 self.retry_wait)

            # Note that we generate the timestamp *after* the readings are
            # taken, this accomodates the possibility that some number of
            # retries occurred before a successful collection of loop
            # data happened
            the_time = time.time()
            packet = {'dateTime': int(time.time()), 'usUnits': weewx.US}

            packet.update(readings)
            yield packet


class Station(object):

    DEFAULT_PORT = '/dev/ttyUSB0'
    last_rain = None
    serial_port = None
    timeout = 1 # Seconds
    bitrate = 9600
    bytesize = serial.EIGHTBITS
    parity = serial.PARITY_NONE
    stopbits = serial.STOPBITS_ONE

    def __init__(self, port, loop_interval, debug_serial=0):
        self._debug_serial = debug_serial
        self.port = port
        self.loop_interval = loop_interval

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, _, value, traceback):
        self.close()

    def open(self):
        logdbg("open serial port %s" % self.port)
        self.serial_port = serial.Serial(self.port, self.bitrate,
                                         self.bytesize, self.parity,
                                         self.stopbits, xonxoff=0,
                                         rtscts=0,
                                         timeout=self.timeout)
        # Once the port is open, send some initialization
        # commands to put the station in a known state:
        # Echo Clear
        self.send_AT_cmd("EC")

        # Linefeed Set
        self.send_AT_cmd("LS")

        # auto Xmit Clear
        self.send_AT_cmd("XCA")

        # Reset peak wind gust for next poll cycle
        self.send_AT_cmd("CWGH")

        # Initialize the rainfall accumulator for the loop delta
        buf = self.send_AT_cmd("RR")
        self.last_rain = Station._decodeRain(buf)
        if self.last_rain is None:
            self.last_rain = 0

    def close(self):
        if self.serial_port is not None:
            logdbg("close serial port %s" % self.port)
            self.serial_port.close()
            self.serial_port = None

    def _readline(self):
        eol = b'\r'
        leneol = len(eol)
        line = bytearray()

        while True:
            c = self.serial_port.read(1)
            if c:
                line += c
                if line[-leneol:] == eol:
                    break
            else:
                break

        return line.decode()

    def send_AT_cmd(self, cmd):
        self.serial_port.reset_input_buffer()
        self.serial_port.write(str.encode("AT"))
        self.serial_port.write(str.encode(cmd))
        self.serial_port.write(str.encode("\r"))
        self.serial_port.flush()
        # print "Sent AT%s\\r" % cmd

        response = self._readline()

        # print("Recvd %s\n" % response)

        return response.strip()

    def set_time(self, ts):
        local_time = time.localtime(ts)
        set_time_cmd = "ST%2.2d%2.2d%2.2d" % (local_time.tm_hour,
                                              local_time.tm_min,
                                              local_time.tm_sec)
        logdbg("set station time to %s (%s)" % (timestamp_to_string(ts),
                                                set_time_cmd))
        self.send_AT_cmd(set_time_cmd)

        set_date_cmd = "SD%2.2d%2.2d%2.2d" % (local_time.tm_year % 100,
                                              local_time.tm_mon,
                                              local_time.tm_mday)
        logdbg("set station date to %s (%s)" % (timestamp_to_string(ts),
                                                set_date_cmd))
        self.send_AT_cmd(set_date_cmd)

    def get_time(self):
        try:
            sta_time = int(self.send_AT_cmd("RT"))
            sta_date = int(self.send_AT_cmd("RD"))
            # break sta_time into hh:mm:ss
            hh = sta_time // 10000
            mm = sta_time // 100 - (hh * 100)
            ss = sta_time - (mm * 100) - (hh * 10000)

            # break sta_date into YY:MM:DD
            YY = sta_date // 10000
            MM = sta_date // 100 - (YY * 100)
            DD = sta_date - (MM * 100) - (YY * 10000)

            # two-digit year hack - this station defaults to a epoch of 1987
            # when power is lost. If the year is greater than 86, we assume
            # it's the 20th century, otherwise it's the 21st.
            # Note - someone (not me!) will have to revisit this code in 2086.
            if YY > 86:
                year = 1900 + YY
            else:
                year = 2000 + YY

            # We keep the station clock in GMT, which eliminates the
            # DST silliness.
            ts = time.mktime((year, MM, DD, hh, mm, ss, 0, 0, -1))
            logdbg("station date: %s, time: %s, (%s)" %
                   (sta_date, sta_time, timestamp_to_string(ts)))
            return ts

        except (serial.serialutil.SerialException, weewx.WeeWxIOError) as e:
            logerr("get_time failed: %s" % e)
        return int(time.time())

    def get_readings(self, max_tries=3, retry_wait=3):
        data = dict()
        for ntries in range(0, max_tries):
            try:
                buf = self.send_AT_cmd("RTI")
                data['inTemp'] = Station._decodeTemperature(buf)

                buf = self.send_AT_cmd("RTO")
                data['outTemp'] = Station._decodeTemperature(buf)

                buf = self.send_AT_cmd("RHI")
                data['inHumidity'] = Station._decodeHumidity(buf)

                buf = self.send_AT_cmd("RHO")
                data['outHumidity'] = Station._decodeHumidity(buf)

                buf = self.send_AT_cmd("RWA")
                data['windSpeed'] = Station._decodeWindSpeed(buf)
                data['windDir'] = Station._decodeWindDirection(buf)

                buf = self.send_AT_cmd("RWGH")
                data['windGust'] = Station._decodeWindSpeed(buf)
                data['windGustDir'] = Station._decodeWindDirection(buf)

                buf = self.send_AT_cmd("CWGH")
                # clears Wind Gust High for next cycle

                buf = self.send_AT_cmd("RB")
                data['barometer'] = Station._decodeBarometer(buf)

                buf = self.send_AT_cmd("RR")
                this_rain = Station._decodeRain(buf)
                if this_rain != None:
                    rain_delta = this_rain - self.last_rain
                    if rain_delta < 0.0:
                        # Whoops - station must have reset
                        # skip the rain on this loop
                        rain_delta = 0.0

                    self.last_rain = this_rain
                    data['rain'] = rain_delta

                buf = self.send_AT_cmd("RRR")
                data['rain_rate'] = Station._decodeRain(buf)

                buf = self.send_AT_cmd("RWCA")
                buf = buf[1:]
                data['windhcill'] = Station._decodeTemperature(buf)

                return data

            except (serial.serialutil.SerialException,
                    IndexError, weewx.WeeWxIOError) as e:
                loginf("Failed attempt %d of %d to get readings: %s, buf = %s" %
                       (ntries + 1, max_tries, e, buf))
                time.sleep(retry_wait)

        msg = "Max retries (%d) exceeded for readings" % max_tries
        logerr(msg)
        raise weewx.RetriesExceeded(msg)


    @staticmethod
    def _decodeTemperature(s):
        # tnnn[C] Indoor Temperature
        # Tnnn[C] Outdoor Temperature
        # cTnnn[C] Wind Chill
        if s[0] == 'c':
            s = s[1:]

        isCelsius = (s[-1] == 'C')

        try:
            temp = int(s[1:4])
            if isCelsius:
                temp = CtoF(temp)
        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            temp = None

        return temp

    @staticmethod
    def _decodeHumidity(s):
        # hnn Indoor Humidity
        # Hnn Outdoor Humidity
        try:
            humidity = int(s[1:3])
        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            humidity = None

        return humidity


    @staticmethod
    def _decodeWindSpeed(s):
        # wnnn[K|L|M]nnnD Wind Average
        # <Wnnn[K|L|M] nnnD Wind Gust High
        # A '<' or '>' symbol at the begining of the buffer indicates
        # that this is a high or low reading. The rest of the message
        # decodes the same at the average or gust messages.
        if (s[0] == '<') or (s[0] == '>'):
            s = s[1:]

        try:
            isKnots = (s[4] == 'K')
            isMPH = (s[4] == 'M')
            isKPH = (s[4] == 'L')
        
            windSpeed = int(s[1:4])
            if isKnots:
                windSpeed *= MILE_PER_KNOT
            elif isKPH:
                windSpeed = kph_to_mph(windSpeed)
            elif isMPH:
                pass

        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            windSpeed = None

        return windSpeed


    @staticmethod
    def _decodeWindDirection(s):
        # wnnn[K|L|M]nnnD Wind Average
        # <Wnnn[K|L|M] nnnD Wind Gust High
        # A '<' or '>' symbol at the begining of the buffer indicates
        # that this is a high or low reading. The rest of the message
        # decodes the same at the average or gust messages.
        if (s[0] == '<') or (s[0] == '>'):
            s = s[1:]

        try:
            windDirection = int(s[5:8])
        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            windDirection = None

        return windDirection


    @staticmethod
    def _decodeBarometer(s):
        # Bnnnn[M] Barometer
        try:
            isMillibars = (s[-1] == 'M')

            baro = int(s[1:5])

            if isMillibars:
                baro *= INHG_PER_MBAR
            else:
                baro /= 100.0

        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            baro = None

        if (baro == 0.0):
            # Unless someone launched the sensor into space, a
            # value of zero indicates a glitched reading. Discard it.
            baro = None

        return baro

    @staticmethod
    def _decodeRain(s):
        # Rnnnnn[nC] Rainfail
        # RRnnnnn[nC] Rainfail Rate
        try:
            if (s[1] == 'R'):
                # This is a rain rate message, we discard the first char
                # and then parse it like a rain total message.
                s = s[1:]

            if (s[-1] == 'C'):
                # measurement in centimeters
                rain = int(s[1:7]) / 100.0 / CM_PER_INCH
            else:
                rain = int(s[1:6]) / 100.0

        except Exception:
            logerr("conversion of buffer failed: %s" % s)
            rain = None

        return rain


class ID5001ConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[ID5001]
    # This section is for the Heathkit ID-5001 weather station

    # Serial port where the station is attached
    port = %s

    model = ID5001

    # Interval between pollings of the station.
    loop_interval = 5.0

    # The driver to use:
    driver = weewx.drivers.id5001
""" % Station.DEFAULT_PORT

    def prompt_for_settings(self):
        print("Specify the serial port on which the station is connected, for")
        print("example: /dev/ttyUSB0 or /dev/ttyS0 or /dev/cua0.")
        port = self._prompt('port', Station.DEFAULT_PORT)
        return {'port': port}


if __name__ == "__main__":
    import optparse

    usage = """%prog [options] [--help]"""

    syslog.openlog('id5001', syslog.LOG_PID | syslog.LOG_CONS)
    syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', dest='version', action='store_true',
                      help='display driver version')
    parser.add_option('--debug', dest='debug', action='store_true',
                      help='provide additional debug output in log')
    parser.add_option('--port', dest='port', metavar='PORT',
                      help='serial port to which the station is connected',
                      default=Station.DEFAULT_PORT)
    parser.add_option('--loop_interval', dest='loop_interval',
                      metavar='LOOP_INTERVAL',
                      help='interval in seconds between polling station',
                      default=5.0)
    (options, args) = parser.parse_args()

    if options.version:
        print(("id5001 driver version %s" % DRIVER_VERSION))
        exit(0)

    driver_dict = {
        'port' : options.port,
        'debug' : options.debug,
        'loop_interval' : options.loop_interval}

    stn = ID5001Driver(**driver_dict)

    for packet in stn.genLoopPackets():
        print(packet)
