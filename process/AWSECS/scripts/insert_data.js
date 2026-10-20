const fs = require('fs');
const readline = require('readline');
const Redis = require('ioredis');
const ngeohash = require('ngeohash');

//const FORECAST_INTERVAL = process.env.FORECAST_INTERVAL;
const FORECAST_INTERVAL = process.argv[3] 
const REDIS_HOST = process.env.REDIS_HOST;
const shortNames = ['2t','2d','10u','10v','tp']
const redis = new Redis({
  host: REDIS_HOST,
  port: 6379,
  /*tls: {
    rejectUnauthorized: false, // Disable SSL verification
  },*/
});

const fileStream = fs.createReadStream(process.argv[2]);

// Use readline to process the file line by line
const rl = readline.createInterface({
  input: fileStream,
  crlfDelay: Infinity, // Handle all types of line breaks
});

let pipeline = redis.pipeline(); // Start Redis pipelining
let counter = 0; // Track number of commands in the pipeline

rl.on('line', (line) => {
  const [latStr, lonStr, valStr, dateStr, shortName] = line.trim().split(/\s+/); // Split line into components
  const lat = parseFloat(latStr);
  const lon = parseFloat(lonStr);
  const val = parseFloat(valStr);

  // Apply filtering logic
  if (lat <= 70.0 && lat >= -70.0 && shortNames.includes(shortName)) {
    const geohash = ngeohash.encode(lat, lon); // Encode latitude and longitude into geohash
    pipeline.hset(`${FORECAST_INTERVAL}:${geohash}`, shortName, val, "dateCode", dateStr);
    counter++;

    // Flush pipeline every 1,000 commands
    if (counter >= 1000) {
      pipeline.exec()
        .then(() => {
          //console.log('Flushed pipeline with 1,000 commands.');
        })
        .catch((error) => {
          console.error('Error executing pipeline:', error);
        });
      pipeline = redis.pipeline(); // Reset pipeline
      counter = 0; // Reset counter
    }
  }
});

rl.on('close', () => {
  // Execute any remaining commands in the pipeline
  if (counter > 0) {
    pipeline.exec()
      .then(() => {
        //console.log('Final flush of remaining commands.');
        redis.quit(); // Close the Redis connection
      })
      .catch((error) => {
        console.error('Error executing final pipeline:', error);
        redis.quit(); // Close the Redis connection on error
      });
  } else {
    redis.quit(); // Close Redis connection if no remaining commands
  }
  console.log("Completed insert for "+FORECAST_INTERVAL);
});

rl.on('error', (error) => {
  console.error('Error reading file:', error);
});

