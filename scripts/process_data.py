import gzip
import json
import os

def process_stations():
    # Read GeoJSON
    import gzip
    try:
        with gzip.open('stations.geojson.gz', 'rb') as f:
            data = json.loads(f.read())
    except:
        with open('stations.geojson', 'r', encoding='utf-8') as f:
            data = json.loads(f.read())
    
    print(f"Total features: {len(data['features'])}")
    
    # Extract relevant fields for each station
    processed_features = []
    
    for feat in data['features']:
        props = feat['properties']
        prices = {p['GasType']: p for p in props.get('Prices', [])}
        
        # Parse price strings to numbers
        regular_price = None
        super_price = None
        diesel_price = None
        
        if 'Régulier' in prices and prices['Régulier']['Price']:
            try:
                regular_price = float(prices['Régulier']['Price'].rstrip('â\x82\xac').rstrip('¢').strip())
            except:
                pass
        
        if 'Super' in prices and prices['Super']['Price']:
            try:
                super_price = float(prices['Super']['Price'].rstrip('â\x82\xac').rstrip('¢').strip())
            except:
                pass
        
        if 'Diesel' in prices and prices['Diesel']['Price']:
            try:
                diesel_price = float(prices['Diesel']['Price'].rstrip('â\x82\xac').rstrip('¢').strip())
            except:
                pass
        
        processed_features.append({
            'type': 'Feature',
            'geometry': feat['geometry'],
            'properties': {
                'name': props.get('Name'),
                'brand': props.get('brand'),
                'status': props.get('Status'),
                'address': props.get('Address'),
                'postal_code': props.get('PostalCode'),
                'region': props.get('Region'),
                'regular_price': regular_price,
                'super_price': super_price,
                'diesel_price': diesel_price
            }
        })
    
    # Create output structure
    output = {
        'type': 'FeatureCollection',
        'features': processed_features,
        'metadata': {
            'generated_at': data['metadata']['generated_at'],
            'total_stations': data['metadata']['total_stations']
        }
    }
    
    # Write processed data
    with open('data/stations.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=None)
    
    # Calculate and save statistics
    prices_regular = [f['properties']['regular_price'] for f in processed_features 
                      if f['properties']['regular_price'] is not None]
    
    stats = {
        'min_price': min(prices_regular) if prices_regular else None,
        'max_price': max(prices_regular) if prices_regular else None,
        'avg_price': sum(prices_regular) / len(prices_regular) if prices_regular else None,
        'total_stations': len(processed_features),
        'regions': list(set(f['properties']['region'] for f in processed_features))
    }
    
    with open('data/stats.json', 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2)
    
    print(f"Processed {len(processed_features)} stations")
    print(f"Regular prices range: {stats['min_price']} - {stats['max_price']}")
    print(f"Average: {stats['avg_price']:.2f}")

if __name__ == '__main__':
    process_stations()
