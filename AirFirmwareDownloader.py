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
CHANGELOG_FILE = 'changelogs.txt'
PACKAGE_SUFFIXES = {
    'left': '_left_FotaPackage.bin',
    'right': '_right_FotaPackage.bin',
    'filesystem': '_FileSystemPackage.bin',
    'fota': '_fota.bin'
}

EARPHONE_MODELS = [
    {
        "name": "AirPods Pro 3 V7 TB",
        "platform": "AB157x",
        "codename": "S865",
        "model": "cc ultimate",
        "chipset": "AB1571AM",
        "legacy_version_prefix": "V310.1.865"
    },
    {
        "name": "AirPods Pro 3 V6 TB",
        "codename": "S82",
        "model": "cc pro",
        "chipset": "AB1563E",
        "legacy_version_prefix": "V31.6.82"
    },
    {
        "name": "AirPods Pro 2 V5.3 TB",
        "platform": "AB157x",
        "codename": "S705",
        "model": "cc ultimate",
        "chipset": "AB1571AM",
        "legacy_version_prefix": "V310.1.705"
    },
    {
        "name": "AirPods Pro 2 V5.3 Lite",
        "codename": "S523",
        "model": "cc pro",
        "chipset": "AB1563E",
        "legacy_version_prefix": "V310.6.523"
    },
    {
        "name": "AirPods Pro 2 V5.2 TB",
        "codename": "S505",
        "model": "cc ultra",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V310.6.505"
    },
    {
        "name": "AirPods Pro V4.9 TB",
        "codename": "S305",
        "model": "cc ultra",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V31.6.305"
    },
    {
        "name": "AirPods Pro 2 V4.6 TB",
        "codename": "S52",
        "model": "cc pro",
        "chipset": "AB1562F",
        "legacy_version_prefix": "V130.6.52"
    },
    {
        "name": "AirPods Pro V4.5",
        "codename": "S30",
        "model": "cc pro",
        "chipset": "AB1562A",
        "legacy_version_prefix": "V130.6.30"
    },
    {
        "name": "AirPods 4 V3 TB",
        "platform": "AB157x",
        "codename": "S605",
        "model": "cc ultimate",
        "chipset": "AB1571AM",
        "legacy_version_prefix": "V310.1.605"
    },
    {
        "name": "AirPods 3 V1E",
        "codename": "S45",
        "model": "cc ultra",
        "chipset": "AB1562E",
        "legacy_version_prefix": "V31.6.45"
    },
    {
        "name": "AirPods 2 V3E",
        "codename": "S235",
        "model": "cc ultra",
        "chipset": "AB1562E",
        "legacy_version_prefix": "V31.6.235"
    },
    {
        "name": "AirPods 2 V3M/V3U/V3F",
        "codename": "S40",
        "model": "cc pro",
        "chipset": "AB1562M",
        "legacy_version_prefix": "V130.2.40"
    },
    {
        "name": "AirPods 2 V3M/V3U/V3F",
        "codename": "S23",
        "model": "cc pro",
        "chipset": "AB1562M",
        "legacy_version_prefix": "V130.1.23"
    },
    {
        "name": "AirPods Max V3 Metal (Max V3 TB)",
        "codename": "H25",
        "model": "cc ultra max",
        "chipset": "AB1562AE",
        "legacy_version_prefix": "V310.6.25"
    },
    {
        "codename": "S235",
        "model": "Gbuds 2",
        "chipset": "AB1562E",
        "legacy_version_prefix": "V31.6.235"
    },
    {
        "codename": "S52",
        "model": "GreenBuds",
        "chipset": "AB1562F",
        "legacy_version_prefix": "V130.6.52"
    }
]

MODEL_GROUPS = ('AirPods', 'AirPods Pro', 'AirPods Max', 'Miscellaneous')

def print_ascii_welcome():
    """Print ASCII art and a welcome message."""
    print(Fore.CYAN + ASCII_ART)
    print(Fore.BLUE + Style.BRIGHT + "=" * 50)

