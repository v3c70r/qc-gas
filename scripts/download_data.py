import urllib.request
import time
import random
import sys

url = 'https://regieessencequebec.ca/stations.geojson.gz'
filename = 'stations.geojson.gz'
max_retries = 3

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/geo+json, gzip;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive'
}

for i in range(max_retries):
    try:
        print(f"Attempt {i+1}...")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(filename, 'wb') as f:
                f.write(response.read())
        print("Download successful!")
        break
    except Exception as e:
        if i < max_retries - 1:
            wait = random.uniform(1.0, 1.5)
            print(f"Failed: {e}. Retrying in {wait:.2f} seconds...")
            time.sleep(wait)
        else:
            print("All 3 attempts failed.")
            print("Using existing data file if available")
            sys.exit(0)
