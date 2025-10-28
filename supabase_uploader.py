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


class DatabaseCache:
    """Simple existence cache for conversation IDs"""

    def __init__(self):
        self._conversations: set[str] = set()

    def has_conversation(self, context_id: str) -> bool:
        return context_id in self._conversations

    def cache_conversation(self, context_id: str) -> None:
        self._conversations.add(context_id)

    def clear(self) -> None:
        self._conversations.clear()


class DataMapper:
    """Maps ParsedMessage data to database schema with JSONB storage (no normalization)"""

    @staticmethod
    def extract_correlation_id(parsed: ParsedMessage) -> Optional[str]:
        """
        Extract correlation ID for indexed queries.

        Logic:
        - For status messages (topic contains '/status/'): Extract from last part of topic
        - For request messages (topic contains '/request/'): Extract from parsed.id

        Supports both gdk-task-* and a2a_subtask_* patterns.
        """
        if not parsed.topic:
            return None

        # For status messages, extract from topic's last part
        if '/status/' in parsed.topic:
            parts = parsed.topic.split('/')
            if len(parts) > 0:
                last_part = parts[-1]
                if last_part.startswith('gdk-task-') or last_part.startswith('a2a_subtask_'):
                    return last_part

        # For request messages, extract from parsed.id
        elif '/request/' in parsed.topic:
            if parsed.id and (parsed.id.startswith('gdk-task-') or parsed.id.startswith('a2a_subtask_')):
                return parsed.id

        return None

    @staticmethod
    def to_conversation(parsed: ParsedMessage) -> Dict[str, Any]:
        """Convert ParsedMessage to conversation record with minimal indexed fields"""
        data: Dict[str, Any] = {
            'context_id': parsed.context_id,
            'started_at': parsed.timestamp.isoformat(),
        }

        # Extract minimal user info for indexed columns
        if parsed.raw_user_profile:
            data['user_id'] = parsed.raw_user_profile.get('id')
            data['user_email'] = parsed.raw_user_profile.get('email') or parsed.raw_user_profile.get('workEmail')
            data['user_name'] = parsed.raw_user_profile.get('name') or parsed.raw_user_profile.get('displayName')
            data['user_country'] = parsed.raw_user_profile.get('country')

        # Store complete raw user profile as JSONB
        data['user_context_raw'] = parsed.raw_user_profile

        return data

    @staticmethod
    def to_message(parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None,
                   interaction_id: Optional[str] = None) -> Dict[str, Any]:
        """Convert ParsedMessage to message record with JSONB storage (no field normalization)"""

        if not parsed.message_id:
            parsed.message_id = f"auto-{parsed.id}-{int(parsed.timestamp.timestamp())}"

        return {
            # Indexed columns only
            'message_id': parsed.message_id,
            'context_id': context_id,
            'task_id': task_id,
            'interaction_id': interaction_id,
            'timestamp': parsed.timestamp.isoformat(),
            'role': parsed.role,  # Store as string, not enum
            'agent_name': parsed.agent_name,
            'topic': parsed.topic,
            'feedback_id': getattr(parsed, 'feedback_id', None),
            'correlation_id': DataMapper.extract_correlation_id(parsed),

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


class ConversationManager:
    """Manages conversations via context_id"""

    def __init__(self, client: Client, cache: DatabaseCache):
        self.client = client
        self.cache = cache

    def ensure_exists(self, parsed: ParsedMessage) -> str:
        """Ensure conversation exists using UPSERT"""
        context_id = parsed.context_id
        if not context_id:
            raise ValueError("Missing context_id")
        if self.cache.has_conversation(context_id):
            return context_id

        # UPSERT: Insert if not exists, do nothing if exists
        self.client.table('conversations').upsert(
            DataMapper.to_conversation(parsed),
            on_conflict='context_id'
        ).execute()

        self.cache.cache_conversation(context_id)
        return context_id

    def update_stats(self, parsed: ParsedMessage, context_id: str) -> None:
        """Update conversation statistics"""
        try:
            # Call Postgres function for atomic increment
            params = {
                'p_context_id': context_id,
                'p_message_increment': 1,
                'p_token_increment': 0,  # Token aggregation now in JSONB
                'p_input_token_increment': 0,
                'p_output_token_increment': 0,
                'p_cached_token_increment': 0,
                'p_ended_at': parsed.timestamp.isoformat()
            }
            self.client.rpc('increment_conversation_stats', params).execute()
        except Exception as e:
            # Fallback to read-modify-write if function doesn't exist
            error_str = str(e)
            print(f"⚠️  RPC failed, using fallback for {context_id}: {e}")
            if 'function' in error_str.lower() and 'does not exist' in error_str.lower():
                self._update_stats_fallback(parsed, context_id)
            else:
                self._update_stats_fallback(parsed, context_id)

    def _update_stats_fallback(self, parsed: ParsedMessage, context_id: str) -> None:
        """Fallback method using read-modify-write"""
        response = self.client.table('conversations').select('*').eq('context_id', context_id).execute()

        if not response.data:
            return

        conv = response.data[0]
        update_data = {
            'total_messages': conv.get('total_messages', 0) + 1,
            'ended_at': parsed.timestamp.isoformat()
        }

        self.client.table('conversations').update(update_data).eq('context_id', context_id).execute()


class MessageManager:
    """Manages message records in Supabase with JSONB storage"""

    def __init__(self, client: Client):
        self.client = client

    def insert(self, parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None,
               interaction_id: Optional[str] = None) -> tuple[str, bool]:
        """
        Insert message into database with JSONB columns (no field normalization).
        Returns (message_id, is_new) tuple.
        """
        if not parsed.message_id:
            parsed.message_id = f"auto-{parsed.id}-{int(parsed.timestamp.timestamp())}"

        msg = DataMapper.to_message(parsed, context_id, task_id, interaction_id)

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


class InteractionManager:
    """Manages interaction records (user query → agent response pairs)"""

    def __init__(self, client: Client):
        self.client = client
        self._interaction_cache: Dict[str, Dict[str, Any]] = {}
        self._agent_name_cache: Dict[str, str] = {}  # interaction_id -> agent_name

    def get_or_create_interaction(self, parsed: ParsedMessage, context_id: str) -> str:
        """Get or create an interaction record. Returns interaction_id."""
        interaction_id = self._determine_interaction_id(parsed, context_id)

        if not interaction_id:
            return None

        # Check cache first
        if interaction_id in self._interaction_cache:
            return interaction_id

        # Check if exists in database
        resp = self.client.table('interactions').select('interaction_id,primary_agent').eq('interaction_id', interaction_id).execute()

        if not resp.data:
            self._create_interaction(parsed, context_id, interaction_id)
        else:
            # Cache agent_name for this interaction
            primary_agent = resp.data[0].get('primary_agent')
            if primary_agent:
                self._agent_name_cache[interaction_id] = primary_agent

        # Cache it
        self._interaction_cache[interaction_id] = {'created': True}
        return interaction_id

    def get_agent_name_for_interaction(self, interaction_id: str) -> Optional[str]:
        """Get cached agent_name for an interaction"""
        # Check cache first
        if interaction_id in self._agent_name_cache:
            return self._agent_name_cache[interaction_id]

        # Query database
        try:
            resp = self.client.table('interactions').select('primary_agent').eq('interaction_id', interaction_id).execute()
            if resp.data and resp.data[0].get('primary_agent'):
                agent_name = resp.data[0]['primary_agent']
                self._agent_name_cache[interaction_id] = agent_name
                return agent_name
        except:
            pass

        return None

    def _determine_interaction_id(self, parsed: ParsedMessage, context_id: str) -> Optional[str]:
        """
        Determine the main interaction ID from the message.

        Logic:
        1. If message has parent_task_id, that's the interaction_id
        2. If message.id starts with 'gdk-task-', it IS the interaction
        3. If message.id starts with 'a2a_subtask_', find parent interaction from DB
        """
        # If this message has a parent_task_id, that's the interaction_id
        if parsed.parent_task_id:
            return parsed.parent_task_id

        # If this IS the main task
        if parsed.id and parsed.id.startswith('gdk-task-'):
            return parsed.id

        # For subtasks, need to find parent interaction from context and timestamp
        if parsed.id and parsed.id.startswith('a2a_subtask_'):
            parent_id = self._find_parent_interaction(context_id, parsed.timestamp)
            return parent_id

        return None

    def _find_parent_interaction(self, context_id: str, timestamp: datetime) -> Optional[str]:
        """Find the parent interaction for a subtask by context and timestamp"""
        try:
            resp = self.client.table('interactions').select('interaction_id').eq(
                'context_id', context_id
            ).lte('started_at', timestamp.isoformat()).order(
                'started_at', desc=True
            ).limit(1).execute()

            if resp.data:
                return resp.data[0]['interaction_id']
        except Exception as e:
            print(f"⚠️  Error finding parent interaction: {e}")

        return None

    def _create_interaction(self, parsed: ParsedMessage, context_id: str, interaction_id: str) -> None:
        """Create a new interaction record"""
        resp = self.client.table('interactions').select('interaction_number').eq('context_id', context_id).order('interaction_number', desc=True).limit(1).execute()

        interaction_number = 1
        if resp.data:
            interaction_number = resp.data[0].get('interaction_number', 0) + 1

        interaction_data = {
            'interaction_id': interaction_id,
            'context_id': context_id,
            'interaction_number': interaction_number,
            'started_at': parsed.timestamp.isoformat(),
            'primary_agent': parsed.agent_name,
            'response_state': 'in_progress',
            'metadata': {}
        }

        try:
            self.client.table('interactions').insert(interaction_data).execute()
            # Cache agent_name if available
            if parsed.agent_name:
                self._agent_name_cache[interaction_id] = parsed.agent_name
        except Exception as e:
            error_str = str(e)
            if '23505' in error_str or 'duplicate key' in error_str.lower():
                print(f"ℹ️  Interaction {interaction_id[:24]}... already exists (race condition)")
            else:
                raise

    def update_interaction(self, parsed: ParsedMessage, interaction_id: str) -> None:
        """Update interaction with response and metrics"""
        if not interaction_id:
            return

        update_data = {}

        # Update agent response if this is the main task's final response
        if parsed.role == 'agent' and parsed.task_status == 'completed' and parsed.id == interaction_id:
            update_data['agent_response_message_id'] = parsed.message_id
            update_data['agent_response'] = parsed.message_text
            update_data['agent_response_timestamp'] = parsed.timestamp.isoformat()
            update_data['response_state'] = 'completed'
            update_data['completed_at'] = parsed.timestamp.isoformat()

        # Track delegated agents
        if parsed.agent_name and parsed.parent_task_id:
            resp = self.client.table('interactions').select('delegated_agents,primary_agent').eq('interaction_id', interaction_id).execute()
            if resp.data:
                current_delegated = resp.data[0].get('delegated_agents', []) or []
                primary = resp.data[0].get('primary_agent')
                if parsed.agent_name != primary and parsed.agent_name not in current_delegated:
                    current_delegated.append(parsed.agent_name)
                    update_data['delegated_agents'] = current_delegated

        # Update subtask count
        if parsed.parent_task_id:
            resp = self.client.table('interactions').select('num_subtasks').eq('interaction_id', interaction_id).execute()
            if resp.data:
                update_data['num_subtasks'] = resp.data[0].get('num_subtasks', 0) + 1

        # Update tool call count
        if parsed.tool_calls or parsed.tool_results:
            num_tool_calls = len(parsed.tool_calls) + len(parsed.tool_results)
            resp = self.client.table('interactions').select('num_tool_calls').eq('interaction_id', interaction_id).execute()
            if resp.data:
                update_data['num_tool_calls'] = resp.data[0].get('num_tool_calls', 0) + num_tool_calls

        # Increment message count
        resp = self.client.table('interactions').select('total_messages').eq('interaction_id', interaction_id).execute()
        if resp.data:
            update_data['total_messages'] = resp.data[0].get('total_messages', 0) + 1

        if update_data:
            self.client.table('interactions').update(update_data).eq('interaction_id', interaction_id).execute()


class SupabaseUploader:
    """Main uploader class that coordinates all managers"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None):
        """Initialize Supabase client and managers"""
        self.url = supabase_url or os.getenv("SUPABASE_URL")
        self.key = supabase_key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be provided")

        self.client: Client = create_client(self.url, self.key)

        # Initialize cache and managers
        self.cache = DatabaseCache()
        self.conversation_manager = ConversationManager(self.client, self.cache)
        self.message_manager = MessageManager(self.client)
        self.interaction_manager = InteractionManager(self.client)

    def upload_message(self, parsed: ParsedMessage) -> Dict[str, str]:
        """
        Upload a parsed message to Supabase with JSONB storage (no field normalization).
        Returns dict with created record IDs.
        """
        result = {}

        try:
            if not parsed.context_id:
                return {'error': 'Missing context_id'}

            # Ensure conversation exists
            ctx = self.conversation_manager.ensure_exists(parsed)
            result['context_id'] = ctx

            # Get or create interaction
            interaction_id = self.interaction_manager.get_or_create_interaction(parsed, ctx)
            result['interaction_id'] = interaction_id

            # Populate agent_name if missing (use fallback from interaction cache)
            if not parsed.agent_name and interaction_id and parsed.role == 'agent':
                cached_agent = self.interaction_manager.get_agent_name_for_interaction(interaction_id)
                if cached_agent:
                    parsed.agent_name = cached_agent
                    result['agent_name_source'] = 'cache'
            else:
                result['agent_name_source'] = 'parsed'

            # Determine task_id to align with interaction hierarchy
            # For subtasks: use parent interaction_id as task_id for consistency
            # For main tasks: use parsed.id as task_id
            # This ensures task_id == interaction_id for proper relationship tracking
            if parsed.id and parsed.id.startswith('a2a_subtask_'):
                # Subtask: use parent interaction_id as task_id
                t_id = interaction_id
            elif parsed.id and parsed.id.startswith('gdk-task-'):
                # Main task: use parsed.id as task_id
                t_id = parsed.id
            else:
                # Fallback: use parsed.id
                t_id = parsed.id
            result['task_id'] = t_id

            # Insert message with JSONB columns
            m_id, is_new = self.message_manager.insert(parsed, ctx, t_id, interaction_id)
            result['message_id'] = m_id
            result['message_is_new'] = is_new

            # Only update stats if this is a new message
            if is_new:
                self.conversation_manager.update_stats(parsed, ctx)
                if interaction_id:
                    self.interaction_manager.update_interaction(parsed, interaction_id)

        except Exception as e:
            print(f"Error uploading message: {e}")
            result['error'] = str(e)

        return result

    def batch_upload_from_directory(self, directory: str, pattern: str = "*.json") -> Dict[str, Any]:
        """
        Upload all JSON files from a directory. Returns statistics about the upload.

        IMPORTANT: Files are sorted by timestamp to ensure parent interactions
        are created before subtask messages, allowing proper interaction_id linking.
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
