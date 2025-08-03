import os
import shutil
import datetime
import numpy as np
from eumdac import DataStore, AccessToken
from pyresample import create_area_def
from satpy import Scene
from dateutil.relativedelta import relativedelta
import cv2
from pyproj import Transformer
import gc
from skyfield.api import load, wgs84
from shapely.wkt import loads
from shapely.geometry import Polygon
import argparse
import warnings
warnings.filterwarnings('ignore')
    
# ========== INPUT PARAMETERS ==========
print("===========================================")
print("================ EUMETSAT ================")
print("===========================================")

print(r""" 
                                                      
                                                      
       *+.                                            
      -=-+*=                                          
      =::::-=+=                                       
     ----:::.--+*=                                    
    ==::.::::::.--=*-              +**+-              
    -**=--..::::: .-+##-          --:::==-            
        **=-::.:..--::=#%       @+.:.                 
           +*=-.  -*++-+   *@*@@@       .@@*          
              ++#@%*=*@@ @@@@+%%@   .@-    .          
                 @@@@@@%+@@-:*#@@@      : .-:         
                    =+:*-%:-#% .@@@   :. ..:          
                    -@@==+*@ -@-.@@@%                 
                     @@@@@@%@+@@%*- :+***#%+          
                          =      :..------=#%@+       
                                =##*=-:------+%@@-    
                                   -###+---===-*#     
                                      :#%%#---+@=     
                                          %@@%*@      
                                            .@@@      
                                                                                                                                                                                                                                                                                                          
""")

parser = argparse.ArgumentParser(description="Download and process EUMETSAT satellite data.")

parser.add_argument('--start_date', type=str, help="Start date in format YYYY-MM-DDTHH:MM:SS")
parser.add_argument('--output_path', type = str, help="Folder where to save the images")
parser.add_argument('--end_date', type=str, help="End date in format YYYY-MM-DDTHH:MM:SS")
parser.add_argument('--skip_night_angle', type=float, help="Skip low sun angle scenes (when the sun elevation is below this angle, data retrieval will be skipped)")
parser.add_argument('--country', type=str, help="Country (spain, france, balearic_islands, etc...)")
parser.add_argument('--width', type=int, help="Output image width in pixels")
parser.add_argument('--channel', type = str, help = 'Spectral band')
parser.add_argument('--lat_min', type=float, help="Minimum latitude for custom region")
parser.add_argument('--lat_max', type=float, help="Maximum latitude for custom region")
parser.add_argument('--lon_min', type=float, help="Minimum longitude for custom region")
parser.add_argument('--lon_max', type=float, help="Maximum longitude for custom region")
parser.add_argument('--consumer_key', type = str, help = 'Your Consumer Key of your EumetSat account')
parser.add_argument('--consumer_secret', type = str, help = 'Your Consumer Secret of your EumetSat account')

args = parser.parse_args()


try:
    dtstart = datetime.datetime.strptime(args.start_date, "%Y-%m-%dT%H:%M:%S")
    dtend = datetime.datetime.strptime(args.end_date, "%Y-%m-%dT%H:%M:%S")
except:
    dtstart = datetime.datetime.now(datetime.timezone.utc) - relativedelta(minutes=15)
    dtend = datetime.datetime.now(datetime.timezone.utc)

channel = args.channel if args.channel is not None else 'vis_06'

# ========== FLAGS ==========
skip_night_angle = args.skip_night_angle
country = args.country if args.country else 'spain'  # default country if not specified

output_path = args.output_path if args.output_path is not None else os.path.join(os.getcwd(), 'imgs')
os.makedirs(output_path, exist_ok = True)
    
# ========== GET SOLAR ANGLE ==========

def get_sun_elevation(dt_utc, lat=39.6, lon=2.9):
    ts = load.timescale()
    t = ts.utc(dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour, dt_utc.minute)
    eph = load('de421.bsp')
    sun = eph['sun']
    earth = eph['earth']
    location = earth + wgs84.latlon(latitude_degrees=lat, longitude_degrees=lon)
    astrometric = location.at(t).observe(sun)
    alt, az, _ = astrometric.apparent().altaz()
    return alt.degrees

# ========== AUTHENTIFICATION ==========

if args.consumer_key is not None:
    cons_key = args.consumer_key
else:
    raise Exception("Missing required argument: --consumer_key")

