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
        self.last_picture = False
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

    def handle_color(self, img, qmin=1, qmax=99, enhance = True):
        if img.ndim == 3 and img.shape[-1] == 3:
            if np.allclose(img[...,0], img[...,1]) and np.allclose(img[...,1], img[...,2]):
                img = img[...,0] 
        if enhance:
            data = np.nan_to_num(img, nan=0.0)
            if data.ndim == 2:  # grayscale
                vmin, vmax = np.percentile(data, (qmin, qmax))
                scaled = np.clip((data - vmin) / (vmax - vmin), 0, 1)
                return (255 * scaled).astype(np.uint8)

            elif data.ndim == 3 and data.shape[-1] == 3:  # RGB
                out = np.zeros_like(data, dtype=np.uint8)
                for i in range(3):
                    vmin, vmax = np.percentile(data[..., i], (qmin, qmax))
                    scaled = np.clip((data[..., i] - vmin) / (vmax - vmin), 0, 1)
                    out[..., i] = (255 * scaled).astype(np.uint8)
                return out
        else:
            return img

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
            'balearic_islands': self._create_area('balearic_islands', [1.0, 38.5, 4.5, 40.27], channel),
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
            'balearic_islands': [area_defs['balearic_islands'], ['0034', '0035']],
            'france': [area_defs['france'], ['0035', '0036', '0037']],
            'uk_ireland': [area_defs['uk_ireland'], ['0037', '0038', '0039']],
            'germany_benelux': [area_defs['germany_benelux'], ['0036', '0037', '0038']],
            'scandinavia': [area_defs['scandinavia'], ['0038', '0039', '0040']],
            'italy': [area_defs['italy'], ['0033', '0034', '0035', '0036']],
            'greece': [area_defs['greece'], ['0033', '0034', '0035']],
            'balkans': [area_defs['balkans'], ['0033', '0034', '0035', '0036']]
        }

        if all(v is not None for v in [lat_min, lat_max, lon_min, lon_max]) and country is None:
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
        " ======================== IR 38 ========================  \n" \
        " DataID(name='ir_38', wavelength=WavelengthRange(min=3.4, central=3.8, max=4.2, unit='µm'), resolution=1000, calibration=<2>, modifiers=())\n" \
        " ======================== NIR 22 ========================  \n" \
        " DataID(name='nir_22', wavelength=WavelengthRange(min=2.2, central=2.25, max=2.3, unit='µm'), resolution=500, calibration=<1>, modifiers=())\n" \
        " ======================== VIS 06 ========================  \n" \
        " DataID(name='vis_06', wavelength=WavelengthRange(min=0.59, central=0.64, max=0.69, unit='µm'), resolution=500, calibration=<1>, modifiers=())\n" \
        " ===========================================================" )

    def get_image(self,
                  start_date,
                  end_date,
                  output_path=None,
                  skip_night_angle=25,
                  country='iberia',
                  channel='vis_06',
                  lat_min=None,
                  lat_max=None,
                  lon_min=None,
                  lon_max=None,
                  width = None,
                  save_as_npy = False,
                  enhance_img = False
                  ):
        if country is not None:
            country = country.lower()
        try:
            dtstart = datetime.datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%S")
            dtend = datetime.datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            self.last_picture = True
            print(f"[WARN] Failed to parse provided dates: {e}")
            now = datetime.datetime.now(datetime.timezone.utc)
            dtend = now
            dtstart = now - relativedelta(minutes=20)
            print(f"[INFO] Using fallback times: start={dtstart}, end={dtend}")
        output_path = output_path or os.path.join(os.getcwd(), 'imgs')
        os.makedirs(output_path, exist_ok=True)

        area_def, chunk_ids = self._define_area(country, lat_min, lat_max, lon_min, lon_max, channel)
        products = self.selected_collection.search(dtstart=dtstart, dtend=dtend)
        print(f"Found {len(products)} matching timestep(s).")

        chunk_patterns = [f"_{cid}.nc" for cid in chunk_ids]

        for i, product in enumerate(products):
            if self.last_picture and i > 0:
                continue
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
                scn.load([channel])  
                scn_resampled = scn.resample(area_def)
                img = scn_resampled[channel].values
                img =(img).astype(np.float32)
                if any(v is None for v in [lon_min, lon_max, lat_min, lat_max]) and country is not None:
                    img_name = f"MTG_{channel}_{country}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
                elif any(v is not None for v in [lon_min, lon_max, lat_min, lat_max]) and country is None:
                    img_name = f"MTG_{channel}_LON{lon_min}S{lon_max}_LAT{lat_min}S{lat_max}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
                else:
                    raise Exception('Mixture of predefined country and customs areas found. Pick one please.')
                img_height, img_width = img.shape
                if width is not None:
                    new_width = width
                    aspect_ratio = img_height / img_width
                    new_height = int(new_width * aspect_ratio)
                    img_resized = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)
                else:
                    img_resized = img  # No resizing
                
                if save_as_npy:
                    ts_str = ts_dt.strftime('%Y%m%dT%H%M%S')
                    base_name = f"{channel.lower()}_{ts_str}"
                    npy_path = os.path.join(output_path, f"{base_name}.npy")
                    np.save(npy_path, img_resized)
                    print(f'Saved at {output_path}')
                    print(f"Saved array: {os.path.basename(npy_path)}  shape={img_resized.shape} dtype={img_resized.dtype}")
                else:
                    img_scaled = self.handle_color(img_resized, enhance = enhance_img)
                    cv2.imwrite(os.path.join(output_path, img_name), img_scaled)
                    print(f"Saved image: {img_name}")

                del scn
                del scn_resampled
                del img
                del img_resized
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
        channel="vis_06"
    )
