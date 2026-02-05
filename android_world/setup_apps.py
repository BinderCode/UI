# Copyright 2024 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Setup AndroidWorld apps on emulator."""

import os
import subprocess
import time
from collections.abc import Sequence

from absl import app
from absl import flags
from absl import logging

from android_world.env import env_launcher
from android_world.env.setup_device import setup

logging.set_verbosity(logging.WARNING)

os.environ['GRPC_VERBOSITY'] = 'ERROR'


def _find_adb_directory() -> str | None:
    """Returns the directory where adb is located."""
    potential_paths = [
        # macOS paths
        os.path.expanduser('~/Library/Android/sdk/platform-tools/adb'),
        # Linux paths
        os.path.expanduser('~/Android/Sdk/platform-tools/adb'),
        # Windows paths
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
        os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'Local', 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
        os.path.join(os.environ.get('ANDROID_HOME', ''), 'platform-tools', 'adb.exe') if os.environ.get('ANDROID_HOME') else None,
        os.path.join(os.environ.get('ANDROID_SDK_ROOT', ''), 'platform-tools', 'adb.exe') if os.environ.get('ANDROID_SDK_ROOT') else None,
    ]
    for path in potential_paths:
        if path and os.path.isfile(path):
            return path
    return None


def _check_adb_server(adb_path: str) -> bool:
    """Check if ADB server is running and responsive."""
    try:
        result = subprocess.run(
            [adb_path, 'devices'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and 'List of devices' in result.stdout
    except Exception:
        return False


def _restart_adb_server(adb_path: str) -> bool:
    """Restart ADB server."""
    try:
        subprocess.run([adb_path, 'kill-server'], capture_output=True, timeout=5)
        time.sleep(1)
        result = subprocess.run(
            [adb_path, 'start-server'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(2)
        return result.returncode == 0
    except Exception as e:
        logging.warning('Failed to restart ADB server: %s', e)
        return False


def _ensure_adb_server(adb_path: str) -> None:
    """Ensure ADB server is running and responsive."""
    if _check_adb_server(adb_path):
        logging.info('ADB server is running and responsive.')
        return

    logging.warning('ADB server is not responding, attempting to restart...')
    if _restart_adb_server(adb_path):
        if _check_adb_server(adb_path):
            logging.info('ADB server restarted successfully.')
            return
        raise RuntimeError(
            'ADB server was restarted but is still not responding. '
            'Please check ADB installation and try again.'
        )
    else:
        raise RuntimeError(
            'Failed to restart ADB server. Please manually run: '
            f'"{adb_path}" kill-server && "{adb_path}" start-server'
        )


# Command line flags
_ADB_PATH = flags.DEFINE_string(
    'adb_path',
    None,
    'Path to adb. Set if not installed through SDK.',
)
_DEVICE_CONSOLE_PORT = flags.DEFINE_integer(
    'console_port',
    5554,
    'The console port of the running Android device.',
)


def _main() -> None:
    """Main function to setup apps."""
    # Resolve adb path
    adb_path = _ADB_PATH.value
    if not adb_path:
        adb_path = _find_adb_directory()
        if not adb_path:
            raise EnvironmentError(
                'adb not found in the common Android SDK paths. Please install Android'
                " SDK and ensure adb is in one of the expected directories. If it's"
                ' already installed, point to the installed location using --adb_path.'
            )

    # Ensure ADB server is running
    _ensure_adb_server(adb_path)

    print("=" * 80)
    print("AndroidWorld App Setup")
    print("=" * 80)
    print("\nThis will install and configure all required apps on the emulator.")
    print("Please do not interact with the device during installation.")
    print("\nLoading Android environment...")

    # Load environment
    env = env_launcher.load_and_setup_env(
        console_port=_DEVICE_CONSOLE_PORT.value,
        emulator_setup=False,  # We'll do setup manually
        adb_path=adb_path,
    )
    env.reset(go_home=True)

    print("Installing and setting up applications...")
    print("This may take several minutes depending on your connection speed.\n")

    try:
        # Setup apps
        setup.setup_apps(env)
        print("\n" + "=" * 80)
        print("✅ App setup completed successfully!")
        print("=" * 80)
        print("\nYou can now run tasks without --perform_emulator_setup flag.")
    except Exception as e:
        print("\n" + "=" * 80)
        print("❌ App setup failed!")
        print("=" * 80)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        env.close()


def main(argv: Sequence[str]) -> None:
    del argv
    _main()


if __name__ == '__main__':
    app.run(main)
