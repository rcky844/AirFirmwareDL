import argparse
import hashlib
import requests
import os
import json
import re
import shutil
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
BACKUP_DIR = 'Firmware-backup'
BACKUP_CHECKSUM_FILE = 'checksums.txt'

EARPHONE_MODELS = [
    {
        "name": "V5.3 TB",
        "codename": "S523",
        "chipset": "AB1563E",
        "legacy_version_prefix": "V310.6.523"
    },
    {
        "name": "V5.2 TB",
        "codename": "S505",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V310.6.505"
    },
    {
        "name": "V4.9 TB",
        "codename": "S305",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V31.6.305"
    },
    {
        "name": "V1E",
        "codename": "S45",
        "chipset": "AB1562E",
        "legacy_version_prefix": "V31.6.45"
    }
]

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

def version_sort_key(version):
    """Numeric key for ordering full version strings."""
    return tuple(int(part) for part in version[1:].split('.'))

def sort_versions(versions):
    """Sort full version strings from newest to oldest."""
    return sorted(versions, key=version_sort_key, reverse=True)

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

    # Older releases keyed checksums by display name and by final build number only.
    for model in EARPHONE_MODELS:
        codename = model['codename']
        legacy_entries = checksums.pop(model['name'], {})
        model_checksums = checksums.setdefault(codename, {})
        for version, hashes in legacy_entries.items():
            model_checksums.setdefault(version, hashes)

        prefix = model['legacy_version_prefix']
        for key in list(model_checksums):
            if str(key).isdigit():
                model_checksums[f"{prefix}.{key}"] = model_checksums.pop(key)
    return checksums

def compare_checksums(codename, version, left_sha256, right_sha256, checksums):
    """Compare calculated checksums with expected checksums."""
    expected = checksums.get(codename, {}).get(version, {"left": None, "right": None})
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

def verify_files(codename, version, left_path, right_path, checksums):
    """Hash both packages and print the checksum comparison."""
    results = compare_checksums(codename, version, calculate_sha256(left_path), calculate_sha256(right_path), checksums)
    print_comparison_results(results)

def get_version_folder(model, version):
    """Get the local download folder for a full version string."""
    return os.path.join(os.getcwd(), version)

def sanitize_filename(name):
    """Decode URL escapes and replace characters unsafe in local filenames."""
    return INVALID_FILENAME_CHARS.sub('_', unquote(name)).strip(' .')

def get_package_file_name(firmware, side):
    """Build the local file name for one side of a firmware package."""
    return f"{sanitize_filename(firmware['package'])}_{side}_FotaPackage.bin"

def construct_file_path(model, firmware, side):
    """Construct the local file path for a firmware package and side."""
    return os.path.join(get_version_folder(model, firmware['version']), get_package_file_name(firmware, side))

def construct_download_url(model, firmware, side):
    """Construct the download URL for a firmware package and side."""
    folder = quote(firmware['folder'], safe='')
    file_name = f"{quote(firmware['package'], safe='')}_{side}_FotaPackage.bin"
    return f"{FOTA_BASE_URL}/{model['codename']}/{model['chipset']}/{folder}/{file_name}"

def get_backup_folder(model, version):
    """Get the backup folder for a full version string."""
    return os.path.join(os.getcwd(), BACKUP_DIR, model['codename'], version)

def save_checksums(json_file, checksums):
    """Write checksums back to disk with versions sorted by name."""
    codename_order = {model['codename']: index for index, model in enumerate(EARPHONE_MODELS)}
    ordered = {
        codename: {version: checksums[codename][version] for version in sorted(checksums[codename], key=version_sort_key)}
        for codename in sorted(checksums, key=lambda name: (codename_order.get(name, len(codename_order)), name))
    }
    with open(json_file, 'w') as f:
        json.dump(ordered, f, indent=4)
        f.write('\n')

def write_backup_checksums(backup_folder, left_sha256, right_sha256):
    """Write the checksum file that accompanies backed-up firmware packages."""
    with open(os.path.join(backup_folder, BACKUP_CHECKSUM_FILE), 'w') as f:
        f.write(f"left - {left_sha256}\n")
        f.write(f"right - {right_sha256}\n")

def get_legacy_package_file_name(firmware, side):
    """Build the file name used by older backups, which kept the URL escaping."""
    return f"{quote(firmware['package'], safe='')}_{side}_FotaPackage.bin"

