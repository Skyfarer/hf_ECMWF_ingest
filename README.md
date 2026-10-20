# forecast-ingest
I'm working on a project that requires global temperature, dewpoint and wind forecasts. As a private pilot,
the ECMWF has proven to be very reliable for long range planning. It's not for
making "go/no go" decisions before I launch in my plane. But, when looking
ahead to decide if I want to make plan a trip Monday-Thursday, or Tuesday-Friday, the ECMWF has been great for getting a heads up on the long range, wide area weather situation.

For this project, I want temperature, dewpoint, and wind forecasts every 6 hours for the next 3 days.
The ECMWF GRIB2 files have this data and publish it multiple
times a day. The files come out as one file per forecast interval every 3 hours, for example: 0h, 3h, 6h
and so forth. For this use case, I want the 0h, 6h, 12h and so forth for
a 3 day span.

It all begins with AWS Event Bridge scheduler. Every 24 hours, just after the 00Z
model output becomes available, AWS Event Bridge launches the "multi-fetch"
lambda. This lambda simply iterates through the forecast intervals I want and calls the"start-fetch" lambda, passing in the forcast intervals each time. This in turn launches an
ECS task. This ECS task downloads the desired ECMWF GRIB file from ECMWF's S3
bucket, and sends it to my own bucket for later processing. The script running
on this container launches another ECS task for processing the file once the
file transfer completes. Several of these all run in parallel greatly reducing
the processing time.

The processing container is a docker image that I built with ECMWF's own
processing tools, called ECCODES. These tools extract the data out of the grib
with latitude and longitude. I have nodeJS ingesting this text stream and sending it
to Redis. Well, in this case, I'm not actually using Redis, I'm using AWS ValKey, a managed cache that is Redis API
compatible. By using nodeJS for it's asyncronous nature and using Redis
pipelining, each instance can process a million data points in just a few minutes.
