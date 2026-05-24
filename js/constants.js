// Shared brand constants — single source of truth

export const brandColors = {
  'AMI': '#0066CC', 'Aucun': '#888888', 'Axco': '#FF6600', 'Beausoir': '#1E90FF',
  'Belzile': '#FF4500', 'Bélisle': '#228B22', 'Canadian Tire': '#FF6600', 'Costco': '#0065AD',
  'Couche-Tard': '#0055A4', 'Crevier': '#4169E1', 'Eko': '#32CD32', 'Esso': '#E31C1C',
  'Francis': '#FF8C00', 'Gaz-O-Bar': '#FF6347', 'Harnois': '#FF6600', 'Irving': '#E31837',
  'Le Relais': '#228B22', 'Little Tree': '#2E8B57', 'MacEwen': '#006400', 'Miraco': '#4169E1',
  'Mobil': '#0066CC', 'Nutrinor Énergies': '#FF8C00', 'Paddock': '#8B4513', 'Paquet': '#FF6347',
  'Pepco': '#FF4500', 'Petro-Canada': '#D00', 'Petroplus': '#4169E1', 'Pétro-Québec': '#0066CC',
  'Pétro-T': '#FF6600', 'Pétroles Maurice': '#FF8C00', 'Quickie': '#FF6347', 'Sergaz': '#FF4500',
  'Shell': '#ED1118', 'Sonic': '#FF4500', 'Stinson': '#4169E1', 'Super Gaz': '#FF6347', 'Ultramar': '#1C75BC',
};

export const brandAbbrs = {
  'AMI': 'AMI', 'Aucun': '?', 'Axco': 'AX', 'Beausoir': 'B', 'Belzile': 'B',
  'Bélisle': 'BL', 'Canadian Tire': 'CT', 'Costco': 'C', 'Couche-Tard': 'CT', 'Crevier': 'C',
  'Eko': 'E', 'Esso': 'E', 'Francis': 'F', 'Gaz-O-Bar': 'GB', 'Harnois': 'H', 'Irving': 'I',
  'Le Relais': 'LR', 'Little Tree': 'LT', 'MacEwen': 'M', 'Miraco': 'M', 'Mobil': 'M',
  'Nutrinor Énergies': 'N', 'Paddock': 'P', 'Paquet': 'P', 'Pepco': 'P', 'Petro-Canada': 'P',
  'Petroplus': 'P', 'Pétro-Québec': 'PQ', 'Pétro-T': 'PT', 'Pétroles Maurice': 'PM',
  'Quickie': 'Q', 'Sergaz': 'S', 'Shell': 'S', 'Sonic': 'S', 'Stinson': 'S', 'Super Gaz': 'SG', 'Ultramar': 'U',
};

export function brandColor(brand) {
  return brandColors[brand] || '#666';
}

export function brandAbbr(brand) {
  return brandAbbrs[brand] || brand?.substring(0, 2).toUpperCase() || '?';
}