def get_model_codename(model):
    """Combine the short codename and model name (e.g. S523_cc pro)."""
    return f"{model['codename']}_{model['model']}"

def get_model_name(model):
    """Return the display name, falling back to codename and chipset."""
    if model.get('name'):
        return model['name']
    return f"{model['codename']} - {model['chipset']}"

def format_model_menu_entry(model):
    """Format one model for the main menu."""
    return f"{get_model_name(model)} [{get_model_codename(model)}]"

def get_model_group(model):
    """Return the main-menu section for a model."""
    name = model.get('name', '')
    if name.startswith('AirPods Pro'):
        return 'AirPods Pro'
    if name.startswith('AirPods Max'):
        return 'AirPods Max'
    if name.startswith('AirPods'):
        return 'AirPods'
    return 'Miscellaneous'

def get_menu_models():
    """Return models ordered by menu section, preserving order within each section."""
    return [model
            for group in MODEL_GROUPS
            for model in EARPHONE_MODELS
            if get_model_group(model) == group]

def uses_fota_package(model):
    """Return True for platform models that ship one combined fota package."""
    return bool(model.get('platform'))

def get_package_sides(model):
    """Return the package sides used by a model."""
    return ('fota',) if uses_fota_package(model) else ('left', 'right')

def get_all_package_sides(model):
    """Return every package type that may be stored for a model."""
    sides = list(get_package_sides(model))
    if not uses_fota_package(model):
        sides.append('filesystem')
    return tuple(sides)

def get_fota_base(model):
    """Build the model-specific FOTA URL prefix."""
    if uses_fota_package(model):
        return f"{FOTA_BASE_URL}/{model['platform']}/{model['codename']}/{model['chipset']}"
    return f"{FOTA_BASE_URL}/{model['codename']}/{model['chipset']}"

def get_fota_url(model):
    """Build the FOTA index URL for a model."""
    return f"{get_fota_base(model)}/fota"

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
        if not uses_fota_package(model) and (
            get_model_codename(model).lower() not in folder_lower
            or model['chipset'].lower() not in folder_lower
        ):
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

def get_update_info_url(model):
    """Build the update-info URL for a model."""
    return f"{get_fota_base(model)}/update_info"

def fetch_changelogs(model):
    """Read update_info and return changelogs keyed by full version string."""
    url = get_update_info_url(model)
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    changelogs = {}
    model_codename = get_model_codename(model).lower()
    chipset = model['chipset'].lower()
    for entry in root.iter('entry'):
        key = (entry.get('key') or '').strip()
        key_lower = key.lower()
        if not uses_fota_package(model) and (model_codename not in key_lower or chipset not in key_lower):
            continue

        version = extract_full_version(key)
        changelog = (entry.text or '').strip()
        if version and changelog:
            changelogs[version] = {'changelog': changelog, 'source': key}
    return changelogs

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

def remote_file_exists(url):
    """Return True when a URL points to a non-empty file."""
    try:
        response = requests.head(url, timeout=30, allow_redirects=True)
        if response.status_code == 405:
            response = requests.get(url, stream=True, timeout=30, allow_redirects=True)
            response.close()
        return response.status_code == 200 and int(response.headers.get('content-length', 1)) > 0
    except requests.RequestException:
        return False

def try_download_filesystem(model, firmware, target_folder, version):
    """Download the optional FileSystemPackage.bin into target_folder when the server has it."""
    os.makedirs(target_folder, exist_ok=True)
    file_path = os.path.join(target_folder, get_package_file_name(firmware, 'filesystem'))
    if os.path.isfile(file_path):
        return file_path

    url = construct_download_url(model, firmware, 'filesystem')
    if not remote_file_exists(url):
        return None

    if download_file(url, file_path, f"{version} filesystem"):
        return file_path
    return None

