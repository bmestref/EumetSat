import os
import shutil
import datetime
from eumdac import DataStore, AccessToken
from satpy import Scene
from pyresample.geometry import AreaDefinition
import cv2
from datetime import datetime, timedelta, timezone
from pvlib import solarposition
import pandas as pd
from pyproj import Geod
import numpy as np
import warnings
warnings.filterwarnings('ignore')

class EumetSatClient:
    def __init__(self, consumer_key, consumer_secret):
        self.key = consumer_key
        self.secret = consumer_secret

    def get_collection(self, country: str = None, lat_center=None, lon_center = None, square_size=None, base_pixel_dim=1000):
        """
        Returns: AreaDefinition, collection_id, mean_lon, mean_lat
        - By country: uses predefined bounding boxes.
        - By lat/lon + square_size (in km): builds bounding box around given center.
        """
        geod = Geod(ellps="WGS84")

        area_boxes = {
            "spain":           (-10.5, 34.8,  4.5, 45.0),
            "france":          (-5.5, 42.0,  8.64, 51.44),
            "germany":         (5.5, 47.0, 15.5, 55.0),
            "italy":           (6.5, 36.5, 19.0, 47.5),
            "united_kingdom":  (-8.0, 49.5, 2.5, 60.5),
            "portugal":        (-9.7, 36.8, -6.0, 42.2),
            "greece":          (19.0, 34.5, 28.5, 42.0),
            "full_globe":      (-180.0, -90.0, 180.0, 90.0)
        }

        rss_countries = {'greece'}
        default_collection = 'EO:EUM:DAT:MSG:HRSEVIRI'

        if country:
            country = country.lower()

            if country not in area_boxes:
                raise ValueError(f"Unknown country: {country}. Try one of: {list(area_boxes.keys())}")
            if country in rss_countries:
                collection_id = 'EO:EUM:DAT:MSG:MSG15-RSS'
            else:
                collection_id = default_collection

            lon_min, lat_min, lon_max, lat_max = area_boxes[country]
            mean_lon = (lon_min + lon_max) / 2
            mean_lat = (lat_min + lat_max) / 2
            delta_lon = lon_max - lon_min
            delta_lat = lat_max - lat_min

            if delta_lon > delta_lat:
                calculated_width = base_pixel_dim
                calculated_height = int(base_pixel_dim * (delta_lat / delta_lon))
            else:
                calculated_height = base_pixel_dim
                calculated_width = int(base_pixel_dim * (delta_lon / delta_lat))

        elif lat_center and lon_center and square_size:

            half_side_m = (square_size * 1000) / 2  

            lons = []
            lats = []
            for azimuth in [0, 90, 180, 270]:  # N, E, S, W
                lon, lat, _ = geod.fwd(lon_center, lat_center, azimuth, half_side_m)
                lons.append(lon)
                lats.append(lat)

            lon_min, lon_max = min(lons), max(lons)
            lat_min, lat_max = min(lats), max(lats)

            mean_lon, mean_lat = lon_center, lat_center

            if mean_lat >= 55 or mean_lat <= -55:
                collection_id = 'EO:EUM:DAT:METOP:AVHRR'
            elif 33 <= mean_lat <= 44 and 18 <= mean_lon <= 30:
                collection_id = 'EO:EUM:DAT:MSG:MSG15-RSS'  

            country = f"custom_{lat_center:.2f}_{lon_center:.2f}"
            calculated_width = base_pixel_dim
            calculated_height = base_pixel_dim

        else:
            raise ValueError("Either provide a valid country name, or lat_lon and square_size (in km).")

        area_def = AreaDefinition(
            area_id=country,
            description=f"{country.capitalize()} area",
            proj_id='latlon',
            projection={'proj': 'latlong'},
            width=calculated_width,
            height=calculated_height,
            area_extent=(lon_min, lat_min, lon_max, lat_max)
        )

        return area_def, collection_id, mean_lon, mean_lat, calculated_width, calculated_height
    
    def compute_sun_elevation(self, time_utc, lat, lon):
        times = pd.DatetimeIndex([time_utc])
        solpos = solarposition.get_solarposition(times, latitude=lat, longitude=lon)
        return solpos['elevation'].values[0]


    def get_image(self, country = None, lat_center=None, lon_center = None, square_size=None, skipsunangle = None, base_pixels = 1000, download_dir = None, start_dt = None, end_dt = None, bw = False):

        credentials = (self.key, self.secret)
        token = AccessToken(credentials)
        datastore = DataStore(token)
        area, collection_id, mean_lon, mean_lat, calculated_width, calculated_height = self.get_collection(country, lat_center, lon_center, square_size, base_pixels)
        selected_collection = datastore.get_collection(collection_id)

        identifier = ':'.join(collection_id.split(':')[-2:])

        if  identifier == "MSG:HRSEVIRI":
            reader = 'seviri_l1b_native'
            if bw:
                channel = ['HRV']
            else:
                channel = ['IR_016', 'VIS008', 'VIS006'] # red, green, blue
        elif identifier == "MSG:MSG15-RSS":
            reader = 'seviri_l1b_native'
            if bw:
                channel = ['HRV']
            else:
                channel = ['IR_016', 'VIS008', 'VIS006'] # red, green, blue
        else:
            raise ValueError(f"Unknown reader for collection {collection_id}")
        if end_dt is None and start_dt is None:
            end_dt = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            start_dt = (datetime.now(timezone.utc) - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%S')


        if download_dir != None:
            os.makedirs(download_dir, exist_ok=True)

        products = selected_collection.search(dtstart=start_dt, dtend=end_dt)

        for product in products:
            for entry in product.entries:
                if not entry.lower().endswith('.nat'):
                    continue
                if download_dir is not None:
                    file_path = os.path.join(download_dir, os.path.basename(entry))
                else:
                    file_path = os.path.join(os.getcwd(), os.path.basename(entry))
                file_datetime_str = os.path.basename(entry).split('-')[-2].split('.')[0][:12]
                print(f'=== Retrieving Image for identifier = {identifier}, channel = {channel}, datetime = {datetime.strptime(file_datetime_str, "%Y%m%d%H%M")} ===')
                print('')
                try:
                    with product.open(entry=entry) as fsrc, open(file_path, mode='wb') as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                        print(f'Download of file {fsrc.name} finished.')
                except Exception as error:
                    print(f"Download error: {error}")
                    continue

                try:
                    scn = Scene(reader=reader, filenames=[file_path])
                    available_datasets = scn.available_dataset_names()
                    print(f"Available datasets in {os.path.basename(file_path)}: {available_datasets}")
                    scn.load(channel)
                    area_for_resampling = AreaDefinition(
                        area_id=area.area_id,
                        description=area.description,
                        proj_id=area.proj_id,
                        projection=area.proj_dict,
                        width=calculated_width,
                        height=calculated_height,
                        area_extent=area.area_extent
                    )



                    scn_resampled = scn.resample(area_for_resampling)
                    if bw:
                        hrv = scn_resampled[channel]
                    else:
                        red = scn_resampled[channel[0]]
                        green = scn_resampled[channel[1]]
                        blue = scn_resampled[channel[2]]

                    if skipsunangle != None:
                        img_time = datetime.strptime(file_datetime_str, "%Y%m%dT%H%M")
                        sun_elevation = self.compute_sun_elevation(img_time, mean_lat, mean_lon)

                        if sun_elevation < skipsunangle:
                            print(f"Skipping due to low sun angle. Angle = {sun_elevation}")
                            os.remove(file_path)
                            continue
                    try:
                        if bw:
                            img_array = hrv.values
                            print(f"HRV data shape: {hrv.values.shape}, dtype: {hrv.values.dtype}, min: {hrv.values.min()}, max: {hrv.values.max()}")

                            img_norm = (img_array - img_array.min()) / (img_array.max() - img_array.min())

                            gamma = 1.5  
                            img_gamma = img_norm ** (1 / gamma)

                            # Scale to 0–255
                            img_scaled = (img_gamma * 255).clip(0, 255).astype('uint8')

                            print(f"Normalized and gamma-adjusted image: shape {img_scaled.shape}, dtype {img_scaled.dtype}")

                            img_gray = cv2.cvtColor(img_scaled, cv2.COLOR_RGB2GRAY)
                            save_img = img_gray

                            identifier_splitted = '_'.join(identifier.split(':'))
                            filename = f"bw_{identifier_splitted}_{country}_{file_datetime_str}.jpg"
                            
                            if download_dir is not None:
                                full_path = os.path.join(download_dir, filename)
                            else:
                                full_path = os.path.join(os.getcwd(), filename)

                            cv2.imwrite(full_path, save_img)
                            print(f"Saved image to {full_path}")

                        else:
                            color_img = np.stack([red.values, green.values, blue.values], axis=-1)
                            color_img = color_img.astype('float32')
                            color_img -= color_img.min()
                            color_img /= color_img.max()
                            color_img *= 255.0
                            color_img = np.clip(color_img, 0, 255).astype('uint8')

                            identifier_splitted = '_'.join(identifier.split(':'))
                            filename = f"color_{identifier_splitted}_{country}_{file_datetime_str}.jpg"
                            
                            if download_dir is not None:
                                full_path = os.path.join(download_dir, filename)
                            else:
                                full_path = os.path.join(os.getcwd(), filename)

                            cv2.imwrite(full_path, color_img)
                            print(f"Saved image to {full_path}")


                    except Exception as e:
                        print(f"Error saving image: {e}")

                except Exception as e:
                    print(f"Processing error for {file_path}: {e}")

                try:
                    os.remove(file_path)
                    print(f"Removed {file_path}")
                except Exception as e:
                    print(f"Error deleting {file_path}: {e}")
                print('')

if __name__ == "__main__":
    consumer_key = '<YOUR CONSUMER KEY>'
    consumer_secret = '<YOUR CONSUMER SECRET>'
    client = EumetSatClient(consumer_key = consumer_key, consumer_secret = consumer_secret)
    client.get_image(country='greece', start_dt='2025-07-17T14:08:00', end_dt= '2025-07-17T14:23:00')

