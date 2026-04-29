# Brand logos for Quebec gas stations
# Each file is a simple SVG with the brand name

brands = [
    ('AMI', '#0066CC'),
    ('Aucun', '#888888'),
    ('Axco', '#FF6600'),
    ('Beausoir', '#1E90FF'),
    ('Belzile', '#FF4500'),
    ('Bélisle', '#228B22'),
    ('Canadian Tire', '#FF6600'),
    ('Costco', '#0065AD'),
    ('Couche-Tard', '#0055A4'),
    ('Crevier', '#4169E1'),
    ('Eko', '#32CD32'),
    ('Esso', '#E31C1C'),
    ('Francis', '#FF8C00'),
    ('Gaz-O-Bar', '#FF6347'),
    ('Harnois', '#FF6600'),
    ('Intergaz', '#FFD700'),
    ('Irving', '#E31837'),
    ('Le Relais', '#228B22'),
    ('Little Tree', '#2E8B57'),
    ('MacEwen', '#006400'),
    ('Miraco', '#4169E1'),
    ('Mobil', '#0066CC'),
    ('Nutrinor Énergies', '#FF8C00'),
    ('Paddock', '#8B4513'),
    ('Paquet', '#FF6347'),
    ('Pepco', '#FF4500'),
    ('Petro-Canada', '#D00'),
    ('Petroplus', '#4169E1'),
    ('Pétro-Québec', '#0066CC'),
    ('Pétro-T', '#FF6600'),
    ('Pétroles Maurice', '#FF8C00'),
    ('Quickie', '#FF6347'),
    ('Sergaz', '#FF4500'),
    ('Shell', '#ED1118'),
    ('Sonic', '#FF4500'),
    ('Stinson', '#4169E1'),
    ('Super Gaz', '#FF6347'),
    ('Ultramar', '#1C75BC'),
]

import os

for brand, color in brands:
    filename = f'logos/{brand.lower().replace(" ", "-").replace(".", "").replace("é", "e").replace("è", "e")}.svg'
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 30">
  <rect width="100" height="30" fill="#f5f5f5" rx="4"/>
  <text x="50" y="20" font-family="Arial, sans-serif" font-weight="bold" font-size="14" fill="{color}" text-anchor="middle">{brand}</text>
</svg>'''
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(svg)

print(f'Created {len(brands)} brand logos')