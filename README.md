# EUMETSAT MSG/MTG Satellite Imagery Processor

This repository provides Python classes and executable Python files to download, process, and visualize satellite imagery data from the **EUMETSAT Meteosat Second (MSG) and Third (MTG) Generation** FCI instrument. It uses the `eumdac` API to access relevant data collections and can extract imagery based on region, time, and spectral channel. This repository is intended to provide a user-friendly access to Satellite Imagery for Computer Vision and Metheorological Projects. It is planned to gradually add more features and satellites in the available databse.

---

## 🚀 Features

- **Download L1C FCI chunk data** from EUMETSAT for selected regions or custom coordinates.
- **Supports multiple spectral channels:** VIS 06, NIR 22, IR 38, and IR 105 for MTG, and composite datasets for MSG (including isolated bands).
- **Skip night-time imagery** (optional), useful for visible light channels.
- **Custom area or predefined European countries support.**
- **Sun elevation filtering** using `Skyfield` to exclude low-angle sun scenes.
- **Automated image resampling** and export to `.jpg`.
- **Automatic cleanup of downloaded chunks.**

## Supported Regions
You can select from these predefined countries:
- **iberia** (Spain and Portugal)
- **mallorca**
- **france**
- **uk_ireland** (UK and Ireland)
- **germany_benelux** (Germany, Netherlands, Belgique, Switzerland and Luxemburg) 
- **scandinavia** (Norway, Sweden and Denmark)
- **italy**
- **greece**
- **balkans** (North Macedonia, Albania, Kosovo, Montenegro, Bosnia and Herzegovina, Serbia, Croatia, Bulgaria, Romania, Turkey)

Or define a custom bounding box via lat_min, lat_max, lon_min, and lon_max.

## 🛠️ Requirements

Install dependencies via:

```bash
pip install eumdac satpy pyresample opencv-python skyfield shapely pyproj python-dateutil
```

### Usage
The code can be called from an already built class or via the command line, depicted as follows:

```bash
python EumetSat_MTG_executable.py --consumer_key --consumer_secret --start_date --end_date --output_path --skip_night_angle --country --width --channel --lat_min --lat_max --lon_min --lon_max
```
Each of these parameters are defined below:
- **consumer_key**:
- **consumer_secret**:
- **start_date**:
- **end_date**:
- **output_path**:
- **skip_night_angle**:
- **country**:
- **width**:
- **channel**:
- **lat_min**:
- **lat_max**:
- **lon_min**:
- **lon_max**:
- 
