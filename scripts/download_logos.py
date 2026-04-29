import urllib.request
import os

logos = {
    'shell': 'https://cdn.worldvectorlogo.com/logos/shell-4.svg',
    'petro-canada': 'https://upload.wikimedia.org/wikipedia/commons/thumb/5/5f/Petro-Canada_logo.svg/512px-Petro-Canada_logo.svg.png',
    'couche-tard': 'https://upload.wikimedia.org/wikipedia/commons/thumb/3/35/Alimentation_Couche-Tard_logo.svg/512px-Alimentation_Couche-Tard_logo.svg.png',
    'canadian-tire': 'https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/Canadian_Tire_logo.svg/512px-Canadian_Tire_logo.svg.png',
    'esso': 'https://cdn.worldvectorlogo.com/logos/esso-1.svg',
    'superga': 'https://cdn.worldvectorlogo.com/logos/superga-1.svg',
    'irving': 'https://cdn.worldvectorlogo.com/logos/irving-energy.svg',
    'harnois': 'https://cdn.worldvectorlogo.com/logos/harnois-1.svg',
    'axco': 'https://cdn.worldvectorlogo.com/logos/axco-1.svg',
    'costco': 'https://cdn.worldvectorlogo.com/logos/costco-wholesale-1.svg',
    'walmart': 'https://cdn.worldvectorlogo.com/logos/walmart-1.svg',
    'canadian-natural': 'https://cdn.worldvectorlogo.com/logos/canadian-natural-1.svg',
}

os.makedirs('logos', exist_ok=True)

for name, url in logos.items():
    try:
        filepath = f'logos/{name}.svg'
        print(f'Downloading {name}...')
        urllib.request.urlretrieve(url, filepath)
        print(f'Saved to {filepath}')
    except Exception as e:
        print(f'Failed to download {name}: {e}')