def load_checksums(json_file):
    """Load checksums from a JSON file."""
    with open(json_file, 'r') as f:
        checksums = json.load(f)

    # Older releases keyed checksums by display name, short codename, or build number only.
    for model in EARPHONE_MODELS:
        model_codename = get_model_codename(model)
        legacy_entries = checksums.pop(model['name'], {}) if model.get('name') else {}
        short_entries = checksums.pop(model['codename'], {})
        model_checksums = checksums.setdefault(model_codename, {})
        for entries in (legacy_entries, short_entries):
            for version, hashes in entries.items():
                model_checksums.setdefault(version, hashes)

        prefix = model['legacy_version_prefix']
        for key in list(model_checksums):
            if str(key).isdigit():
                model_checksums[f"{prefix}.{key}"] = model_checksums.pop(key)
    return checksums

def compare_checksums(codename, version, checksums, calculated_hashes):
    """Compare calculated checksums with expected checksums for any package sides."""
    expected = checksums.get(codename, {}).get(version, {})
    results = {}
    for side, calculated in calculated_hashes.items():
        value = expected.get(side)
        results[side] = {
            'checksum': calculated,
            'match': calculated.lower() == value.lower() if calculated and value else None
        }
    return results

def get_match_result(match):
    if match is True:
        return 'Match'
    elif match is False:
        return 'No match'
    else:
        return 'Not available in checksums.json'

def print_comparison_results(results):
    """Print comparison results in a formatted manner."""
    labels = {'fota': 'Fota', 'left': 'Left', 'right': 'Right', 'filesystem': 'Filesystem'}
    print(Fore.GREEN + Style.BRIGHT + "\nChecksum Comparison Results:")
    print(Fore.GREEN + Style.BRIGHT + "=" * 50)
    for side, result in results.items():
        label = labels.get(side, side.capitalize())
        print(Fore.GREEN + Style.BRIGHT + f"{label} file checksum:  {result['checksum']}")
        print(Fore.GREEN + Style.BRIGHT + f"{label} file match:     {get_match_result(result['match'])}")
    print(Fore.GREEN + Style.BRIGHT + "=" * 50)

def verify_files(codename, version, paths, checksums):
    """Hash the packages and print the checksum comparison."""
    calculated = {
        side: calculate_sha256(path)
        for side, path in paths.items()
        if path and os.path.isfile(path)
    }
    print_comparison_results(compare_checksums(codename, version, checksums, calculated))

def get_version_folder(model, version):
    """Get the local download folder for a full version string."""
    return os.path.join(os.getcwd(), version)

def sanitize_filename(name):
    """Decode URL escapes and replace characters unsafe in local filenames."""
    return INVALID_FILENAME_CHARS.sub('_', unquote(name)).strip(' .')

def get_package_file_name(firmware, side):
    """Build the local file name for a firmware package."""
    return f"{sanitize_filename(firmware['package'])}{PACKAGE_SUFFIXES[side]}"

def construct_file_path(model, firmware, side):
    """Construct the local file path for a firmware package."""
    return os.path.join(get_version_folder(model, firmware['version']), get_package_file_name(firmware, side))

def construct_download_url(model, firmware, side):
    """Construct the download URL for a firmware package."""
    folder = quote(firmware['folder'], safe='')
    file_name = f"{quote(firmware['package'], safe='')}{PACKAGE_SUFFIXES[side]}"
    return f"{get_fota_base(model)}/{folder}/{file_name}"

def get_backup_folder(model, version):
    """Get the backup folder for a full version string."""
    return os.path.join(os.getcwd(), BACKUP_DIR, model['codename'], model['model'], version)

def get_changelog_entry(changelogs, firmware):
    """Return the changelog entry for a firmware version when available."""
    if not changelogs:
        return None
    return changelogs.get(firmware['version'])

def format_changelog(changelog_entry):
    """Return a changelog as plain text with real line breaks."""
    return (changelog_entry.get('changelog') or '').replace('\\n', '\n').strip()

def print_changelog(version, changelog_entry):
    """Show a build's changelog when one is available."""
    if not changelog_entry:
        return
    print(Fore.CYAN + Style.BRIGHT + f"\nChangelog for {version}:")
    print(Fore.CYAN + format_changelog(changelog_entry))

