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

"""Runs a single task.

The minimal_run.py module is used to run a single task, it is a minimal version
of the run.py module. A task can be specified, otherwise a random task is
selected.
"""

from collections.abc import Sequence
import os
import random
import subprocess
import time
from typing import Type

from absl import app
from absl import flags
from absl import logging
from android_world import registry
from android_world.agents import infer
from android_world.agents import t3a
from android_world.env import env_launcher
from android_world.task_evals import task_eval

# AutoGLM support
try:
    from android_world.agents import autoglm_agent
    from phone_agent.model import ModelConfig
    from phone_agent.agent import AgentConfig
    AUTOGLM_AVAILABLE = True
except ImportError:
    AUTOGLM_AVAILABLE = False

logging.set_verbosity(logging.WARNING)

os.environ['GRPC_VERBOSITY'] = 'ERROR'  # Only show errors
# Don't set GRPC_TRACE - leaving it unset disables tracing
# Setting it to 'none' causes "Unknown tracer" error


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
      os.path.join(os.environ.get('ANDROID_HOME', ''), 'platform-tools', 'adb.exe'),
      os.path.join(os.environ.get('ANDROID_SDK_ROOT', ''), 'platform-tools', 'adb.exe'),
  ]
  for path in potential_paths:
    if path and os.path.isfile(path):
      return path
  return None


def _check_adb_server(adb_path: str) -> bool:
  """Check if ADB server is running and responsive.
  
  Args:
    adb_path: Path to adb executable.
    
  Returns:
    True if ADB server is working, False otherwise.
  """
  try:
    result = subprocess.run(
        [adb_path, 'devices'],
        capture_output=True,
        text=True,
        timeout=5,
    )
    # Check if command succeeded and got valid output
    return result.returncode == 0 and 'List of devices' in result.stdout
  except Exception:
    return False


