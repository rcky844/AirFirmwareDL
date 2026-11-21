import hashlib
import requests
import os
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote
from tqdm import tqdm
from colorama import Fore, Style, init

init(autoreset=True) 

ASCII_ART = r"""
    _    _      _____ _                                      ____  _     
   / \  (_)_ __|  ___(_)_ __ _ __ _____      ____ _ _ __ ___|  _ \| |    
  / _ \ | | '__| |_  | | '__| '_ ` _ \ \ /\ / / _` | '__/ _ \ | | | |    
 / ___ \| | |  |  _| | | |  | | | | | \ V  V / (_| | | |  __/ |_| | |___ 
/_/   \_\_|_|  |_|   |_|_|  |_| |_| |_|\_/\_/ \__,_|_|  \___|____/|_____| 
"""

FOTA_BASE_URL = "http://twsfota.198509.xyz/tws_fota_bin"
FOTA_SUFFIX = "_fota"
VERSION_PATTERN = re.compile(r"V\d+(?:\.\d+){2,3}")
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CHECKSUM_FILE = 'checksums.json'

EARPHONE_MODELS = {
    "1": {
        "name": "V5.2 TB",
        "codename": "S505",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V310.6.505"
    },
    "2": {
        "name": "V4.9 TB",
        "codename": "S305",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V31.6.305"
    },
    "3": {
        "name": "V1E",
        "codename": "S45",
        "chipset": "AB1562E",
        "legacy_version_prefix": "V31.6.45"
    }
}

def print_ascii_welcome():
    """Print ASCII art and a welcome message."""
    print(Fore.CYAN + ASCII_ART)
    print(Fore.BLUE + Style.BRIGHT + "=" * 50)

def get_fota_url(model):
    """Build the FOTA index URL for a model."""
    return f"{FOTA_BASE_URL}/{model['codename']}/{model['chipset']}/fota"

def extract_full_version(folder_name):
    """Extract the full version string (e.g. V310.6.505.153) from a FOTA folder name."""
    match = VERSION_PATTERN.search(folder_name)
    return match.group(0) if match else None

def fetch_firmware_versions(model):
    """Read the FOTA index and return the firmware packages available for a model."""
    url = get_fota_url(model)
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    firmwares = {}
    for entry in root.iter('entry'):
        folder = (entry.text or '').strip()
        if not folder.endswith(FOTA_SUFFIX):
            continue

        folder_lower = folder.lower()
        if model['codename'].lower() not in folder_lower or model['chipset'].lower() not in folder_lower:
            continue

        full_version = extract_full_version(folder)
        if not full_version:
            continue

        firmwares[full_version] = {
            'version': full_version,
            'folder': folder,
            'package': folder[:-len(FOTA_SUFFIX)],
            'source_version': (entry.get('key') or '').strip()
        }

    if not firmwares:
        raise ValueError(f"No firmware versions found in {url}")
    return firmwares

def sort_versions(versions):
    """Sort full version strings from newest to oldest."""
    return sorted(versions, key=lambda v: tuple(int(part) for part in v[1:].split('.')), reverse=True)

def resolve_version_input(version_input, firmwares):
    """Accept a full version string, its final build number, or a FOTA source version."""
    value = version_input.strip()
    if value in firmwares:
        return value

    matches = [v for v in firmwares if v.rsplit('.', 1)[-1] == value]
    if len(matches) == 1:
        return matches[0]

    for version, firmware in firmwares.items():
        if firmware['source_version'] == value:
            return version
    return None

