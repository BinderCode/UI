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

"""Standalone app launcher that can be used without android_world env object.

This module provides a standalone function to launch Android apps using
android_world's app-to-activity mapping, but without requiring the full
android_world environment object. This makes it easy for external projects
like AutoGLM to use android_world's precise app launching capabilities.
"""

import subprocess
import time
import logging

from android_world.env.adb_utils import get_adb_activity

# Set up logging
logger = logging.getLogger(__name__)


def launch_app_standalone(
    app_name: str,
    device_id: str | None = None,
    timeout_sec: float = 60.0,
    delay_after_launch: float = 1.0,
) -> bool:
  """Launch an app using android_world's mapping, but without requiring env object.
  
  This function can be used by external projects like AutoGLM to leverage
  android_world's precise app-to-activity mapping for reliable app launching.
  
  The function:
  1. Uses android_world's _PATTERN_TO_ACTIVITY mapping to find the exact activity
  2. Executes `am start -n package/activity` command directly via subprocess
  3. Falls back to monkey command if activity mapping is not found
  
  Args:
    app_name: App name (will be converted to lowercase for matching against
      android_world's pattern mapping). Examples: "Contacts", "Chrome", "Gmail".
    device_id: Optional ADB device ID for multi-device setups.
    timeout_sec: Command timeout in seconds.
    delay_after_launch: Delay in seconds after launching (to allow app to start).
  
  Returns:
    True if app was launched successfully, False otherwise.
  
  Example:
    >>> launch_app_standalone("Contacts", device_id="emulator-5554")
    True
  """
  # Convert app name to lowercase for matching (android_world uses lowercase)
  app_name_lower = app_name.lower()
  
  # 1. Try to find Activity using android_world's mapping
  activity = get_adb_activity(app_name_lower)
  
  # 2. Build ADB command prefix
  adb_cmd = ["adb"]
  if device_id:
    adb_cmd.extend(["-s", device_id])
  
  # 3. If Activity found, use am start (precise method)
  if activity:
    try:
      cmd = adb_cmd + ["shell", "am", "start", "-n", activity]
      logger.info(f"Launching {app_name} using am start: {activity}")
      
      result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
      )
      
      # Check if successful (returncode 0 and no error in stderr)
      if result.returncode == 0 and "Error" not in result.stderr:
        time.sleep(delay_after_launch)
        logger.info(f"Successfully launched {app_name}")
        return True
      else:
        logger.warning(
          f"am start failed for {app_name}: returncode={result.returncode}, "
          f"stderr={result.stderr}"
        )
    except subprocess.TimeoutExpired:
      logger.error(f"am start timed out for {app_name} after {timeout_sec}s")
    except Exception as e:
      logger.warning(f"am start exception for {app_name}: {e}")
  
  # 4. Fallback: Activity not found or am start failed
  # Note: We don't have package name mapping here, so we can't use monkey
  # The caller should handle the fallback to monkey if needed
  logger.warning(
    f"Could not launch {app_name} using android_world mapping. "
    f"Activity mapping not found or am start failed."
  )
  return False