def _restart_adb_server(adb_path: str) -> bool:
  """Restart ADB server.
  
  Args:
    adb_path: Path to adb executable.
    
  Returns:
    True if restart succeeded, False otherwise.
  """
  try:
    # Kill server
    subprocess.run(
        [adb_path, 'kill-server'],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)
    
    # Start server
    result = subprocess.run(
        [adb_path, 'start-server'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    
    # Wait a bit for server to fully start
    time.sleep(2)
    
    return result.returncode == 0
  except Exception as e:
    logging.warning('Failed to restart ADB server: %s', e)
    return False


def _ensure_adb_server(adb_path: str) -> None:
  """Ensure ADB server is running and responsive.
  
  This function checks ADB server status and restarts it if necessary.
  
  Args:
    adb_path: Path to adb executable.
    
  Raises:
    RuntimeError: If ADB server cannot be started.
  """
  # Check if ADB server is working
  if _check_adb_server(adb_path):
    logging.info('ADB server is running and responsive.')
    return
  
  logging.warning('ADB server is not responding, attempting to restart...')
  
  # Try to restart ADB server
  if _restart_adb_server(adb_path):
    # Verify it's working now
    if _check_adb_server(adb_path):
      logging.info('ADB server restarted successfully.')
      return
    else:
      raise RuntimeError(
          'ADB server was restarted but is still not responding. '
          'Please check ADB installation and try again.'
      )
  else:
    raise RuntimeError(
        'Failed to restart ADB server. Please manually run: '
        f'"{adb_path}" kill-server && "{adb_path}" start-server'
    )


_ADB_PATH = flags.DEFINE_string(
    'adb_path',
    None,
    'Path to adb. Set if not installed through SDK.',
)
_EMULATOR_SETUP = flags.DEFINE_boolean(
    'perform_emulator_setup',
    False,
    'Whether to perform emulator setup. This must be done once and only once'
    ' before running Android World. After an emulator is setup, this flag'
    ' should always be False.',
)
_DEVICE_CONSOLE_PORT = flags.DEFINE_integer(
    'console_port',
    5554,
    'The console port of the running Android device. This can usually be'
    ' retrieved by looking at the output of `adb devices`. In general, the'
    ' first connected device is port 5554, the second is 5556, and'
    ' so on.',
)

_TASK = flags.DEFINE_string(
    'task',
    None,
    'A specific task to run.',
)

_USE_AUTOGLM = flags.DEFINE_boolean(
    'use_autoglm',
    False,
    'Whether to use AutoGLM agent instead of T3A.',
)


def _main() -> None:
  """Runs a single task."""
  # Resolve adb path: use provided path, or try to find it automatically
  adb_path = _ADB_PATH.value
  if not adb_path:
    adb_path = _find_adb_directory()
    if not adb_path:
      raise EnvironmentError(
          'adb not found in the common Android SDK paths. Please install Android'
          " SDK and ensure adb is in one of the expected directories. If it's"
          ' already installed, point to the installed location using --adb_path.'
      )
  
  # Ensure ADB server is running and responsive
  _ensure_adb_server(adb_path)
  
  env = env_launcher.load_and_setup_env(
      console_port=_DEVICE_CONSOLE_PORT.value,
      emulator_setup=_EMULATOR_SETUP.value,
      adb_path=adb_path,
  )
  env.reset(go_home=True)
  task_registry = registry.TaskRegistry()
  aw_registry = task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY)
  if _TASK.value:
    if _TASK.value not in aw_registry:
      raise ValueError('Task {} not found in registry.'.format(_TASK.value))
    task_type: Type[task_eval.TaskEval] = aw_registry[_TASK.value]
  else:
    task_type: Type[task_eval.TaskEval] = random.choice(
        list(aw_registry.values())
    )
  params = task_type.generate_random_params()
  task = task_type(params)
  task.initialize_task(env)
  
  print('Goal: ' + str(task.goal))
  
  # Configure API - you can modify these values directly here
  OPENAI_API_KEY = "9114ec73253f41ac8297088b76c5951f.BThWl1eJnZoF7A3k"  # API Key
  BASE_URL = "https://open.bigmodel.cn/api/paas/v4"  # Base URL (without /chat/completions, OpenAI client will add it)
  MODEL_NAME = "autoglm-phone"  # Model name
  
  # Use AutoGLM if requested and available
  if _USE_AUTOGLM.value:
    if not AUTOGLM_AVAILABLE:
      raise RuntimeError(
          'AutoGLM is not available. Please ensure AutoGLM is installed '
          'in the expected location (AutoGLM).'
      )
    
    # Create AutoGLM model and agent configs
    model_config = ModelConfig(
        base_url=BASE_URL,
        api_key=OPENAI_API_KEY,
        model_name=MODEL_NAME,
    )
    agent_config = AgentConfig(
        max_steps=int(task.complexity * 20),
        lang='en',  # Use English to match English device
    )
    
    # Create AutoGLM agent
    agent = autoglm_agent.AutoGLMAgent(
        env,
        model_config=model_config,
        agent_config=agent_config,
    )
    
    # Let AutoGLM run the entire task
    print('Running task with AutoGLM...')
    result_message = agent.run_task(task.goal)
    print(f'AutoGLM completed: {result_message}')
    
    # Refresh state before validation
    env.get_state()
    
    # Validate task
    agent_successful = task.is_successful(env) == 1
    
  else:
    # Use T3A agent (original behavior)
    agent = t3a.T3A(
        env, 
        infer.Gpt4Wrapper(
            MODEL_NAME,
            api_key=OPENAI_API_KEY,
            base_url=BASE_URL
        )
    )
    
    is_done = False
    for _ in range(int(task.complexity * 10)):
      response = agent.step(task.goal)
      if response.done:
        is_done = True
        break
    agent_successful = is_done and task.is_successful(env) == 1
  
  print(
      f'{"Task Successful ✅" if agent_successful else "Task Failed ❌"};'
      f' {task.goal}'
  )
  env.close()


def main(argv: Sequence[str]) -> None:
  del argv
  _main()


if __name__ == '__main__':
  app.run(main)