def calculate_sha256(file_path):
    """Calculate SHA-256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def download_file(url, file_path, version):
    """Download a file from a URL with a progress bar."""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        if total_size == 0:
            print(Fore.RED + Style.BRIGHT + f"Cannot determine size of the file from URL: {url}")
            return False
        
        with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024, 
                  desc=version, bar_format='{l_bar}{bar} | {n_fmt}/{total_fmt}', 
                  colour='cyan') as bar:
            with open(file_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        bar.update(len(chunk))
        
        return True
    except requests.exceptions.HTTPError as e:
        print(Fore.RED + Style.BRIGHT + f"Error downloading file from {url}: {e}")
    except Exception as e:
        print(Fore.RED + Style.BRIGHT + f"An unexpected error occurred: {e}")
    return False

def load_checksums(json_file):
    """Load checksums from a JSON file."""
    with open(json_file, 'r') as f:
        checksums = json.load(f)

    # Older releases keyed checksums by the final build number only.
    for model in EARPHONE_MODELS.values():
        model_checksums = checksums.get(model['name'], {})
        prefix = model['legacy_version_prefix']
        for key in list(model_checksums):
            if str(key).isdigit():
                model_checksums[f"{prefix}.{key}"] = model_checksums.pop(key)
    return checksums

def compare_checksums(model_name, version, left_sha256, right_sha256, checksums):
    """Compare calculated checksums with expected checksums."""
    expected = checksums.get(model_name, {}).get(version, {"left": None, "right": None})
    return {
        "left_file_checksum": left_sha256,
        "right_file_checksum": right_sha256,
        "left_match": left_sha256.lower() == expected.get("left", "").lower() if expected.get("left") else None,
        "right_match": right_sha256.lower() == expected.get("right", "").lower() if expected.get("right") else None
    }

def print_comparison_results(results):
    """Print comparison results in a formatted manner."""
    print(Fore.GREEN + Style.BRIGHT + "\nChecksum Comparison Results:")
    print(Fore.GREEN + Style.BRIGHT + "=" * 50)
    print(Fore.GREEN + Style.BRIGHT + f"Left file checksum:  {results['left_file_checksum']}")
    print(Fore.GREEN + Style.BRIGHT + f"Right file checksum: {results['right_file_checksum']}")
    print(Fore.GREEN + Style.BRIGHT + f"Left file match:     {get_match_result(results['left_match'])}")
    print(Fore.GREEN + Style.BRIGHT + f"Right file match:    {get_match_result(results['right_match'])}")
    print(Fore.GREEN + Style.BRIGHT + "=" * 50)

def get_match_result(match):
    if match is True:
        return 'Match'
    elif match is False:
        return 'No match'
    else:
        return 'Not available in checksums.json'

def get_version_folder(model, version):
    """Get the local download folder for a full version string."""
    return os.path.join(os.getcwd(), f"{model['name']}_{version}")

def sanitize_filename(name):
    """Decode URL escapes and replace characters unsafe in local filenames."""
    return INVALID_FILENAME_CHARS.sub('_', unquote(name)).strip(' .')

def construct_file_path(model, firmware, side):
    """Construct the local file path for a firmware package and side."""
    file_name = f"{sanitize_filename(firmware['package'])}_{side}_FotaPackage.bin"
    return os.path.join(get_version_folder(model, firmware['version']), file_name)

def construct_download_url(model, firmware, side):
    """Construct the download URL for a firmware package and side."""
    folder = quote(firmware['folder'], safe='')
    file_name = f"{quote(firmware['package'], safe='')}_{side}_FotaPackage.bin"
    return f"{FOTA_BASE_URL}/{model['codename']}/{model['chipset']}/{folder}/{file_name}"

def main():
    """Main function to run the program."""
    print_ascii_welcome()
    checksums = load_checksums(CHECKSUM_FILE)

    while True:
        try:
            print(Fore.YELLOW + Style.BRIGHT + "Available Earphone Models:")
            for number, model in EARPHONE_MODELS.items():
                print(Fore.GREEN + Style.BRIGHT + f"{number}. {model['name']}")
            
            model_input = input(Fore.YELLOW + Style.BRIGHT + "\nEnter the number of the earphone model: ").strip()

            if model_input not in EARPHONE_MODELS:
                print(Fore.RED + Style.BRIGHT + "Invalid model number provided. Please try again.")
                continue

            model = EARPHONE_MODELS[model_input]
            print(Fore.CYAN + Style.BRIGHT + f"Checking available versions for {model['name']}...")
            try:
                firmwares = fetch_firmware_versions(model)
            except Exception as e:
                print(Fore.RED + Style.BRIGHT + f"Could not check firmware versions from {get_fota_url(model)}: {e}")
                continue

            latest_version = sort_versions(firmwares)[0]
            version_input = input(Fore.YELLOW + Style.BRIGHT + f"Enter the firmware version for {model['name']} (e.g., {latest_version}), or press Enter to display available versions: ").strip()

            if not version_input:
                print(Fore.YELLOW + Style.BRIGHT + f"\nAvailable Versions for {model['name']}:")
                for version in sort_versions(firmwares):
                    print(Fore.GREEN + Style.BRIGHT + f"Version {version} is available.")
                version_input = input(Fore.YELLOW + Style.BRIGHT + f"\nPlease enter the firmware version you want to download for {model['name']}: ").strip()

            version = resolve_version_input(version_input, firmwares)
            if version is None:
                print(Fore.RED + Style.BRIGHT + "Invalid version provided. Please try again.")
                continue

            firmware = firmwares[version]
            version_folder = get_version_folder(model, version)
            left_file_path = construct_file_path(model, firmware, "left")
            right_file_path = construct_file_path(model, firmware, "right")

            if os.path.exists(version_folder) and os.path.isfile(left_file_path) and os.path.isfile(right_file_path):
                print(Fore.GREEN + Style.BRIGHT + f"Version {version} for {model['name']} is already downloaded.")
                left_sha256 = calculate_sha256(left_file_path)
                right_sha256 = calculate_sha256(right_file_path)
                results = compare_checksums(model['name'], version, left_sha256, right_sha256, checksums)
                print_comparison_results(results)
                break

            os.makedirs(version_folder, exist_ok=True)

            left_file_url = construct_download_url(model, firmware, "left")
            right_file_url = construct_download_url(model, firmware, "right")

            print(Fore.CYAN + Style.BRIGHT + f"Downloading files for {model['name']} version {version}...")
            left_downloaded = download_file(left_file_url, left_file_path, version)
            right_downloaded = download_file(right_file_url, right_file_path, version)

            if left_downloaded and right_downloaded:
                print(Fore.GREEN + Style.BRIGHT + "\nFiles downloaded successfully!")
                left_sha256 = calculate_sha256(left_file_path)
                right_sha256 = calculate_sha256(right_file_path)
                results = compare_checksums(model['name'], version, left_sha256, right_sha256, checksums)
                print_comparison_results(results)
            else:
                print(Fore.RED + Style.BRIGHT + "Failed to download one or both files.")
            
            break
                    
        except KeyboardInterrupt:
            print(Fore.RED + Style.BRIGHT + "\nProcess interrupted by user.")
            return
        except Exception as e:
            print(Fore.RED + Style.BRIGHT + f"An unexpected error occurred: {str(e)}")
            import traceback
            print(Fore.RED + Style.BRIGHT + "Error traceback:")
            print(traceback.format_exc())
            return

if __name__ == "__main__":
    main()