def get_existing_backup_paths(model, firmware):
    """Return paths to both backed-up packages for a version, if they exist."""
    backup_folder = get_backup_folder(model, firmware['version'])
    paths = []
    for side in ('left', 'right'):
        candidates = (get_package_file_name(firmware, side), get_legacy_package_file_name(firmware, side))
        match = None
        for name in candidates:
            path = os.path.join(backup_folder, name)
            if os.path.isfile(path):
                match = path
                break
        if match is None:
            return None
        paths.append(match)
    return paths

def backup_is_complete(model, firmware):
    """Check whether a version already has backed-up packages and a checksum file."""
    backup_folder = get_backup_folder(model, firmware['version'])
    if not os.path.isfile(os.path.join(backup_folder, BACKUP_CHECKSUM_FILE)):
        return False

    for side in ('left', 'right'):
        names = (get_package_file_name(firmware, side), get_legacy_package_file_name(firmware, side))
        if not any(os.path.isfile(os.path.join(backup_folder, name)) for name in names):
            return False
    return True

def register_version(model, firmware, checksums, label="New version"):
    """Back up a version and record its checksums if they are not already stored."""
    version = firmware['version']
    model_checksums = checksums.setdefault(model['codename'], {})
    if version in model_checksums and backup_is_complete(model, firmware):
        return False

    backup_folder = get_backup_folder(model, version)
    os.makedirs(backup_folder, exist_ok=True)

    print(Fore.YELLOW + Style.BRIGHT + f"\n{label} {version} for {model['name']}; updating {BACKUP_DIR} and {CHECKSUM_FILE}...")
    left_path = os.path.join(backup_folder, get_package_file_name(firmware, 'left'))
    right_path = os.path.join(backup_folder, get_package_file_name(firmware, 'right'))
    left_downloaded = download_file(construct_download_url(model, firmware, 'left'), left_path, f"{version} left")
    right_downloaded = download_file(construct_download_url(model, firmware, 'right'), right_path, f"{version} right")

    if not (left_downloaded and right_downloaded):
        print(Fore.RED + Style.BRIGHT + f"Could not register version {version}; skipping it.")
        return False

    left_sha256 = calculate_sha256(left_path)
    right_sha256 = calculate_sha256(right_path)
    write_backup_checksums(backup_folder, left_sha256, right_sha256)
    model_checksums[version] = {"left": left_sha256, "right": right_sha256}
    save_checksums(CHECKSUM_FILE, checksums)
    print(Fore.GREEN + Style.BRIGHT + f"Registered version {version} with checksums.")
    return True

def register_new_versions(model, firmwares, checksums):
    """Back up every FOTA version that is not yet fully recorded."""
    return sum(1 for version in sort_versions(firmwares)
               if register_version(model, firmwares[version], checksums))

def build_manual_firmware(firmwares, version):
    """Build package metadata for a manually entered full version string."""
    template = next(iter(firmwares.values()), None)
    if template is None or not VERSION_PATTERN.search(template['package']):
        return None

    package = VERSION_PATTERN.sub(version, template['package'], count=1)
    return {
        'version': version,
        'package': package,
        'folder': f"{package}{FOTA_SUFFIX}",
        'source_version': 'manual'
    }

def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Download and verify earphone firmware packages.")
    parser.add_argument('--discover', dest='discover', action='store_true',
                        help="back up newly discovered versions and update checksums.json (default)")
    parser.add_argument('--no-discover', '-n', dest='discover', action='store_false', default=True,
                        help="skip automatic backup and checksum discovery")
    return parser.parse_args()