def write_changelog_file(folder, version, changelog_entry):
    """Write a firmware folder's changelog file, returning True when it changed."""
    path = os.path.join(folder, CHANGELOG_FILE)
    text = format_changelog(changelog_entry)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                if f.read().strip() == text:
                    return False
        except OSError:
            pass

    os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"{text}\n")
    return True

def save_checksums(json_file, checksums):
    """Write checksums back to disk with versions sorted by name."""
    codename_order = {get_model_codename(model): index for index, model in enumerate(EARPHONE_MODELS)}
    ordered = {
        codename: {version: checksums[codename][version] for version in sorted(checksums[codename], key=version_sort_key)}
        for codename in sorted(checksums, key=lambda name: (codename_order.get(name, len(codename_order)), name))
    }
    with open(json_file, 'w') as f:
        json.dump(ordered, f, indent=4)
        f.write('\n')

def write_backup_checksums(backup_folder, hashes):
    """Write the checksum file that accompanies backed-up firmware packages."""
    with open(os.path.join(backup_folder, BACKUP_CHECKSUM_FILE), 'w') as f:
        for side in ('left', 'right', 'filesystem', 'fota'):
            value = hashes.get(side)
            if value:
                f.write(f"{side} - {value}\n")

def get_legacy_package_file_name(firmware, side):
    """Build the file name used by older backups, which kept the URL escaping."""
    return f"{quote(firmware['package'], safe='')}{PACKAGE_SUFFIXES[side]}"

def find_backup_file(backup_folder, firmware, side):
    """Find a backed-up package by side, allowing legacy escaped names."""
    for name in (get_package_file_name(firmware, side), get_legacy_package_file_name(firmware, side)):
        path = os.path.join(backup_folder, name)
        if os.path.isfile(path):
            return path
    return None

def get_existing_backup_paths(model, firmware, sides=None):
    """Return available backed-up package paths for a version, keyed by side."""
    backup_folder = get_backup_folder(model, firmware['version'])
    if sides is None:
        sides = get_all_package_sides(model)
    return {side: find_backup_file(backup_folder, firmware, side) for side in sides}

def backup_is_complete(model, firmware):
    """Check whether a version already has backed-up packages and a checksum file."""
    backup_folder = get_backup_folder(model, firmware['version'])
    if not os.path.isfile(os.path.join(backup_folder, BACKUP_CHECKSUM_FILE)):
        return False

    paths = get_existing_backup_paths(model, firmware, get_package_sides(model))
    return all(paths.values())

def copy_filesystem_from_backup(model, firmware, backup_paths, version_folder):
    """Place a backed-up filesystem package in the local version folder when present."""
    local_path = construct_file_path(model, firmware, 'filesystem')
    if os.path.isfile(local_path):
        return local_path

    backup_path = backup_paths.get('filesystem')
    if not backup_path:
        return None

    os.makedirs(version_folder, exist_ok=True)
    shutil.copy2(backup_path, local_path)
    print(Fore.GREEN + Style.BRIGHT + "Copied FileSystemPackage from backup.")
    return local_path

