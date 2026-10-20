#!/bin/bash
# Fetch the latest ECMWF 00Z run (0h-72h, every 6h) and build /mnt/hf/data/forecast.grid.
# Each run downloads into its own directory, so a leftover older run can never cause
# a mixed-run error, and the grid file is only replaced once the new one is complete.
set -euo pipefail

DATA=${HF_DATA:-/mnt/hf/data}
IMAGE=${HF_GRID_IMAGE:-hf_grid:latest}
BASE=https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com
STEPS=$(seq 0 6 72)

fetch_run() {
    local day=$1 dir=$DATA/incoming/$1 h
    mkdir -p "$dir"
    for h in $STEPS; do
        [ -s "$dir/${day}000000-${h}h-oper-fc.grib2" ] && continue
        curl -sf --retry 3 --retry-delay 10 -o "$dir/${day}000000-${h}h-oper-fc.grib2.part" \
            "$BASE/$day/00z/ifs/0p25/oper/${day}000000-${h}h-oper-fc.grib2" || return 1
        mv "$dir/${day}000000-${h}h-oper-fc.grib2.part" "$dir/${day}000000-${h}h-oper-fc.grib2"
    done
}

# Today's 00Z run is published several hours after 00Z; fall back to yesterday's.
for day in $(date -u +%Y%m%d) $(date -u -d yesterday +%Y%m%d); do
    if fetch_run "$day"; then
        echo "Fetched run $day"
        docker run --rm --user "$(id -u):$(id -g)" -v "$DATA:/data" "$IMAGE" \
            --out /data/forecast.grid "/data/incoming/$day"
        rm -rf "$DATA/incoming"
        exit 0
    fi
    echo "Run $day not available yet" >&2
done
echo "No complete run found" >&2
exit 1