if args.consumer_secret is not None:
    cons_secret = args.consumer_secret
else:
    raise Exception("Missing required argument: --consumer_secret")

credentials = (cons_key, cons_secret)
token = AccessToken(credentials)
datastore = DataStore(token)
selected_collection = datastore.get_collection('EO:EUM:DAT:0665')

resolution_map = {'vis_06':500, 'nir_22':500, 'ir_38':1000, 'ir_105':1000}
# ========== GENERATE AREA ==========

def compute_pixel_dimensions(area_extent, meters_per_pixel=500):
    lon_min, lat_min, lon_max, lat_max = area_extent

    # Use Mercator projection for distance in meters
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    x_min, y_min = transformer.transform(lon_min, lat_min)
    x_max, y_max = transformer.transform(lon_max, lat_max)

    width_m = abs(x_max - x_min)
    height_m = abs(y_max - y_min)

    width_px = int(width_m / meters_per_pixel)
    height_px = int(height_m / meters_per_pixel)

    return width_px, height_px
# === CREATE AREA ===
def create_area(name, area_extent, channel):
    xpix, ypix = compute_pixel_dimensions(area_extent, resolution_map[channel])
    return create_area_def(
        name,
        {'proj': 'latlong', 'datum': 'WGS84'},
        width=xpix,
        height=ypix,
        area_extent=area_extent
    )
# === PREDEFINED AREAS ===

area_defs = {
    'balearic_islands': create_area('balearic_islands', [1.0, 38.5, 4.5, 40.1], channel),
    'iberia': create_area('iberia', [-10.0, 35.0, 4.5, 44.5], channel),
    'france': create_area('france', [-5.5, 41.0, 9.5, 51.5], channel),
    'uk_ireland': create_area('uk_ireland', [-11.0, 49.5, 3.5, 60.0], channel),
    'germany_benelux': create_area('germany_benelux', [2.5, 47.0, 14.5, 55.0], channel),
    'scandinavia': create_area('scandinavia', [5.0, 55.0, 25.0, 71.5], channel),
    'italy': create_area('italy', [6.0, 36.0, 19.0, 47.0], channel),
    'greece':create_area('greece', [19.0, 34.5, 29.5, 42.5], channel),
    'balkans':create_area('balkans', [13.0, 36.0, 30.0, 47.5], channel)
}

countries_dict = {'iberia':[area_defs['iberia'], ['0033','0034','0035', '0036']],
                  'balearic_islands':[area_defs['balearic_islands'], ['0034', '0035']],
                  'france':[area_defs['france'], ['0035', '0036','0037']],
                  'uk_ireland':[area_defs['uk_ireland'], ['0037', '0038','0039']],
                  'germany_benelux':[area_defs['germany_benelux'], ['0036', '0037','0038']],
                  'scandinavia':[area_defs['scandinavia'], ['0038', '0039','0040']],
                  'italy':[area_defs['italy'], ['0033', '0034','0035', '0036']],
                  'greece':[area_defs['greece'], ['0033','0034','0035']],
                  'balkans':[area_defs['balkans'], ['0033', '0034', '0035','0036']]}

wkt_file_path = "FCI_chunks.wkt"  

if not os.path.exists(wkt_file_path):
    raise FileNotFoundError(f"File {wkt_file_path} not found. Make sure it is in the repository.")

with open(wkt_file_path, "r") as file:
    wkt_data = file.readlines()

chunk_polygons = {}
for line in wkt_data:
    chunk_id, wkt_poly = line.strip().split(',', 1) 
    chunk_polygons[chunk_id] = loads(wkt_poly)  

use_custom_roi = all([
    args.lat_min is not None,
    args.lat_max is not None,
    args.lon_min is not None,
    args.lon_max is not None
])

if use_custom_roi:
    # Construct user-defined area
    manual_extent = [args.lon_min, args.lat_min, args.lon_max, args.lat_max]
    area_def_custom = create_area('custom_area', manual_extent, channel)

    # Build ROI polygon
    roi_polygon = Polygon([
        (args.lon_min, args.lat_min),
        (args.lon_min, args.lat_max),
        (args.lon_max, args.lat_max),
        (args.lon_max, args.lat_min)
    ])

    # Find intersecting chunks
    relevant_chunks = []
    for chunk_id, chunk_poly in chunk_polygons.items():
        if roi_polygon.intersects(chunk_poly):
            relevant_chunks.append(chunk_id)

    print(f"Custom bounding box intersects chunks: {relevant_chunks}")
    
    if not relevant_chunks:
        raise ValueError("No chunks intersect with the custom bounding box.")

    # Replace `area_def` and `chunk_ids`
    area_def = area_def_custom
    chunk_ids = relevant_chunks