def main(discover=True):
    """Main function to run the program."""
    print_ascii_welcome()
    checksums = load_checksums(CHECKSUM_FILE)
    if not discover:
        print(Fore.YELLOW + Style.BRIGHT + "Automatic version discovery is disabled.")

    while True:
        try:
            print(Fore.YELLOW + Style.BRIGHT + "Available Earphone Models:")
            for number, model in enumerate(EARPHONE_MODELS, start=1):
                print(Fore.GREEN + Style.BRIGHT + f"{number}. {model['name']} [{model['codename']}]")
            
            model_input = input(Fore.YELLOW + Style.BRIGHT + "\nEnter the number of the earphone model: ").strip()

            if not model_input.isdigit() or not 1 <= int(model_input) <= len(EARPHONE_MODELS):
                print(Fore.RED + Style.BRIGHT + "Invalid model number provided. Please try again.")
                continue

            model = EARPHONE_MODELS[int(model_input) - 1]
            print(Fore.CYAN + Style.BRIGHT + f"Checking available versions for {model['name']}...")
            try:
                firmwares = fetch_firmware_versions(model)
            except Exception as e:
                print(Fore.RED + Style.BRIGHT + f"Could not check firmware versions from {get_fota_url(model)}: {e}")
                continue

            if discover:
                try:
                    registered = register_new_versions(model, firmwares, checksums)
                    if registered:
                        print(Fore.GREEN + Style.BRIGHT + f"Added {registered} new version(s) to {BACKUP_DIR} and {CHECKSUM_FILE}.")
                except Exception as e:
                    print(Fore.RED + Style.BRIGHT + f"Could not update backups for {model['name']}: {e}")

            latest_version = sort_versions(firmwares)[0]
            version_input = input(Fore.YELLOW + Style.BRIGHT + f"Enter the firmware version for {model['name']} (e.g., {latest_version}), an older full version string, or press Enter to display available versions: ").strip()

            if not version_input:
                print(Fore.YELLOW + Style.BRIGHT + f"\nAvailable Versions for {model['name']}:")
                for version in sort_versions(firmwares):
                    print(Fore.GREEN + Style.BRIGHT + f"Version {version} is available.")
                version_input = input(Fore.YELLOW + Style.BRIGHT + f"\nPlease enter the firmware version you want to download for {model['name']}: ").strip()

            entered_version = version_input
            if VERSION_PATTERN.fullmatch(entered_version) is None and entered_version.isdigit():
                entered_version = f"{model['legacy_version_prefix']}.{entered_version}"

            version = resolve_version_input(entered_version, firmwares)
            if version is None:
                manual_firmware = build_manual_firmware(firmwares, entered_version)
                if manual_firmware is None:
                    print(Fore.RED + Style.BRIGHT + "Invalid version provided. Please try again.")
                    continue
                version = manual_firmware['version']
                firmwares[version] = manual_firmware
                print(Fore.YELLOW + Style.BRIGHT + f"Version {version} is not listed by FOTA; treating it as a manual entry.")

            firmware = firmwares[version]
            if discover:
                try:
                    register_version(model, firmware, checksums,
                                     "Manually entered version" if firmware['source_version'] == 'manual' else "New version")
                except Exception as e:
                    print(Fore.RED + Style.BRIGHT + f"Could not update backups for version {version}: {e}")

            version_folder = get_version_folder(model, version)
            left_file_path = construct_file_path(model, firmware, "left")
            right_file_path = construct_file_path(model, firmware, "right")

            if os.path.exists(version_folder) and os.path.isfile(left_file_path) and os.path.isfile(right_file_path):
                print(Fore.GREEN + Style.BRIGHT + f"Version {version} for {model['name']} is already downloaded.")
                verify_files(model['codename'], version, left_file_path, right_file_path, checksums)
                break

            backup_paths = get_existing_backup_paths(model, firmware)
            if backup_paths:
                os.makedirs(version_folder, exist_ok=True)
                shutil.copy2(backup_paths[0], left_file_path)
                shutil.copy2(backup_paths[1], right_file_path)
                print(Fore.GREEN + Style.BRIGHT + f"Version {version} for {model['name']} is already backed up; copied files from {BACKUP_DIR}.")
                verify_files(model['codename'], version, left_file_path, right_file_path, checksums)
                break

            os.makedirs(version_folder, exist_ok=True)

            left_file_url = construct_download_url(model, firmware, "left")
            right_file_url = construct_download_url(model, firmware, "right")

            print(Fore.CYAN + Style.BRIGHT + f"Downloading files for {model['name']} version {version}...")
            left_downloaded = download_file(left_file_url, left_file_path, version)
            right_downloaded = download_file(right_file_url, right_file_path, version)

            if left_downloaded and right_downloaded:
                print(Fore.GREEN + Style.BRIGHT + "\nFiles downloaded successfully!")
                verify_files(model['codename'], version, left_file_path, right_file_path, checksums)
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
    main(parse_args().discover)