def register_version(model, firmware, checksums, label="New version", changelogs=None):
    """Back up a version and record its checksums, changelog, and optional extras."""
    version = firmware['version']
    model_checksums = checksums.setdefault(get_model_codename(model), {})
    stored_hashes = model_checksums.get(version)
    package_sides = get_package_sides(model)
    backup_folder = get_backup_folder(model, version)
    os.makedirs(backup_folder, exist_ok=True)

    backup_paths = get_existing_backup_paths(model, firmware, get_all_package_sides(model))
    have_backup = (all(backup_paths[side] for side in package_sides)
                   and os.path.isfile(os.path.join(backup_folder, BACKUP_CHECKSUM_FILE)))
    hashes = dict(stored_hashes) if stored_hashes else {}
    changed = False

    if have_backup:
        if not stored_hashes:
            for side in package_sides:
                hashes[side] = calculate_sha256(backup_paths[side])
            changed = True
    else:
        print(Fore.YELLOW + Style.BRIGHT + f"\n{label} {version} for {get_model_name(model)}; updating {BACKUP_DIR} and {CHECKSUM_FILE}...")
        downloaded = {}
        for side in package_sides:
            path = os.path.join(backup_folder, get_package_file_name(firmware, side))
            downloaded[side] = download_file(construct_download_url(model, firmware, side), path, f"{version} {side}")
        if not all(downloaded.values()):
            print(Fore.RED + Style.BRIGHT + f"Could not register version {version}; skipping it.")
            return False
        for side in package_sides:
            path = os.path.join(backup_folder, get_package_file_name(firmware, side))
            hashes[side] = calculate_sha256(path)
        changed = True

    if not uses_fota_package(model):
        filesystem_path = backup_paths.get('filesystem')
        if not filesystem_path:
            filesystem_path = try_download_filesystem(model, firmware, backup_folder, version)
        if filesystem_path:
            filesystem_hash = calculate_sha256(filesystem_path)
            if hashes.get('filesystem') != filesystem_hash:
                hashes['filesystem'] = filesystem_hash
                changed = True

    changelog_entry = get_changelog_entry(changelogs, firmware)
    changelog_saved = bool(changelog_entry and write_changelog_file(backup_folder, version, changelog_entry))
    if changelog_saved:
        changed = True
        print(Fore.GREEN + Style.BRIGHT + f"Saved changelog for {version} to {BACKUP_DIR}.")

    if changed or not stored_hashes:
        write_backup_checksums(backup_folder, hashes)
        model_checksums[version] = hashes
        save_checksums(CHECKSUM_FILE, checksums)
        if stored_hashes and hashes.get('filesystem') and not stored_hashes.get('filesystem'):
            print(Fore.GREEN + Style.BRIGHT + f"Added FileSystemPackage to {BACKUP_DIR} and recorded its checksum for {version}.")
        elif stored_hashes:
            print(Fore.GREEN + Style.BRIGHT + f"Updated backup data for version {version}.")
        else:
            print(Fore.GREEN + Style.BRIGHT + f"Registered version {version} with checksums.")
    return changed