else:
    if country not in countries_dict:
        raise ValueError(f"Invalid country: {country}. Choose from: {list(countries_dict.keys())}")

    area_def, chunk_ids = countries_dict[country]

                  

# ========== DDOWNLOAD PRODUCTS ==========

products = selected_collection.search(dtstart=dtstart, dtend=dtend)
print(f"Found {len(products)} matching timestep(s).")

if country not in countries_dict:
    raise ValueError(f"Invalid country: {country}. Choose from: {list(countries_dict.keys())}")

area_def, chunk_ids = countries_dict[country]
chunk_patterns = [f"_{cid}.nc" for cid in chunk_ids]

for product in products:
    downloaded_files = []
    for entry in product.entries:
        if any(pattern in entry for pattern in chunk_patterns):
            local_filename = os.path.basename(entry)

            # === FETCH UTC DATETIME FROM THE FILE ===
            try:
                ts_str = local_filename.split('_C_EUMT_')[1][:14] 
                ts_dt = datetime.datetime.strptime(ts_str, "%Y%m%d%H%M%S")
            except Exception as e:
                print(f"Failed to parse timestamp from filename: {local_filename}")
                continue

            # === SKIP IF THE SUN ANGLE IS BELOW A CERTAIN THRESHOLD ===
            if skip_night_angle is not None:
                sun_elev = get_sun_elevation(ts_dt)
                print(f"Sun elevation at {ts_dt} UTC: {sun_elev:.2f}°")
                if sun_elev < skip_night_angle: # 25º as threshold
                    print("Skipping due to low sun angle.")
                    continue

            print(f"Downloading: {local_filename}")
            local_filepath = os.path.join(output_path, local_filename)
            # === DOWNLOAD ===
            with product.open(entry=entry) as fsrc:
                with open(local_filepath, 'wb') as fdst:
                    shutil.copyfileobj(fsrc, fdst)

            downloaded_files.append(local_filepath)
    print(f"Saved: {[os.path.basename(file) for file in downloaded_files]}")
    try:
        scn = Scene(filenames=downloaded_files, reader='fci_l1c_nc')
        # print(f'Available composite ids: {scn.available_composite_ids()}')
        # print(f'Available spectral ids: {scn.available_dataset_ids()}')

        scn.load([channel])  
        scn_resampled = scn.resample(area_def)
        red = scn_resampled[channel].values
        if channel == 'vis_06':
            red_scaled = (red*4).astype(np.float32)
            red_scaled_uint8 = np.clip(red_scaled, 0, 255).astype(np.uint8)
        else:
            red_scaled =(red).astype(np.float32)
            red_scaled_uint8 = red_scaled.astype(np.uint8)

        if country is not None:
            img_name = f"{channel}_{country}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
        else:
            img_name = f"{channel}_LON{args.lon_min}S{args.lon_max}_LAT{args.lat_min}S{args.lat_max}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
        img_height, img_width = red_scaled_uint8.shape
        if args.width is not None:
            new_width = args.width
            aspect_ratio = img_height / img_width
            new_height = int(new_width * aspect_ratio)
            red_scaled_uint8_resized = cv2.resize(red_scaled_uint8, (new_width, new_height), interpolation=cv2.INTER_AREA)
        else:
            red_scaled_uint8_resized = red_scaled_uint8  # No resizing
        cv2.imwrite(os.path.join(output_path, img_name), red_scaled_uint8_resized)
        print(f"Saved image: {img_name}")

        del scn
        del scn_resampled
        del red
        del red_scaled_uint8
        gc.collect() 

    except Exception as e:
        print(f"Error processing scene: {e}")

    finally:
        # Solo intenta eliminar si el archivo existe
        for local_filepath in downloaded_files:
            if os.path.exists(local_filepath):
                try:
                    os.remove(local_filepath)
                except Exception as e:
                    print(f"Error deleting file {local_filename}: {e}")

    print("===========================================")




