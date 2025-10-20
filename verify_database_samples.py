#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database Sample Verification
Shows sample records from each table to verify data completeness
"""

import json
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def verify_database_samples():
    """Query and display sample data from all tables"""

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

    client: Client = create_client(supabase_url, supabase_key)

    print("\n" + "="*80)
    print("DATABASE SAMPLE DATA VERIFICATION")
    print("="*80)

    # 1. Conversations Sample
    print("\n1. CONVERSATIONS TABLE - Sample Record")
    print("-" * 80)
    conv_response = client.table('conversations').select('*').limit(1).execute()
    if conv_response.data:
        conv = conv_response.data[0]
        print(json.dumps(conv, indent=2, default=str))

    # 2. Tasks Sample
    print("\n2. TASKS TABLE - Sample Record")
    print("-" * 80)
    task_response = client.table('tasks').select('*').limit(1).execute()
    if task_response.data:
        task = task_response.data[0]
        print(json.dumps(task, indent=2, default=str))

    # 3. Messages Sample - with FULL parts
    print("\n3. MESSAGES TABLE - Sample Record (with full parts)")
    print("-" * 80)
    msg_response = client.table('messages').select('*').limit(1).execute()
    if msg_response.data:
        msg = msg_response.data[0]
        # Show message without parts first
        msg_copy = msg.copy()
        parts = msg_copy.pop('parts', [])
        print("Message (without parts):")
        print(json.dumps(msg_copy, indent=2, default=str))

        # Show parts separately with size info
        print(f"\nParts field:")
        print(f"  Type: {type(parts)}")
        print(f"  Count: {len(parts) if isinstance(parts, list) else 'N/A'}")
        if parts and isinstance(parts, list):
            parts_json = json.dumps(parts, indent=2)
            parts_size = len(parts_json)
            print(f"  Total size: {parts_size} bytes")
            print(f"  First 500 characters of parts:")
            print(f"  {parts_json[:500]}")
            if parts_size > 500:
                print(f"  ... (truncated, {parts_size - 500} more bytes)")

    # 4. Count statistics
    print("\n4. DATABASE STATISTICS")
    print("-" * 80)

    # Conversations count
    conv_count = client.table('conversations').select('context_id', count='exact').execute()
    print(f"Total conversations: {conv_count.count}")

    # Tasks count
    task_count = client.table('tasks').select('task_id', count='exact').execute()
    print(f"Total tasks: {task_count.count}")

    # Messages count
    msg_count = client.table('messages').select('message_id', count='exact').execute()
    print(f"Total messages: {msg_count.count}")

    # Tool calls count
    tool_count = client.table('tool_calls').select('id', count='exact').execute()
    print(f"Total tool calls: {tool_count.count}")

    # 5. User data verification
    print("\n5. USER DATA VERIFICATION")
    print("-" * 80)
    conv_with_user = client.table('conversations').select('*').not_.is_('user_id', 'null').execute()
    print(f"Conversations with user_id: {len(conv_with_user.data)}")

    if conv_with_user.data:
        user_conv = conv_with_user.data[0]
        print("\nUser fields populated:")
        user_fields = [
            'user_id', 'user_email', 'user_name', 'user_country',
            'user_job_grade', 'user_company', 'user_manager_id',
            'user_location', 'user_language', 'user_authenticated'
        ]
        for field in user_fields:
            value = user_conv.get(field)
            print(f"  {field}: {value}")

        print("\nUser metadata fields:")
        metadata = user_conv.get('metadata', {})
        user_profile = metadata.get('user_profile', {})
        if user_profile:
            for key, value in user_profile.items():
                print(f"  {key}: {value}")

    # 6. Task metadata verification
    print("\n6. TASK METADATA VERIFICATION")
    print("-" * 80)
    tasks = client.table('tasks').select('*').execute()
    if tasks.data:
        tasks_with_is_final = [t for t in tasks.data if t.get('metadata', {}).get('is_final') is not None]
        tasks_with_result_id = [t for t in tasks.data if t.get('metadata', {}).get('result_id')]

        print(f"Tasks with is_final: {len(tasks_with_is_final)}/{len(tasks.data)}")
        print(f"Tasks with result_id: {len(tasks_with_result_id)}/{len(tasks.data)}")

        if tasks_with_result_id:
            print("\nSample task with result_id:")
            sample_task = tasks_with_result_id[0]
            print(f"  Task ID: {sample_task.get('task_id')}")
            print(f"  Result ID: {sample_task.get('metadata', {}).get('result_id')}")
            print(f"  Is Final: {sample_task.get('metadata', {}).get('is_final')}")

    # 7. Message metadata verification
    print("\n7. MESSAGE METADATA VERIFICATION")
    print("-" * 80)
    messages = client.table('messages').select('*').execute()
    if messages.data:
        msgs_with_kind = [m for m in messages.data if m.get('metadata', {}).get('message_kind')]
        msgs_with_correlation = [m for m in messages.data if m.get('correlation_id')]
        msgs_with_is_partial = [m for m in messages.data if m.get('metadata', {}).get('is_partial') is not None]
        msgs_with_token_usage = [m for m in messages.data if m.get('metadata', {}).get('token_usage')]

        print(f"Messages with message_kind: {len(msgs_with_kind)}/{len(messages.data)}")
        print(f"Messages with correlation_id: {len(msgs_with_correlation)}/{len(messages.data)}")
        print(f"Messages with is_partial: {len(msgs_with_is_partial)}/{len(messages.data)}")
        print(f"Messages with token_usage: {len(msgs_with_token_usage)}/{len(messages.data)}")

        if msgs_with_token_usage:
            print("\nSample message with token usage:")
            sample_msg = msgs_with_token_usage[0]
            token_usage = sample_msg.get('metadata', {}).get('token_usage', {})
            print(f"  Message ID: {sample_msg.get('message_id')}")
            print(f"  Model: {token_usage.get('model')}")
            print(f"  Input tokens: {token_usage.get('input_tokens')}")
            print(f"  Output tokens: {token_usage.get('output_tokens')}")
            print(f"  Total tokens: {token_usage.get('total_tokens')}")

    # 8. Parts data verification
    print("\n8. PARTS DATA VERIFICATION")
    print("-" * 80)
    messages_with_parts = [m for m in messages.data if m.get('parts')]
    print(f"Messages with parts: {len(messages_with_parts)}/{len(messages.data)}")

    if messages_with_parts:
        # Check parts sizes
        parts_sizes = []
        for msg in messages_with_parts:
            parts = msg.get('parts', [])
            if parts and isinstance(parts, list):
                parts_json = json.dumps(parts)
                parts_sizes.append(len(parts_json))

        if parts_sizes:
            print(f"\nParts size statistics:")
            print(f"  Minimum size: {min(parts_sizes)} bytes")
            print(f"  Maximum size: {max(parts_sizes)} bytes")
            print(f"  Average size: {sum(parts_sizes) / len(parts_sizes):.0f} bytes")

            # Check if any parts are suspiciously small (possibly sanitized)
            small_parts = [s for s in parts_sizes if s < 100]
            if small_parts:
                print(f"  WARNING: {len(small_parts)} messages have parts < 100 bytes (possibly sanitized)")
            else:
                print(f"  All parts appear to contain FULL data (all > 100 bytes)")

    print("\n" + "="*80)
    print("VERIFICATION COMPLETE")
    print("="*80)


if __name__ == "__main__":
    try:
        verify_database_samples()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
