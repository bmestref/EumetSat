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
import time
warnings.filterwarnings('ignore')

class EumetSatMSG:
    def __init__(self, consumer_key=None, consumer_secret=None):
        if not consumer_key or not consumer_secret:
            raise Exception("Consumer key and secret are required.")
        self.last_picture = False
        self.credentials = (consumer_key, consumer_secret)
        self.token = AccessToken(self.credentials)
        self.datastore = DataStore(self.token)
        self.selected_collection = self.datastore.get_collection('EO:EUM:DAT:MSG:MSG15-RSS')
        self.resolution = {
            'HRV': 1000,
            'IR_016': 3000,
            'IR_039': 3000,
            'IR_087': 3000,
            'IR_097': 3000,
            'IR_108': 3000,
            'IR_120': 3000,
            'IR_134': 3000,
            'VIS006': 3000,
            'VIS008': 3000,
            'WV_062': 3000,
            'WV_073': 3000
        }

        composite_map = {
            '24h_microphysics': 3000,
            'airmass': 3000,
            'ash': 3000,
            'cloud_phase_distinction': 3000,
            'cloud_phase_distinction_raw': 3000,
            'cloudtop': 3000,
            'cloudtop_daytime': 3000,
            'colorized_ir_clouds': 3000,
            'convection': 3000,
            'day_microphysics': 3000,
            'day_microphysics_winter': 3000,
            'day_severe_storms': 3000,
            'day_severe_storms_tropical': 3000,
            'dust': 3000,
            'fog': 3000,
            'green_snow': 3000,
            'hrv_clouds': 1000,
            'hrv_fog': 1000,
            'hrv_severe_storms': 1000,
            'hrv_severe_storms_masked': 1000,
            'ir108_3d': 3000,
            'ir_cloud_day': 3000,
            'ir_overview': 3000,
            'ir_sandwich': 3000,
            'natural_color': 3000,
            'natural_color_nocorr': 3000,
            'natural_color_raw': 3000,
            'natural_color_raw_with_night_ir': 3000,
            'natural_color_with_night_ir': 3000,
            'natural_color_with_night_ir_hires': 3000,
            'natural_enh': 3000,
            'natural_enh_with_night_ir': 3000,
            'natural_enh_with_night_ir_hires': 3000,
            'natural_with_night_fog': 3000,
            'night_fog': 3000,
            'night_ir_alpha': 3000,
            'night_ir_with_background': 3000,
            'night_ir_with_background_hires': 3000,
            'night_microphysics': 3000,
            'night_microphysics_tropical': 3000,
            'overshooting_tops': 3000,
            'overview': 3000,
            'overview_raw': 3000,
            'realistic_colors': 3000,
            'rocket_plume_day': 3000,
            'rocket_plume_night': 3000,
            'snow': 3000,
            'vis_sharpened_ir': 3000
        }

        self.resolution.update(composite_map)

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
            'greece':self._create_area('greece', [19.0, 34.5, 29.5, 42.5], channel),
            'balkans':self._create_area('balkans', [13.0, 36.0, 30.0, 47.5], channel)
        }

        countries_dict = {'iberia':area_defs['iberia'],
                        'balearic_islands':area_defs['balearic_islands'],
                        'france':area_defs['france'],
                        'uk_ireland':area_defs['uk_ireland'],
                        'germany_benelux':area_defs['germany_benelux'],
                        'scandinavia':area_defs['scandinavia'],
                        'italy':area_defs['italy'],
                        'greece':area_defs['greece'],
                        'balkans':area_defs['balkans']}

        
        use_custom_roi = all([
            lat_min is not None,
            lat_max is not None,
            lon_min is not None,
            lon_max is not None
        ])

        if use_custom_roi:
            manual_extent = [lon_min, lat_min, lon_max, lat_max]
            area_def_custom = self._create_area('custom_area', manual_extent, channel)

            area_def = area_def_custom
        else:
            if country not in countries_dict:
                raise ValueError(f"Invalid country: {country}. Choose from: {list(countries_dict.keys())}")

            area_def = countries_dict[country]
        return area_def

    def get_available_ids(self):
        print("Channel Name".ljust(35), "Resolution (m/px)")
        print("-" * 50)
        for channel, res in sorted(self.resolution.items()):
            print(channel.ljust(35), res)

    def get_image(self,
                  start_date,
                  end_date,
                  output_path=None,
                  skip_night_angle=25,
                  country='iberia',
                  channel='HRV',
                  lat_min=None,
                  lat_max=None,
                  lon_min=None,
                  lon_max=None,
                  save_as_npy = False,
                  enhance_img = False):
        
        start = time.time()
        if country is not None:
            country = country.lower()

        try:
            dtstart = datetime.datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%S")
            dtend = datetime.datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%S")
        except:
            self.last_picture = True
            dtstart = datetime.datetime.now(datetime.timezone.utc) - relativedelta(minutes=15)
            dtend = datetime.datetime.now(datetime.timezone.utc)

        output_path = output_path or os.path.join(os.getcwd(), 'imgs')
        os.makedirs(output_path, exist_ok=True)

        products = self.selected_collection.search(dtstart=dtstart, dtend=dtend)
        print(f"Found {len(products)} matching timestep(s).")
        existing_stems = {os.path.splitext(f)[0].lower() for f in os.listdir(output_path)}

        # If no start datetime is provided, retrieve the most recent product available
        for i, product in enumerate(products):
            if self.last_picture and i > 0:
                continue
            for entry in product.entries:
                try:
                    local_filename = os.path.basename(entry)
                    if not local_filename.endswith('.nat'):
                        continue
                    try:
                        ts_str = local_filename.split('-')[5]  
                        ts_str = ts_str.split('.')[0]          
                        ts_dt = datetime.datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                    except Exception as e:
                        print(f"Failed to parse timestamp from filename: {local_filename} ({e})")
                        continue

                    effective_channel = (channel or 'HRV')          # always a string
                    channel_norm = effective_channel.lower()   

                    base_name = f"{channel_norm}_{ts_dt.strftime('%Y%m%dT%H%M%S')}" 

                    # Skip if we've already produced this timestamp (either .jpg or .npy), any case
                    if base_name.lower() in existing_stems:
                        print(f"{base_name} already exists. Skipping download.")
                        continue

                    # === SKIP IF THE SUN ANGLE IS BELOW A CERTAIN THRESHOLD ===
                    if skip_night_angle:
                        sun_elev = self._get_sun_elevation(ts_dt)
                        print(f"Sun elevation at {ts_dt} UTC: {sun_elev:.2f}°")
                        if sun_elev < skip_night_angle: # 25º as threshold
                            print("Skipping due to low sun angle.")
                            continue

                    with product.open(entry=entry) as fsrc:

                        print(f"Downloading: {local_filename} | UTC Time: {ts_dt.strftime('%Y-%m-%d %H:%M')}")
                        local_filepath = os.path.join(output_path, local_filename)
                        with open(local_filepath, 'wb') as fdst:
                            shutil.copyfileobj(fsrc, fdst)
                        print(f"Saved: {local_filename}")
                        try:
                            scn = Scene(filenames=[local_filepath], reader='seviri_l1b_native')
                            # print(scn.available_composite_ids())
                            scn.load([effective_channel])
                            area_def = self._define_area(country, lat_min, lat_max, lon_min, lon_max, channel)
                            scn_resampled = scn.resample(area_def)
                            img = scn_resampled[effective_channel].values
                            if img.ndim == 3 and img.shape[0] == 3:
                                img = np.moveaxis(img, 0, -1)
            
                            if save_as_npy:
                                ts_str = ts_dt.strftime('%Y%m%dT%H%M%S')
                                base_name = f"{channel_norm.lower()}_{ts_str}"
                                npy_path = os.path.join(output_path, f"{base_name}.npy")
                                np.save(npy_path, img)
                                print(f'Saved at {output_path}')
                                print(f"Saved array: {os.path.basename(npy_path)}  shape={img.shape} dtype={img.dtype}")
                                # print(f'Just to see the saved file: {img} ')

                            else:
                                img = self.handle_color(img, enhance = enhance_img)
                                if any(v is None for v in [lon_min, lon_max, lat_min, lat_max]) and country is not None:
                                    img_name = f"MSG_{channel}_{country}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
                                elif any(v is not None for v in [lon_min, lon_max, lat_min, lat_max]) and country is None:
                                    img_name = f"MSG_{channel}_LON{lon_min}S{lon_max}_LAT{lat_min}S{lat_max}_{ts_dt.strftime('%Y%m%dT%H%M%S')}.jpg"
                                else:
                                    raise Exception('Mixture of predefined country and customs areas found. Pick one please.')
                                cv2.imwrite(os.path.join(output_path, img_name), img)
                                print(f'Saved at {output_path}')
                                print(f"Saved image: {img_name}")
                            existing_stems.add(base_name)
                            os.remove(local_filepath)

                        except Exception as e:
                            print(f"Error processing scene: {e}")

                        print('====================================================')

                except Exception as e:
                    print(f"Download failed for {entry}: {e}")

                finally:
                    elapsed = time.time() - start
        end = time.time()
        elapsed = end - start
        print(f'Ended execution at: {datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")}. It took {elapsed:.2f} seconds.')

# ========== MAIN ==========

if __name__ == "__main__":
    cons_key = '<your consumer key>'
    cons_secret = '<your consumer secret>'
    processor = EumetSatMSG(
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
        channel="HRV"
    )
