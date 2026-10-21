# hf_ECMWF_ingest

I'm working on a project that requires global temperature, dewpoint and wind forecasts. As a private pilot,
the ECMWF has proven to be very reliable for long range planning. It's not for
making "go/no go" decisions before I launch in my plane. But, when looking
ahead to decide if I want to make plan a trip Monday-Thursday, or Tuesday-Friday, the ECMWF has been great for getting a heads up on the long range, wide area weather situation.

For this project, I want temperature, dewpoint, and wind forecasts every 6 hours for the next 3 days.
The ECMWF GRIB2 files have this data and publish it multiple
times a day. The files come out as one file per forecast interval every 3 hours, for example: 0h, 3h, 6h
and so forth. For this use case, I want the 0h, 6h, 12h and so forth for
a 3 day span.

## Current pipeline (self-hosted)

This originally ran on AWS (EventBridge -> Lambda -> Fargate/ECS -> ValKey). That path is gone;
everything now runs as a single systemd timer job on my own hardware:

1. `selfhost/hf-ingest.timer` fires `selfhost/hf-ingest.sh` once a day, shortly after the 00Z
   model run becomes available.
2. `hf-ingest.sh` downloads the 0h-72h (every 6h) GRIB2 files for that run directly from ECMWF's
   public S3 bucket (`ecmwf-forecasts`, no AWS credentials needed), falling back to the previous
   day's run if today's isn't published yet.
3. Once a run downloads completely, it runs the `hf_grid` Docker image (built from `grid/Dockerfile`,
   entrypoint `grid/build_grid.py`) against the downloaded files. `build_grid.py` uses ECMWF's
   ECCODES library to pull out temperature, dewpoint, wind (u/v) and precipitation, clips to
   +/-70 degrees latitude, and writes a single flat binary grid file (`forecast.grid`) with a small
   JSON header describing the run time, steps, params and geometry.
4. The grid file is written atomically (temp file + `os.replace`), so readers never see a partial
   write. `hf_weather_api` memory-maps this file directly to answer forecast lookups — no database,
   no cache, no network hop between ingest and serving.

This is simpler and cheaper than the old AWS pipeline: one scheduled job, one Docker image, one flat
file on disk instead of Lambda + ECS + ValKey.
