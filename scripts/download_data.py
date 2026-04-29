import urllib.request
import time
import random
import sys

url = 'https://regieessencequebec.ca/stations.geojson.gz'
filename = 'stations.geojson.gz'
max_retries = 3

for i in range(max_retries):
    try:
        print(f"Attempt {i+1}...")
        urllib.request.urlretrieve(url, filename)
        print("Download successful!")
        break
    except Exception as e:
        if i < max_retries - 1:
            wait = random.uniform(1.0, 1.5)
            print(f"Failed. Retrying in {wait:.2f} seconds...")
            time.sleep(wait)
        else:
            print("All 3 attempts failed.")
            print("Using existing data file if available")
            sys.exit(0)
