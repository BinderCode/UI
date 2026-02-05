#!/usr/bin/env python3
"""List all available tasks in AndroidWorld."""

import json
import sys
import io

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from android_world import registry

def list_tasks():
    """List all available tasks."""
    # Initialize registry
    task_registry = registry.TaskRegistry()
    
    # Get all task families
    families = {
        'android_world': 'All AndroidWorld tasks (Android + Information Retrieval)',
        'android': 'Android tasks only',
        'information_retrieval': 'Information Retrieval tasks only',
        'miniwob': 'MiniWoB++ tasks',
        'miniwob_subset': 'MiniWoB++ subset',
    }
    
    print("=" * 80)
    print("Available Tasks in AndroidWorld")
    print("=" * 80)
    print()
    
    # List tasks for each family
    for family_key, family_desc in families.items():
        try:
            reg = task_registry.get_registry(family_key)
            task_names = sorted(reg.keys())
            
            print(f"\n{family_desc} ({family_key}):")
            print("-" * 80)
            print(f"Total: {len(task_names)} tasks\n")
            
            # Try to load task metadata for descriptions
            try:
                with open('android_world/task_metadata.json', 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                    metadata_dict = {item['task_name']: item for item in metadata}
            except Exception:
                metadata_dict = {}
            
            # Print tasks with descriptions if available
            for task_name in task_names:
                if task_name in metadata_dict:
                    task_info = metadata_dict[task_name]
                    difficulty = task_info.get('difficulty', 'unknown')
                    template = task_info.get('task_template', 'No description')
                    print(f"  - {task_name}")
                    print(f"    Difficulty: {difficulty}")
                    print(f"    Description: {template}")
                    print()
                else:
                    print(f"  - {task_name}")
            
            print()
        except ValueError as e:
            print(f"  Error loading {family_key}: {e}")
            print()
    
    # Show usage example
    print("=" * 80)
    print("Usage Examples:")
    print("=" * 80)
    print()
    print("Run a specific task:")
    print("  python minimal_task_runner.py --task=ContactsAddContact")
    print()
    print("Run with AutoGLM:")
    print("  python minimal_task_runner.py --task=ContactsAddContact --use_autoglm")
    print()
    print("Run multiple tasks (using run.py):")
    print("  python run.py --tasks=ContactsAddContact,ClockStopWatchRunning")
    print()

if __name__ == '__main__':
    list_tasks()