def register_new_versions(model, firmwares, checksums, changelogs=None):
    """Back up every FOTA version that is not yet fully recorded."""
    return sum(1 for version in sort_versions(firmwares)
               if register_version(model, firmwares[version], checksums, changelogs=changelogs))

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

    menu_models = get_menu_models()
    grouped_models = {
        group: [model for model in menu_models if get_model_group(model) == group]
        for group in MODEL_GROUPS
    }

    while True:
        try:
            print(Fore.YELLOW + Style.BRIGHT + "Available Earphone Models:")
            number = 1
            for group in MODEL_GROUPS:
                if not grouped_models[group]:
                    continue
                print(Fore.CYAN + Style.BRIGHT + f"\n{group}:")
                for model in grouped_models[group]:
                    print(Fore.GREEN + Style.BRIGHT + f"{number}. {format_model_menu_entry(model)}")
                    number += 1
            
            model_input = input(Fore.YELLOW + Style.BRIGHT + "\nEnter the number of the earphone model: ").strip()

            if not model_input.isdigit() or not 1 <= int(model_input) <= len(menu_models):
                print(Fore.RED + Style.BRIGHT + "Invalid model number provided. Please try again.")
                continue

            model = menu_models[int(model_input) - 1]
            print(Fore.CYAN + Style.BRIGHT + f"Checking available versions for {get_model_name(model)}...")
            try:
                firmwares = fetch_firmware_versions(model)
            except Exception as e:
                print(Fore.RED + Style.BRIGHT + f"Could not check firmware versions from {get_fota_url(model)}: {e}")
                continue

            try:
                changelogs = fetch_changelogs(model)
            except Exception as e:
                changelogs = {}
                print(Fore.YELLOW + Style.BRIGHT + f"Could not check changelogs from {get_update_info_url(model)}: {e}")

            if discover:
                try:
                    registered = register_new_versions(model, firmwares, checksums, changelogs)
                    if registered:
                        print(Fore.GREEN + Style.BRIGHT + f"Added/updated {registered} version(s) in {BACKUP_DIR} and {CHECKSUM_FILE}.")
                except Exception as e:
                    print(Fore.RED + Style.BRIGHT + f"Could not update backups for {get_model_name(model)}: {e}")

            latest_version = sort_versions(firmwares)[0]
            version_input = input(Fore.YELLOW + Style.BRIGHT + f"Enter the firmware version for {get_model_name(model)} (e.g., {latest_version}), an older full version string, or press Enter to display available versions: ").strip()

            if not version_input:
                print(Fore.YELLOW + Style.BRIGHT + f"\nAvailable Versions for {get_model_name(model)}:")
                for version in sort_versions(firmwares):
                    print(Fore.GREEN + Style.BRIGHT + f"Version {version} is available.")
                version_input = input(Fore.YELLOW + Style.BRIGHT + f"\nPlease enter the firmware version you want to download for {get_model_name(model)}: ").strip()

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
            changelog_entry = get_changelog_entry(changelogs, firmware)
            if discover:
                try:
                    register_version(model, firmware, checksums,
                                     "Manually entered version" if firmware['source_version'] == 'manual' else "New version",
                                     changelogs=changelogs)
                except Exception as e:
                    print(Fore.RED + Style.BRIGHT + f"Could not update backups for version {version}: {e}")

            version_folder = get_version_folder(model, version)
            package_sides = get_package_sides(model)
            local_paths = {side: construct_file_path(model, firmware, side) for side in package_sides}
            backup_paths = get_existing_backup_paths(model, firmware)
            verify_paths = dict(local_paths)

            local_files_exist = (os.path.isdir(version_folder)
                                 and all(os.path.isfile(path) for path in local_paths.values()))
            if local_files_exist:
                if not uses_fota_package(model):
                    verify_paths['filesystem'] = copy_filesystem_from_backup(model, firmware, backup_paths, version_folder)
                print_changelog(version, changelog_entry)
                print(Fore.GREEN + Style.BRIGHT + f"Version {version} for {get_model_name(model)} is already downloaded.")
                verify_files(get_model_codename(model), version, verify_paths, checksums)
                break

            if backup_is_complete(model, firmware):
                os.makedirs(version_folder, exist_ok=True)
                for side in package_sides:
                    shutil.copy2(backup_paths[side], local_paths[side])
                if not uses_fota_package(model):
                    verify_paths['filesystem'] = copy_filesystem_from_backup(model, firmware, backup_paths, version_folder)
                print_changelog(version, changelog_entry)
                print(Fore.GREEN + Style.BRIGHT + f"Version {version} for {get_model_name(model)} is already backed up; copied files from {BACKUP_DIR}.")
                verify_files(get_model_codename(model), version, verify_paths, checksums)
                break

            os.makedirs(version_folder, exist_ok=True)
            if not uses_fota_package(model):
                verify_paths['filesystem'] = copy_filesystem_from_backup(model, firmware, backup_paths, version_folder)

            print(Fore.CYAN + Style.BRIGHT + f"Downloading files for {get_model_name(model)} version {version}...")
            downloaded = {
                side: download_file(construct_download_url(model, firmware, side), local_paths[side], f"{version} {side}")
                for side in package_sides
            }

            if all(downloaded.values()):
                if (not uses_fota_package(model) and not verify_paths.get('filesystem') and not discover):
                    verify_paths['filesystem'] = try_download_filesystem(model, firmware, version_folder, version)
                print_changelog(version, changelog_entry)
                print(Fore.GREEN + Style.BRIGHT + "\nFiles downloaded successfully!")
                verify_files(get_model_codename(model), version, verify_paths, checksums)
            else:
                print(Fore.RED + Style.BRIGHT + "Failed to download one or more files.")
            
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
