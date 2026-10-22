#!/bin/bash
# Fetch today's ECMWF 00Z run (0h-72h, every 6h) and build /mnt/hf/data/forecast.grid.
#
# ECMWF's publish time for the 00Z run varies a lot in practice -- sometimes a run
# isn't fully published until well into the afternoon UTC. So this script is meant
# to be invoked on a short interval (see hf-ingest.timer) and simply no-ops until
# today's run is fully available. It never silently re-ingests an older run once we
# already have real data, so a late ECMWF publish shows up as "still waiting", not
# as a forecast.grid that looks fresh but is actually a day old.
#
# Each run downloads into its own directory, so a leftover partial run can never
# cause a mixed-run error, and the grid file is only replaced once the new one is
# complete.
set -euo pipefail

DATA=${HF_DATA:-/mnt/hf/data}
OUT=$DATA/forecast.grid
IMAGE=${HF_GRID_IMAGE:-hf_grid:latest}
BASE=https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com
STEPS=$(seq 0 6 72)
TODAY=$(date -u +%Y%m%d)

# Print the "run" field from the existing grid file's header, if any.
current_run() {
    [ -s "$OUT" ] || return 1
    python3 - "$OUT" <<'PY'
import struct, sys
with open(sys.argv[1], 'rb') as f:
    f.read(8)
    n = struct.unpack('<I', f.read(4))[0]
    print(f.read(n).decode())
PY
}

want_run="${TODAY:0:4}-${TODAY:4:2}-${TODAY:6:2}T00:00:00Z"
if current_run 2>/dev/null | grep -q "\"run\": \"${want_run}\""; then
    echo "Already have today's run ($want_run), nothing to do"
    exit 0
fi

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

if fetch_run "$TODAY"; then
    echo "Fetched run $TODAY"
    docker run --rm --user "$(id -u):$(id -g)" -v "$DATA:/data" "$IMAGE" \
        --out /data/forecast.grid "/data/incoming/$TODAY"
    rm -rf "$DATA/incoming"
    exit 0
fi

rm -rf "$DATA/incoming/$TODAY"
echo "Run $TODAY not fully published yet, will retry on the next timer tick" >&2

# Bootstrap only: if we have no grid file at all yet, fall back once so the API
# always has something to serve instead of nothing. Once a grid file exists this
# branch never runs again -- we wait for today's real run instead of recycling it.
if [ ! -s "$OUT" ]; then
    YDAY=$(date -u -d yesterday +%Y%m%d)
    if fetch_run "$YDAY"; then
        echo "Bootstrapping with previous run $YDAY"
        docker run --rm --user "$(id -u):$(id -g)" -v "$DATA:/data" "$IMAGE" \
            --out /data/forecast.grid "/data/incoming/$YDAY"
        rm -rf "$DATA/incoming"
    fi
fi

exit 0
