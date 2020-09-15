#!/usr/bin/env python
#
# 17/02/2019
# Juan M. Casillas <juanm.casillas@gmail.com>
# https://github.com/juanmcasillas/gopro2gpx.git
#
# Released under GNU GENERAL PUBLIC LICENSE v3. (Use at your own risk)
#


import argparse
import array
import os
import platform
import re
import struct
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime

from .config import setup_environment
from . import fourCC
from . import gpmf
from . import gpshelper


def BuildGPSPoints(data, skip=False):
    """
    Data comes UNSCALED so we have to do: Data / Scale.
    Do a finite state machine to process the labels.
    GET
     - SCAL     Scale value
     - GPSF     GPS Fix
     - GPSU     GPS Time
     - GPS5     GPS Data
    """

    last_lat = None
    last_lon = None

    points = []
    SCAL = fourCC.XYZData(1.0, 1.0, 1.0)
    GPSU = None
    SYST = fourCC.SYSTData(0, 0)

    start_time = None

    stats = {
        'ok': 0,
        'badfix': 0,
        'badfixskip': 0,
        'empty' : 0
    }

    GPSFIX = None
    GPSFIX_next = 0
    for d in data:

        if d.fourCC == 'SCAL':
            SCAL = d.data
        elif d.fourCC == 'GPSU':
            GPSU = d.data
            if start_time is None:
                print('XXX got start_time value:', d.data)
                start_time = datetime.fromtimestamp(time.mktime(d.data))
        elif d.fourCC == 'GPSF':
            if d.data != GPSFIX:
                print("GPSFIX change to %s [%s]" % (d.data,fourCC.LabelGPSF.xlate[d.data]))
                print("d.data: ", repr(d.data))
            if GPSFIX is not None:
                GPSFIX = GPSFIX_next
            else:
                # At startup, just pass the value through
                GPSFIX = d.data
            # we delay using GPSFIX till next set
            GPSFIX_next = d.data
            # if GPSFIX_next == 0:
            #     # if we just lost fix, don't delay on passing it through
            #     GPSFIX = GPSFIX_next
        elif d.fourCC == 'GPS5':
            # we have to use the REPEAT value.

            for item in d.data:

                if item.lon == item.lat == item.alt == 0:
                    print("Warning: Skipping empty point")
                    stats['empty'] += 1
                    continue

                if GPSFIX == 0:
                    stats['badfix'] += 1
                    if skip:
                        print("Warning: Skipping point due GPSFIX==0")
                        stats['badfixskip'] += 1
                        continue

                retdata = [ float(x) / float(y) for x,y in zip( item._asdict().values() ,list(SCAL) ) ]


                gpsdata = fourCC.GPSData._make(retdata)
                # print('SMT-000:lat = ' + repr(gpsdata.lat) + ' lon = ' + repr(gpsdata.lon))
                if last_lat is not None and abs(abs(last_lat) - abs(gpsdata.lat)) > 1.0:
                    print('BIG LAT SKIP: last_lat = %f  gpsdata.lat = %f' % (last_lat, gpsdata.lat))
                    print('skipping')
                    continue
                if last_lon is not None and abs(abs(last_lon) - abs(gpsdata.lon)) > 1.0:
                    print('BIG LON SKIP: last_lon = %f  gpsdata.lon = %f' % (last_lon, gpsdata.lon))
                    print('skipping')
                    continue

                p = gpshelper.GPSPoint(gpsdata.lat, gpsdata.lon, gpsdata.alt, datetime.fromtimestamp(time.mktime(GPSU)), gpsdata.speed)
                points.append(p)
                stats['ok'] += 1
                last_lat = gpsdata.lat
                last_lon = gpsdata.lon

        elif d.fourCC == 'SYST':
            data = [ float(x) / float(y) for x,y in zip( d.data._asdict().values() ,list(SCAL) ) ]
            if data[0] != 0 and data[1] != 0:
                SYST = fourCC.SYSTData._make(data)


        elif d.fourCC == 'GPRI':
            # KARMA GPRI info

            if d.data.lon == d.data.lat == d.data.alt == 0:
                print("Warning: Skipping empty point")
                stats['empty'] += 1
                continue

            if GPSFIX == 0:
                stats['badfix'] += 1
                if skip:
                    print("Warning: Skipping point due GPSFIX==0")
                    stats['badfixskip'] += 1
                    continue

            data = [ float(x) / float(y) for x,y in zip( d.data._asdict().values() ,list(SCAL) ) ]
            gpsdata = fourCC.KARMAGPSData._make(data)

            if SYST.seconds != 0 and SYST.miliseconds != 0:
                p = gpshelper.GPSPoint(gpsdata.lat, gpsdata.lon, gpsdata.alt, datetime.fromtimestamp(SYST.miliseconds), gpsdata.speed)
                points.append(p)
                stats['ok'] += 1




    print("-- stats -----------------")
    total_points =0
    for i in stats.keys():
        total_points += stats[i]
    print("- Ok:              %5d" % stats['ok'])
    print("- GPSFIX=0 (bad):  %5d (skipped: %d)" % (stats['badfix'], stats['badfixskip']))
    print("- Empty (No data): %5d" % stats['empty'])
    print("Total points:      %5d" % total_points)
    print("--------------------------")
    return(points, start_time)

def parseArgs():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count")
    parser.add_argument("-b", "--binary", help="read data from bin file", action="store_true")
    parser.add_argument("-s", "--skip", help="Skip bad points (GPSFIX=0)", action="store_true", default=False)
    parser.add_argument("files", help="Video file or binary metadata dump", nargs='*')
    parser.add_argument("outputfile", help="output file. builds KML and GPX")
    args = parser.parse_args()

    return args

def main():
    args = parseArgs()
    config = setup_environment(args)
    points = []
    start_time = None
    for file in config.files:
        config.file = file
        parser = gpmf.Parser(config)

        if not args.binary:
            data = parser.readFromMP4()
        else:
            data = parser.readFromBinary()

        # build some funky tracks from camera GPS

        file_points, file_start_time = BuildGPSPoints(data, skip=args.skip)
        if start_time is None:
            start_time = file_start_time
        points += file_points

        if len(points) == 0:
            print("Can't create file. No GPS info in %s. Exitting" % args.files)
            sys.exit(0)

    kml = gpshelper.generate_KML(points)
    with open("%s.kml" % args.outputfile , "w+") as fd:
        fd.write(kml)

    print('SMT-200: start time:', repr(start_time))
    gpx = gpshelper.generate_GPX(points, start_time, trk_name="gopro7-track")
    with open("%s" % args.outputfile , "w+") as fd:
        fd.write(gpx)

if __name__ == "__main__":
    main()
