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

"""Run AutoGLM agent on AndroidWorld tasks with task list management and checkpoint support."""

import os
import sys
from pathlib import Path

# 脚本在 task_collection/ 下，将项目根与 AutoGLM 加入 path 以便 import android_world、phone_agent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AUTOGLM = _PROJECT_ROOT / "AutoGLM"
for _p in (_PROJECT_ROOT, _AUTOGLM):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import csv
import dataclasses
import json
import subprocess
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from absl import app
from absl import flags
from absl import logging

from android_world import registry
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
_EMULATOR_SETUP = flags.DEFINE_boolean(
    'perform_emulator_setup',
    False,
    'Whether to perform emulator setup.',
)
_DEVICE_CONSOLE_PORT = flags.DEFINE_integer(
    'console_port',
    5554,
    'The console port of the running Android device.',
)
_OUTPUT_DIR = flags.DEFINE_string(
    'output_dir',
    os.path.join(os.getcwd(), 'autoglm_runs'),
    'Directory to save task execution data and task list.',
)
_TASKS = flags.DEFINE_list(
    'tasks',
    None,
    'List of specific tasks to run. If None, run all tasks in android_world family.',
)
_N_TASK_COMBINATIONS = flags.DEFINE_integer(
    'n_task_combinations',
    1,
    'Number of task instances to run for each task template.',
)
_TASK_RANDOM_SEED = flags.DEFINE_integer(
    'task_random_seed',
    30,
    'Random seed for task randomness.',
)

# API configuration（未传时从工程目录 project_config.json 读取）
_OPENAI_API_KEY = flags.DEFINE_string(
    'api_key',
    '',
    'OpenAI API key.',
)
_BASE_URL = flags.DEFINE_string(
    'base_url',
    '',
    'Base URL for API (without /chat/completions).',
)
_MODEL_NAME = flags.DEFINE_string(
    'model_name',
    "autoglm-phone",
    'Model name.',
)
_CREATE_LIST_ONLY = flags.DEFINE_boolean(
    'create_list_only',
    False,
    'If True, only create or load task_list.csv and exit without running tasks.',
)


# Task status constants
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def load_task_list(task_list_path: str) -> list[dict[str, Any]]:
    """Load task list from CSV file."""
    if not os.path.exists(task_list_path):
        return []

    tasks = []
    with open(task_list_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append(row)
    return tasks


def save_task_list(task_list_path: str, tasks: list[dict[str, Any]]) -> None:
    """Save task list to CSV file."""
    if not tasks:
        return

    os.makedirs(os.path.dirname(task_list_path), exist_ok=True)
    fieldnames = tasks[0].keys()

    with open(task_list_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tasks)


def convert_to_json_serializable(obj: Any) -> Any:
    """Recursively convert dataclass objects and other non-serializable types to JSON-serializable format.
    
    This function handles:
    - dataclass objects: converts to dict using dataclasses.asdict()
    - lists: recursively processes each element
    - dicts: recursively processes each value
    - tuples: converts to lists
    - other types: returns as-is if JSON serializable
    
    Args:
        obj: Object to convert (can be any type).
        
    Returns:
        JSON-serializable version of the object.
    """
    # Handle dataclass objects
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # Convert dataclass to dict and recursively process
        return convert_to_json_serializable(dataclasses.asdict(obj))
    
    # Handle lists
    if isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    
    # Handle tuples (convert to list)
    if isinstance(obj, tuple):
        return [convert_to_json_serializable(item) for item in obj]
    
    # Handle dictionaries
    if isinstance(obj, dict):
        return {key: convert_to_json_serializable(value) for key, value in obj.items()}
    
    # Handle other types that are already JSON serializable
    # (str, int, float, bool, None, etc.)
    try:
        # Test if it's JSON serializable by attempting to encode
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        # If not serializable, try to convert to string representation
        # This handles edge cases like custom objects
        return str(obj)


def create_task_list(
    task_registry: registry.TaskRegistry,
    output_dir: str,
    tasks: list[str] | None = None,
    n_task_combinations: int = 1,
    seed: int = 30,
) -> list[dict[str, Any]]:
    """Create initial task list from registry."""
    import random
    random.seed(seed)

    aw_registry = task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY)

    task_list = []
    task_id = 1

    # Get tasks to run
    if tasks:
        task_names = [t for t in tasks if t in aw_registry]
    else:
        task_names = sorted(aw_registry.keys())

    # Create task instances
    for task_name in task_names:
        task_type = aw_registry[task_name]
        for instance_idx in range(n_task_combinations):
            # Generate task parameters
            params = task_type.generate_random_params()
            task_instance = task_type(params)

            task_id_str = f"task_{task_id:04d}"
            # 存相对路径（相对 output_dir），便于拷贝工程目录
            task_data_dir_rel = task_id_str
            task_params_path_rel = task_id_str + os.path.sep + 'task_params.json'
            task_data_dir_abs = os.path.join(output_dir, task_id_str)
            task_params_path_abs = os.path.join(task_data_dir_abs, 'task_params.json')
            os.makedirs(task_data_dir_abs, exist_ok=True)
            with open(task_params_path_abs, 'w', encoding='utf-8') as f:
                serializable_params = convert_to_json_serializable(params)
                json.dump(serializable_params, f, ensure_ascii=False, indent=2)

            task_entry = {
                'task_id': task_id_str,
                'task_name': task_name,
                'instance_id': str(instance_idx),
                'task_goal': task_instance.goal,
                'difficulty': str(getattr(task_instance, 'complexity', 1.0)),
                'task_data_dir': task_data_dir_rel,
                'task_params_path': task_params_path_rel,
                'status': STATUS_PENDING,
                'result': '',
                'start_time': '',
                'end_time': '',
                'duration_seconds': '',
                'error_message': '',
            }
            task_list.append(task_entry)
            task_id += 1

    return task_list


