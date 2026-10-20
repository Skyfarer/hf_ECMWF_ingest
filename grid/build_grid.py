#!/usr/bin/env python3
"""
Build the forecast grid file from ECMWF GRIB2 files.

Reads every GRIB2 file given (one model run, any number of forecast steps)
and writes a single flat binary file that the weather API memory-maps.

File layout:
    bytes 0-7    magic b"HFGRID1\\n"
    bytes 8-11   header length N (uint32, little endian)
    bytes 12-    N bytes of UTF-8 JSON header
    padding      zero bytes up to the next multiple of 4096
    data         float32 little endian, C order [step][param][row][col]

Rows run north to south starting at lat0, columns run west to east starting
at lon0. The header carries the model run time, steps, params and geometry.

Usage:
    build_grid.py --out /data/forecast.grid /data/0h.grib2 /data/6h.grib2 ...
    build_grid.py --out /data/forecast.grid /data/grib_dir
"""
import argparse
import datetime
import glob
import json
import os
import struct
import sys

import eccodes
import numpy as np

MAGIC = b"HFGRID1\n"
DATA_ALIGN = 4096
PARAMS = ['2t', '2d', '10u', '10v', 'tp']


def expand_inputs(inputs):
    """Expand directories into the GRIB2 files they contain."""
    files = []
    for path in inputs:
        if os.path.isdir(path):
            files.extend(sorted(glob.glob(os.path.join(path, '*.grib2'))))
        else:
            files.append(path)
    return files


def read_geometry(handle):
    """Return the grid geometry of a GRIB message as a comparable tuple."""
    grid_type = eccodes.codes_get(handle, 'gridType')
    if grid_type != 'regular_ll':
        raise ValueError(f"Unsupported gridType: {grid_type}")
    if eccodes.codes_get(handle, 'iScansNegatively') or eccodes.codes_get(handle, 'jScansPositively'):
        raise ValueError("Unsupported scanning mode (expected west-to-east, north-to-south)")
    return (
        eccodes.codes_get(handle, 'Ni'),
        eccodes.codes_get(handle, 'Nj'),
        eccodes.codes_get(handle, 'latitudeOfFirstGridPointInDegrees'),
        eccodes.codes_get(handle, 'longitudeOfFirstGridPointInDegrees'),
        eccodes.codes_get(handle, 'iDirectionIncrementInDegrees'),
        eccodes.codes_get(handle, 'jDirectionIncrementInDegrees'),
    )


def read_run(handle):
    """Return the model run time of a GRIB message as a UTC datetime."""
    date = eccodes.codes_get(handle, 'dataDate')
    time = eccodes.codes_get(handle, 'dataTime')
    return datetime.datetime(
        date // 10000, date // 100 % 100, date % 100,
        time // 100, time % 100,
        tzinfo=datetime.timezone.utc,
    )


def read_gribs(files, max_lat):
    """
    Read the wanted params from all files.

    Returns (run, geometry, row_start, row_count, fields) where fields maps
    step -> {param: 2D float32 array clipped to +/- max_lat}.
    """
    run = None
    geometry = None
    row_start = row_count = None
    fields = {}

    for path in files:
        with open(path, 'rb') as f:
            while True:
                handle = eccodes.codes_grib_new_from_file(f)
                if handle is None:
                    break
                try:
                    short_name = eccodes.codes_get(handle, 'shortName')
                    if short_name not in PARAMS:
                        continue

                    msg_run = read_run(handle)
                    if run is None:
                        run = msg_run
                    elif msg_run != run:
                        raise ValueError(f"{path}: model run {msg_run:%Y-%m-%d %HZ} does not match {run:%Y-%m-%d %HZ}")

                    msg_geometry = read_geometry(handle)
                    if geometry is None:
                        geometry = msg_geometry
                        ni, nj, lat_first, lon_first, di, dj = geometry
                        # Rows are north to south, keep those within +/- max_lat
                        row_start = max(0, int(np.ceil((lat_first - max_lat) / dj - 1e-9)))
                        row_end = min(nj - 1, int(np.floor((lat_first + max_lat) / dj + 1e-9)))
                        row_count = row_end - row_start + 1
                        if row_count <= 0:
                            raise ValueError(f"No rows within +/- {max_lat} degrees latitude")
                    elif msg_geometry != geometry:
                        raise ValueError(f"{path}: grid geometry of {short_name} differs from the first message")

                    step = int(eccodes.codes_get(handle, 'step'))
                    values = eccodes.codes_get_values(handle).reshape(geometry[1], geometry[0])
                    clipped = values[row_start:row_start + row_count].astype('<f4')
                    fields.setdefault(step, {})[short_name] = clipped
                finally:
                    eccodes.codes_release(handle)
        print(f"Read {path}")

    if not fields:
        raise ValueError("No matching GRIB messages found")

    for step in sorted(fields):
        missing = [p for p in PARAMS if p not in fields[step]]
        if missing:
            raise ValueError(f"Step {step}h is missing params: {', '.join(missing)}")

    return run, geometry, row_start, row_count, fields


def write_grid(out_path, run, geometry, row_start, row_count, fields):
    """Write the grid file to a temp file and atomically move it into place."""
    ni, nj, lat_first, lon_first, di, dj = geometry
    steps = sorted(fields)

    # ECMWF starts at 180E, which is the same meridian as 180W
    lon0 = (lon_first + 180.0) % 360.0 - 180.0

    header = {
        'run': run.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'steps': steps,
        'params': PARAMS,
        'lat0': lat_first - row_start * dj,
        'lon0': lon0,
        'dlat': -dj,
        'dlon': di,
        'nlat': row_count,
        'nlon': ni,
        'dtype': '<f4',
    }
    header_bytes = json.dumps(header).encode('utf-8')
    prefix_len = len(MAGIC) + 4 + len(header_bytes)
    padding = -prefix_len % DATA_ALIGN

    tmp_path = f"{out_path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, 'wb') as f:
            f.write(MAGIC)
            f.write(struct.pack('<I', len(header_bytes)))
            f.write(header_bytes)
            f.write(b'\0' * padding)
            for step in steps:
                for param in PARAMS:
                    f.write(fields[step][param].tobytes())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)
        # Readers see either the old file or the new one, never a partial write
        os.replace(tmp_path, out_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return header


def main():
    parser = argparse.ArgumentParser(description="Build the forecast grid file from ECMWF GRIB2 files.")
    parser.add_argument('inputs', nargs='+', help="GRIB2 files, or directories containing *.grib2 files")
    parser.add_argument('--out', required=True, help="Path of the grid file to write")
    parser.add_argument('--max-lat', type=float, default=70.0, help="Keep rows within +/- this latitude (default: 70)")
    args = parser.parse_args()

    files = expand_inputs(args.inputs)
    if not files:
        print("No GRIB2 files found", file=sys.stderr)
        return 1

    try:
        run, geometry, row_start, row_count, fields = read_gribs(files, args.max_lat)
        header = write_grid(args.out, run, geometry, row_start, row_count, fields)
    except (ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    size_mb = os.path.getsize(args.out) / 1e6
    print(f"Wrote {args.out} ({size_mb:.1f} MB)")
    print(f"Run {header['run']}, steps {header['steps']}, {header['nlat']}x{header['nlon']} points")
    return 0


if __name__ == '__main__':
    sys.exit(main())
