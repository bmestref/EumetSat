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
import warnings
warnings.filterwarnings('ignore')

class EumetSatMTG:
    def __init__(self, consumer_key=None, consumer_secret=None):
        if not consumer_key or not consumer_secret:
            raise Exception("Consumer key and secret are required.")

        self.credentials = (consumer_key, consumer_secret)
        self.token = AccessToken(self.credentials)
        self.datastore = DataStore(self.token)
        self.selected_collection = self.datastore.get_collection('EO:EUM:DAT:0665')
        self.chunk_polygons = self._load_chunks("FCI_chunks.wkt")
        self.resolution = {'vis_06':500, 'nir_22':500, 'ir_38':1000, 'ir_105':1000}



    def _load_chunks(self, wkt_file_path):
        if not os.path.exists(wkt_file_path):
            raise FileNotFoundError(f"File {wkt_file_path} not found.")
        with open(wkt_file_path, "r") as file:
            wkt_data = file.readlines()

        chunk_polygons = {}
        for line in wkt_data:
            chunk_id, wkt_poly = line.strip().split(',', 1)
            chunk_polygons[chunk_id] = loads(wkt_poly)
        return chunk_polygons

    def _get_sun_elevation(self, dt_utc, lat=39.6, lon=2.9):
        ts = load.timescale()
        t = ts.utc(dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour, dt_utc.minute)
        eph = load('de421.bsp')
        sun = eph['sun']
        earth = eph['earth']
        location = earth + wgs84.latlon(latitude_degrees=lat, longitude_degrees=lon)
        astrometric = location.at(t).observe(sun)
        alt, _, _ = astrometric.apparent().altaz()
        return alt.degrees

    def _compute_pixel_dimensions(self, area_extent, meters_per_pixel=500):
        lon_min, lat_min, lon_max, lat_max = area_extent
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x_min, y_min = transformer.transform(lon_min, lat_min)
        x_max, y_max = transformer.transform(lon_max, lat_max)
        width_px = int(abs(x_max - x_min) / meters_per_pixel)
        height_px = int(abs(y_max - y_min) / meters_per_pixel)
        return width_px, height_px

    def _create_area(self, name, area_extent, channel):
        xpix, ypix = self._compute_pixel_dimensions(area_extent, meters_per_pixel = self.resolution[channel])
        return create_area_def(name, {'proj': 'latlong', 'datum': 'WGS84'}, width=xpix, height=ypix, area_extent=area_extent)

    def _define_area(self, country, lat_min, lat_max, lon_min, lon_max, channel):
        area_defs = {
            'mallorca': self._create_area('mallorca', [2.0, 39.1, 3.7, 40.2], channel),
            'iberia': self._create_area('iberia', [-10.0, 35.0, 4.5, 44.5], channel),
            'france': self._create_area('france', [-5.5, 41.0, 9.5, 51.5], channel),
            'uk_ireland': self._create_area('uk_ireland', [-11.0, 49.5, 3.5, 60.0], channel),
            'germany_benelux': self._create_area('germany_benelux', [2.5, 47.0, 14.5, 55.0], channel),
            'scandinavia': self._create_area('scandinavia', [5.0, 55.0, 25.0, 71.5], channel),
            'italy': self._create_area('italy', [6.0, 36.0, 19.0, 47.0], channel),
            'greece': self._create_area('greece', [19.0, 34.5, 29.5, 42.5], channel),
            'balkans': self._create_area('balkans', [13.0, 36.0, 30.0, 47.5], channel)
        }

        countries_dict = {
            'iberia': [area_defs['iberia'], ['0033', '0034', '0035', '0036']],
            'mallorca': [area_defs['mallorca'], ['0034', '0035']],
            'france': [area_defs['france'], ['0035', '0036', '0037']],
            'uk_ireland': [area_defs['uk_ireland'], ['0037', '0038', '0039']],
            'germany_benelux': [area_defs['germany_benelux'], ['0036', '0037', '0038']],
            'scandinavia': [area_defs['scandinavia'], ['0038', '0039', '0040']],
            'italy': [area_defs['italy'], ['0033', '0034', '0035', '0036']],
            'greece': [area_defs['greece'], ['0033', '0034', '0035']],
            'balkans': [area_defs['balkans'], ['0033', '0034', '0035', '0036']]
        }

        if all(v is not None for v in [lat_min, lat_max, lon_min, lon_max]):
            manual_extent = [lon_min, lat_min, lon_max, lat_max]
            area_def_custom = self._create_area('custom_area', manual_extent, channel)
            roi_polygon = Polygon([
                (lon_min, lat_min),
                (lon_min, lat_max),
                (lon_max, lat_max),
                (lon_max, lat_min)
            ])

            relevant_chunks = [cid for cid, poly in self.chunk_polygons.items() if roi_polygon.intersects(poly)]
            if not relevant_chunks:
                raise ValueError("No chunks intersect with the custom bounding box.")
            return area_def_custom, relevant_chunks

        if country not in countries_dict:
            raise ValueError(f"Invalid country: {country}. Choose from: {list(countries_dict.keys())}")

        return countries_dict[country]

    def get_available_ids(self):
        print(
        " ======================== IR 105 ========================  \n" \
        " DataID(name='ir_105', wavelength=WavelengthRange(min=9.8, central=10.5, max=11.2, unit='µm'), resolution=1000, calibration=<3>, modifiers=())\n" \
        " DataID(name='ir_105_earth_sun_distance', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_index_map', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_pixel_quality', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_platform_altitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_subsatellite_latitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_subsatellite_longitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_subsolar_latitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_subsolar_longitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_sun_satellite_distance', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_swath_direction', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_swath_number', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_105_time', resolution=1000, modifiers=())\n" \
        " ======================== IR 38 ========================  \n" \
        " DataID(name='ir_38', wavelength=WavelengthRange(min=3.4, central=3.8, max=4.2, unit='µm'), resolution=1000, calibration=<2>, modifiers=())\n" \
        " DataID(name='ir_38_earth_sun_distance', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_index_map', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_pixel_quality', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_platform_altitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_subsatellite_latitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_subsatellite_longitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_subsolar_latitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_subsolar_longitude', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_sun_satellite_distance', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_swath_direction', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_swath_number', resolution=1000, modifiers=())\n" \
        " DataID(name='ir_38_time', resolution=1000, modifiers=())\n" \
        " ======================== NIR 22 ========================  \n" \
        " DataID(name='nir_22', wavelength=WavelengthRange(min=2.2, central=2.25, max=2.3, unit='µm'), resolution=500, calibration=<1>, modifiers=())\n" \
        " DataID(name='nir_22_earth_sun_distance', resolution=500, modifiers=()), DataID(name='nir_22_index_map', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_pixel_quality', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_platform_altitude', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_subsatellite_latitude', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_subsatellite_longitude', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_subsolar_latitude', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_subsolar_longitude', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_sun_satellite_distance', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_swath_direction', resolution=500, modifiers=())\n" \
        " DataID(name='nir_22_swath_number', resolution=500,modifiers=())\n" \
        " DataID(name='nir_22_time', resolution=500, modifiers=())\n" \
        " ======================== VIS 06 ========================  \n" \
        " DataID(name='vis_06', wavelength=WavelengthRange(min=0.59, central=0.64, max=0.69, unit='µm'), resolution=500, calibration=<1>, modifiers=())\n" \
        " DataID(name='vis_06_earth_sun_distance', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_index_map', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_pixel_quality', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_platform_altitude', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_subsatellite_latitude', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_subsatellite_longitude', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_subsolar_latitude', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_subsolar_longitude', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_sun_satellite_distance', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_swath_direction', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_swath_number', resolution=500, modifiers=())\n" \
        " DataID(name='vis_06_time', resolution=500, modifiers=())\n" \
        " ===========================================================" )

    def get_image(self,
                  start_date,
                  end_date,
                  output_path=None,
                  skip_night_angle=25,
                  country='iberia',
                  resize_factor=1.0,
                  channel='vis_06',
                  lat_min=None,
                  lat_max=None,
                  lon_min=None,
                  lon_max=None,
                  width = None):
        
        country = country.lower()
        try:
            dtstart = datetime.datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%S")
            dtend = datetime.datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            print(f"[WARN] Failed to parse provided dates: {e}")
            now = datetime.datetime.now(datetime.timezone.utc)
            dtend = now
            dtstart = now - relativedelta(minutes=20)
            print(f"[INFO] Using fallback times: start={dtstart}, end={dtend}")
        output_path = output_path or os.path.join(os.getcwd(), 'tests')
        os.makedirs(output_path, exist_ok=True)

        area_def, chunk_ids = self._define_area(country, lat_min, lat_max, lon_min, lon_max, channel)
        products = self.selected_collection.search(dtstart=dtstart, dtend=dtend)
        print(f"Found {len(products)} matching timestep(s).")

        chunk_patterns = [f"_{cid}.nc" for cid in chunk_ids]

        for product in products:
            downloaded_files = []
            for entry in product.entries:
                if any(pattern in entry for pattern in chunk_patterns):
                    local_filename = os.path.basename(entry)
                    try:
                        ts_str = local_filename.split('_C_EUMT_')[1][:14]
                        ts_dt = datetime.datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                    except:
                        print(f"Failed to parse timestamp from filename: {local_filename}")
                        continue

                    if skip_night_angle is not None:
                        sun_elev = self._get_sun_elevation(ts_dt)
                        print(f"Sun elevation at {ts_dt} UTC: {sun_elev:.2f}°")
                        if sun_elev < skip_night_angle:
                            print("Skipping due to low sun angle.")
                            continue

                    print(f"Downloading: {local_filename}")
                    local_filepath = os.path.join(output_path, local_filename)
                    with product.open(entry=entry) as fsrc, open(local_filepath, 'wb') as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    downloaded_files.append(local_filepath)

            print(f"Saved: {[os.path.basename(f) for f in downloaded_files]}")
            try:
                scn = Scene(filenames=downloaded_files, reader='fci_l1c_nc')

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
                    img_name = f"{channel}_LON{lon_min}S{lon_max}_LAT{lat_min}S{lat_max}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
                img_height, img_width = red_scaled_uint8.shape
                if width is not None:
                    new_width = width
                    aspect_ratio = img_height / img_width
                    new_height = int(new_width * aspect_ratio)
                    red_scaled_uint8_resized = cv2.resize(red_scaled_uint8, (new_width, new_height), interpolation=cv2.INTER_AREA)
                else:
                    red_scaled_uint8_resized = red_scaled_uint8  # No resizing
                cv2.imwrite(os.path.join(output_path, img_name), resized)
                print(f"Saved image: {img_name}")

                del scn, scn_resampled, red, red_scaled_uint8
                gc.collect()
            except Exception as e:
                print(f"Error processing scene: {e}")
            finally:
                for file in downloaded_files:
                    if os.path.exists(file):
                        try:
                            os.remove(file)
                        except Exception as e:
                            print(f"Error deleting file {file}: {e}")

            print("===========================================")

# ========== MAIN ==========

if __name__ == "__main__":
    cons_key = '<your consumer key>'
    cons_secret = '<your consumer secret>'
    processor = EumetSatMTG(
        consumer_key=cons_key,
        consumer_secret=cons_secret
    )
    processor.get_image(
        start_date=None,
        end_date=None,
        output_path=None,
        skip_night_angle = 25,
        country='iberia',
        lon_min = None,
        lat_min = None,
        lon_max = None,
        lat_max = None,
        resize_factor=0.5,
        channel="vis_06"
    )