def update_task_status(
    task_list: list[dict[str, Any]],
    task_id: str,
    status: str,
    result: str = '',
    error_message: str = '',
) -> None:
    """Update task status in task list."""
    for task in task_list:
        if task['task_id'] == task_id:
            task['status'] = status
            task['result'] = result
            task['error_message'] = error_message
            if status == STATUS_RUNNING and not task['start_time']:
                task['start_time'] = datetime.now().isoformat()
            elif status in [STATUS_SUCCESS, STATUS_FAILED] and not task['end_time']:
                task['end_time'] = datetime.now().isoformat()
                if task['start_time']:
                    start = datetime.fromisoformat(task['start_time'])
                    end = datetime.fromisoformat(task['end_time'])
                    task['duration_seconds'] = str((end - start).total_seconds())
            break


def _resolve_path(base_dir: str, path: str) -> str:
    """若 path 非绝对路径，则解析为 base_dir 下的相对路径；否则原样返回。"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def run_single_task(
    task_entry: dict[str, Any],
    env: Any,
    model_config: ModelConfig,
    base_agent_config: AgentConfig,
    output_dir: str,
) -> tuple[bool, str]:
    """Run a single task with AutoGLM.

    Returns:
        Tuple of (success: bool, message: str)
    """
    task_registry = registry.TaskRegistry()
    aw_registry = task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY)

    task_name = task_entry['task_name']
    if task_name not in aw_registry:
        return False, f"Task {task_name} not found in registry"

    # 相对路径按 output_dir 解析（CSV 中存的是相对路径）
    task_data_dir = _resolve_path(output_dir, task_entry.get('task_data_dir', ''))
    task_params_path = _resolve_path(output_dir, task_entry.get('task_params_path', ''))

    # Load task parameters if saved
    task_type = aw_registry[task_name]
    if task_params_path and os.path.exists(task_params_path):
        with open(task_params_path, 'r', encoding='utf-8') as f:
            params = json.load(f)
    else:
        # Fallback: generate new params (shouldn't happen if task list was created properly)
        params = task_type.generate_random_params()

    # Create task instance
    task = task_type(params)

    # Initialize task
    try:
        task.initialize_task(env)
    except Exception as e:
        return False, f"Failed to initialize task: {str(e)}"

    # Create agent config with task-specific settings
    agent_config = AgentConfig(
        max_steps=int(float(task_entry.get('difficulty', 1)) * 20),
        lang=base_agent_config.lang if hasattr(base_agent_config, 'lang') else 'en',
        device_id=base_agent_config.device_id if hasattr(base_agent_config, 'device_id') else None,
        task_data_dir=task_data_dir,
        task_id=task_entry['task_id'],
        verbose=base_agent_config.verbose if hasattr(base_agent_config, 'verbose') else True,
    )

    agent = autoglm_agent.AutoGLMAgent(
        env,
        model_config=model_config,
        agent_config=agent_config,
    )

    # Run task
    try:
        result_message = agent.run_task(task.goal)
        env.get_state()  # Refresh state before validation
        task_successful = task.is_successful(env) == 1

        if task_successful:
            return True, result_message or "Task completed successfully"
        else:
            return False, result_message or "Task completed but validation failed"
    except Exception as e:
        return False, f"Task execution error: {str(e)}"
    finally:
        try:
            task.tear_down(env)
        except Exception:
            pass


def _main() -> None:
    """Main function to run tasks with AutoGLM."""
    if not AUTOGLM_AVAILABLE:
        raise RuntimeError(
            'AutoGLM is not available. Please ensure AutoGLM is installed '
            'in the expected location (AutoGLM).'
        )

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

    # Create output directory
    output_dir = _OUTPUT_DIR.value
    os.makedirs(output_dir, exist_ok=True)

    # Task list file path
    task_list_path = os.path.join(output_dir, 'task_list.csv')

    # Load or create task list
    task_list = load_task_list(task_list_path)
    if not task_list:
        print("Creating new task list...")
        task_registry = registry.TaskRegistry()
        task_list = create_task_list(
            task_registry,
            output_dir,
            tasks=_TASKS.value,
            n_task_combinations=_N_TASK_COMBINATIONS.value,
            seed=_TASK_RANDOM_SEED.value,
        )
        save_task_list(task_list_path, task_list)
        print(f"Created {len(task_list)} tasks in task list.")
    else:
        print(f"Loaded {len(task_list)} tasks from existing task list.")

    if _CREATE_LIST_ONLY.value:
        print(f"Task list saved: {task_list_path}")
        return

    # Load environment（可能较慢或等待设备/模拟器）
    print("Loading Android environment...", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    env = env_launcher.load_and_setup_env(
        console_port=_DEVICE_CONSOLE_PORT.value,
        emulator_setup=_EMULATOR_SETUP.value,
        adb_path=adb_path,
    )
    env.reset(go_home=True)

    # API 配置必须由调用方传入（如 Web 从 get_config_manager().get_config() 取后传 --api_key、--base_url）
    model_config = ModelConfig(
        base_url=_BASE_URL.value,
        api_key=_OPENAI_API_KEY.value,
        model_name=_MODEL_NAME.value,
    )

    # Process tasks
    pending_tasks = [t for t in task_list if t['status'] == STATUS_PENDING]
    running_tasks = [t for t in task_list if t['status'] == STATUS_RUNNING]

    # Reset any running tasks to pending (in case of crash)
    for task in running_tasks:
        update_task_status(task_list, task['task_id'], STATUS_PENDING)
        save_task_list(task_list_path, task_list)

    print(f"\nStarting task execution. {len(pending_tasks)} tasks pending.")

    for task_entry in task_list:
        if task_entry['status'] in [STATUS_SUCCESS, STATUS_FAILED]:
            print(f"Skipping {task_entry['task_id']} ({task_entry['task_name']}) - already completed")
            continue

        task_id = task_entry['task_id']
        task_name = task_entry['task_name']
        task_goal = task_entry['task_goal']

        print(f"\n{'='*80}")
        print(f"Running {task_id}: {task_name}")
        print(f"Goal: {task_goal}")
        print(f"{'='*80}")

        # Update status to running
        update_task_status(task_list, task_id, STATUS_RUNNING)
        save_task_list(task_list_path, task_list)

        # 相对路径按 output_dir 解析
        task_data_dir = _resolve_path(output_dir, task_entry['task_data_dir'])
        os.makedirs(task_data_dir, exist_ok=True)
        os.makedirs(os.path.join(task_data_dir, 'steps'), exist_ok=True)

        # Create base agent config
        base_agent_config = AgentConfig(
            lang='en',
        )

        # Run task
        try:
            success, message = run_single_task(
                task_entry,
                env,
                model_config,
                base_agent_config,
                output_dir,
            )

            if success:
                update_task_status(task_list, task_id, STATUS_SUCCESS, result=message)
                print(f"✅ Task {task_id} completed successfully")
            else:
                update_task_status(task_list, task_id, STATUS_FAILED, result=message, error_message=message)
                print(f"❌ Task {task_id} failed: {message}")

        except Exception as e:
            error_msg = str(e)
            update_task_status(task_list, task_id, STATUS_FAILED, error_message=error_msg)
            print(f"❌ Task {task_id} crashed: {error_msg}")
            import traceback
            traceback.print_exc()

        # Save task list after each task
        save_task_list(task_list_path, task_list)

        # Reset environment for next task
        env.reset(go_home=True)

    print(f"\n{'='*80}")
    print("Task execution completed!")
    print(f"Results saved to: {output_dir}")
    print(f"Task list: {task_list_path}")
    print(f"{'='*80}")

    env.close()


def main(argv: Sequence[str]) -> None:
    del argv
    _main()


if __name__ == '__main__':
    app.run(main)
