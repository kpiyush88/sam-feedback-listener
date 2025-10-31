#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase Uploader for SAM Agent Messages
Uploads parsed messages to Supabase database with JSONB storage (no field normalization)
"""

import os
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

try:
    from supabase import create_client, Client  # type: ignore
except ImportError:  # Allow tests to run without supabase installed
    class Client:  # type: ignore
        pass
    def create_client(url: str, key: str):  # type: ignore
        raise ImportError("supabase package not installed. Install with 'pip install supabase'.")

from message_parser import MessageParser, ParsedMessage


class DataMapper:
    """Maps ParsedMessage data to database schema with JSONB storage (no normalization)"""

    @staticmethod
    def to_message(parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        """Convert ParsedMessage to message record with JSONB storage (no field normalization)"""

        if not parsed.message_id:
            parsed.message_id = f"auto-{parsed.id}-{int(parsed.timestamp.timestamp())}"

        return {
            # Indexed columns only
            'message_id': parsed.message_id,
            'context_id': context_id,
            'task_id': task_id,
            'timestamp': parsed.timestamp.isoformat(),
            'role': parsed.role,  # Store as string, not enum
            'agent_name': parsed.agent_name,
            'topic': parsed.topic,

            # JSONB columns - store raw data as-is, no normalization
            'message_content': parsed.message_parts,
            'tool_calls': DataMapper.extract_tool_calls(parsed),
            'raw_payload': getattr(parsed, 'raw_payload', None),
            'user_context_raw': parsed.raw_user_profile,
            'token_usage_raw': parsed.raw_token_usage,
            'metadata': {
                'task_status': parsed.task_status,
                'message_type': parsed.message_text is not None,
                'method': getattr(parsed, 'method', None),
            }
        }

    @staticmethod
    def extract_tool_calls(parsed: ParsedMessage) -> Optional[List[Dict[str, Any]]]:
        """
        Extract tool calls from ParsedMessage with ORIGINAL field names (no normalization).
        Returns tool calls in JSONB-ready format preserving original structure.
        """
        # Check if there are ANY tool calls or results
        if not parsed.tool_calls and not parsed.tool_results:
            return None

        tool_calls = []

        # Store raw tool calls as-is with original field names
        for tool_call in parsed.tool_calls:
            # Keep original structure: id, name, args (not function_call_id, tool_name, parameters)
            tool_entry = {
                'type': tool_call.get('type'),
                'timestamp': parsed.timestamp.isoformat()
            }

            # For tool_invocation_start, extract with original names
            if tool_call.get('type') == 'tool_invocation_start':
                tool_entry['id'] = tool_call.get('function_call_id')
                tool_entry['name'] = tool_call.get('tool_name')
                tool_entry['args'] = tool_call.get('tool_args', {})

            # For function_call from LLM response, extract as-is
            elif tool_call.get('type') == 'llm_response':
                func_call = tool_call.get('function_call', {})
                tool_entry['id'] = func_call.get('id')
                tool_entry['name'] = func_call.get('name')
                tool_entry['args'] = func_call.get('args', {})

            tool_calls.append(tool_entry)

        # Also include raw tool results as-is
        for result in parsed.tool_results:
            if result.get('type') == 'tool_result':
                tool_calls.append({
                    'type': 'tool_result',
                    'id': result.get('function_call_id'),
                    'name': result.get('tool_name'),
                    'result': result.get('result_data'),
                    'timestamp': parsed.timestamp.isoformat()
                })

        return tool_calls if tool_calls else None


class MessageManager:
    """Manages message records in Supabase with JSONB storage"""

    def __init__(self, client: Client):
        self.client = client

    def insert(self, parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None) -> tuple[str, bool]:
        """
        Insert message into database with JSONB columns (no field normalization).
        Returns (message_id, is_new) tuple.
        """
        if not parsed.message_id:
            parsed.message_id = f"auto-{parsed.id}-{int(parsed.timestamp.timestamp())}"

        msg = DataMapper.to_message(parsed, context_id, task_id)

        try:
            self.client.table('messages').insert(msg).execute()
            return parsed.message_id, True
        except Exception as e:
            # Check if it's a duplicate key error
            error_dict = str(e)
            if '23505' in error_dict or 'duplicate key' in error_dict.lower():
                # Message already exists
                return parsed.message_id, False
            else:
                # Re-raise other errors
                raise


class SupabaseUploader:
    """Main uploader class that coordinates all managers"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None):
        """Initialize Supabase client"""
        self.url = supabase_url or os.getenv("SUPABASE_URL")
        self.key = supabase_key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be provided")

        self.client: Client = create_client(self.url, self.key)
        self.message_manager = MessageManager(self.client)

    def upload_message(self, parsed: ParsedMessage) -> Dict[str, str]:
        """
        Upload a parsed message to Supabase with JSONB storage (no field normalization).
        Returns dict with created record IDs.
        """
        result = {}

        try:
            if not parsed.context_id:
                return {'error': 'Missing context_id'}

            result['context_id'] = parsed.context_id

            # Determine task_id: ALWAYS the actual message's task ID
            # - For main tasks: gdk-task-*
            # - For subtasks: a2a_subtask_*
            t_id = parsed.id  # Always use the actual task ID
            result['task_id'] = t_id

            # Insert message with JSONB columns
            m_id, is_new = self.message_manager.insert(parsed, parsed.context_id, t_id)
            result['message_id'] = m_id
            result['message_is_new'] = is_new

        except Exception as e:
            print(f"Error uploading message: {e}")
            result['error'] = str(e)

        return result

    def batch_upload_from_directory(self, directory: str, pattern: str = "*.json") -> Dict[str, Any]:
        """
        Upload all JSON files from a directory. Returns statistics about the upload.

        Files are sorted by timestamp to ensure chronological order.
        """
        stats = {
            'total_files': 0,
            'successful': 0,
            'failed': 0,
            'errors': [],
            'main_tasks_processed': 0,
            'subtasks_processed': 0
        }

        directory_path = Path(directory)
        parser = MessageParser()

        # Collect all files with their parsed timestamps
        files_with_timestamps = []
        for json_file in directory_path.glob(pattern):
            try:
                parsed = parser.parse_message_file(str(json_file))
                files_with_timestamps.append((json_file, parsed.timestamp, parsed.id))
            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append({
                    'file': str(json_file),
                    'error': f"Failed to parse for sorting: {str(e)}"
                })

        # Sort by timestamp to ensure main tasks (gdk-task-*) are processed before subtasks
        # Secondary sort: main tasks before subtasks at same timestamp
        def sort_key(item):
            _, timestamp, task_id = item
            is_subtask = task_id and task_id.startswith('a2a_subtask_')
            return (timestamp, is_subtask)  # False (main task) sorts before True (subtask)

        files_with_timestamps.sort(key=sort_key)

        # Now upload in sorted order
        for json_file, _, task_id in files_with_timestamps:
            stats['total_files'] += 1

            try:
                parsed = parser.parse_message_file(str(json_file))
                result = self.upload_message(parsed)

                if 'error' in result:
                    stats['failed'] += 1
                    stats['errors'].append({
                        'file': str(json_file),
                        'error': result['error']
                    })
                else:
                    stats['successful'] += 1

                    # Track main tasks vs subtasks
                    if task_id and task_id.startswith('gdk-task-'):
                        stats['main_tasks_processed'] += 1
                    elif task_id and task_id.startswith('a2a_subtask_'):
                        stats['subtasks_processed'] += 1

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append({
                    'file': str(json_file),
                    'error': str(e)
                })

        return stats


def main():
    """Test the uploader"""
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python supabase_uploader.py <json_file_or_directory>")
        sys.exit(1)

    path = sys.argv[1]
    uploader = SupabaseUploader()
    parser = MessageParser()

    path_obj = Path(path)

    if path_obj.is_file():
        # Upload single file
        parsed = parser.parse_message_file(path)
        result = uploader.upload_message(parsed)
        print(json.dumps(result, indent=2, default=str))

    elif path_obj.is_dir():
        # Upload directory
        stats = uploader.batch_upload_from_directory(path)
        print(json.dumps(stats, indent=2, default=str))

    else:
        print(f"Error: {path} is not a valid file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
