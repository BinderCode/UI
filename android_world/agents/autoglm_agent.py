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

"""AutoGLM Agent adapter for android_world."""

import sys
import os
from typing import Any

# Add AutoGLM path to sys.path
_AUTOGLM_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'AutoGLM'))
if os.path.exists(_AUTOGLM_PATH) and _AUTOGLM_PATH not in sys.path:
    sys.path.insert(0, _AUTOGLM_PATH)

from android_world.agents import base_agent
from android_world.env import interface

try:
    from phone_agent import PhoneAgent
    from phone_agent.agent import AgentConfig, StepResult
    from phone_agent.model import ModelConfig
except ImportError as e:
    raise ImportError(
        f"AutoGLM not found at {_AUTOGLM_PATH}. "
        "Please ensure AutoGLM is available in the expected location."
    ) from e


class AutoGLMAgent(base_agent.EnvironmentInteractingAgent):
    """Adapter for AutoGLM PhoneAgent to work with android_world framework."""

    def __init__(
        self,
        env: interface.AsyncEnv,
        model_config: ModelConfig | None = None,
        agent_config: AgentConfig | None = None,
        name: str = 'AutoGLM',
    ):
        """Initializes AutoGLM agent adapter.

        Args:
            env: The android_world environment.
            model_config: AutoGLM model configuration.
            agent_config: AutoGLM agent configuration.
            name: Agent name.
        """
        super().__init__(env, name, transition_pause=None)
        
        # Get device ID from ADB
        device_id = self._get_device_id()
        
        # Create AutoGLM agent config if not provided
        if agent_config is None:
            agent_config = AgentConfig(device_id=device_id)
        else:
            agent_config.device_id = device_id or agent_config.device_id
        
        # Create model config if not provided
        if model_config is None:
            model_config = ModelConfig()
        
        # Initialize AutoGLM PhoneAgent
        self._autoglm_agent = PhoneAgent(
            model_config=model_config,
            agent_config=agent_config,
        )
        
        self._is_first_step = True
        self._task_goal = None

    def _get_adb_cmd(self) -> str:
        """Get adb executable path (same logic as _get_device_id)."""
        adb_cmd = 'adb'
        if os.name == 'nt':
            potential_adb_paths = [
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
                os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'Local', 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
                os.path.join(os.environ.get('ANDROID_HOME', ''), 'platform-tools', 'adb.exe') if os.environ.get('ANDROID_HOME') else None,
                os.path.join(os.environ.get('ANDROID_SDK_ROOT', ''), 'platform-tools', 'adb.exe') if os.environ.get('ANDROID_SDK_ROOT') else None,
            ]
            for path in potential_adb_paths:
                if path and os.path.isfile(path):
                    return path
        return adb_cmd

    def _get_device_id(self) -> str | None:
        """Get device ID from ADB."""
        import subprocess

        adb_cmd = self._get_adb_cmd()
        try:
            result = subprocess.run(
                [adb_cmd, 'devices'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                if line.strip() and '\tdevice' in line:
                    device_id = line.split('\t')[0].strip()
                    return device_id
        except Exception:
            pass
        return None

    def _get_display_size(self) -> tuple[int, int]:
        """Get display size via adb shell wm size (no screenshot)."""
        import re
        import subprocess

        device_id = self._autoglm_agent.agent_config.device_id
        adb_cmd = self._get_adb_cmd()
        args = [adb_cmd]
        if device_id:
            args.extend(['-s', device_id])
        args.extend(['shell', 'wm', 'size'])
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=5, encoding='utf-8', errors='ignore'
            )
            out = (result.stdout or '') + (result.stderr or '')
            # "Physical size: 1080x2400" or "Override size: 1080x2400"
            match = re.search(r'(\d+)\s*x\s*(\d+)', out)
            if match:
                return int(match.group(1)), int(match.group(2))
        except Exception:
            pass
        return 1080, 2400  # fallback

    def reset(self, go_home: bool = False) -> None:
        """Resets the agent."""
        super().reset(go_home=go_home)
        self._autoglm_agent.reset()
        self._is_first_step = True
        self._task_goal = None

    def step(self, goal: str) -> base_agent.AgentInteractionResult:
        """Performs a step of the agent on the environment.

        Args:
            goal: The task goal.

        Returns:
            AgentInteractionResult with done flag and step data.
        """
        # Store goal for first step
        if self._is_first_step:
            self._task_goal = goal
        
        # Execute AutoGLM step
        try:
            step_result: StepResult = self._autoglm_agent.step(
                self._task_goal if self._is_first_step else None
            )
            self._is_first_step = False
        except ValueError:
            # If task is required but not provided, use stored goal
            step_result = self._autoglm_agent.step(self._task_goal)
            self._is_first_step = False
        
        # Convert AutoGLM StepResult to android_world AgentInteractionResult
        step_data = {
            'autoglm_result': {
                'success': step_result.success,
                'finished': step_result.finished,
                'action': step_result.action,
                'thinking': step_result.thinking,
                'message': step_result.message,
            }
        }
        
        # Check if task is finished
        done = step_result.finished
        
        return base_agent.AgentInteractionResult(
            done=done,
            data=step_data,
        )

    def run_task(self, goal: str) -> str:
        """Run AutoGLM to complete the entire task.

        This method delegates the entire task execution to AutoGLM,
        which manages its own loop.

        Args:
            goal: The task goal.

        Returns:
            Final message from AutoGLM.
        """
        self._autoglm_agent.reset()
        return self._autoglm_agent.run(goal)

    def execute_action(self, action: dict[str, Any]) -> Any:
        """Execute a single action dict (e.g. from graph edge / step JSON) without LLM.

        Used for replaying a saved execution sequence. Uses adb wm size for screen
        dimensions instead of taking a screenshot.

        Args:
            action: One action dict, e.g. {"_metadata": "do", "action": "Launch", "app": "录音"}.

        Returns:
            Result from the AutoGLM action handler.
        """
        width, height = self._get_display_size()
        return self._autoglm_agent.action_handler.execute(action, width, height)
