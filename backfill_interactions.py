#!/usr/bin/env python3
"""
Backfill interaction metrics from existing messages in the database
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

CONTEXT_ID = "web-session-f83e50f894164583b5a3afb33aacf241"

def main():
    # Initialize Supabase client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    client: Client = create_client(url, key)

    print(f"Backfilling interaction metrics for context_id: {CONTEXT_ID}")
    print("=" * 60)

    # Get all interactions for this context
    resp = client.table('interactions').select('*').eq('context_id', CONTEXT_ID).execute()
    interactions = resp.data

    print(f"Found {len(interactions)} interactions")
    print("=" * 60)

    for interaction in interactions:
        interaction_id = interaction['interaction_id']
        print(f"\nProcessing: {interaction_id}")

        # Get all messages for this interaction_id
        msgs = client.table('messages').select('*').eq('context_id', CONTEXT_ID).execute()

        # Filter messages that belong to this interaction
        # Main task messages or subtask messages with this parent
        interaction_messages = []
        for msg in msgs.data:
            msg_task_id = msg.get('task_id')
            if msg_task_id == interaction_id:
                # This is the main task message
                interaction_messages.append(msg)
            elif msg_task_id:
                # Check if this is a subtask of our interaction
                task_resp = client.table('tasks').select('parent_task_id').eq('task_id', msg_task_id).execute()
                if task_resp.data and task_resp.data[0].get('parent_task_id') == interaction_id:
                    interaction_messages.append(msg)

        print(f"  Found {len(interaction_messages)} messages")

        # Calculate metrics
        total_messages = len(interaction_messages)
        total_tokens = sum(msg.get('total_tokens', 0) or 0 for msg in interaction_messages)
        total_input_tokens = sum(msg.get('input_tokens', 0) or 0 for msg in interaction_messages)
        total_output_tokens = sum(msg.get('output_tokens', 0) or 0 for msg in interaction_messages)

        # Get user query and agent response FROM MAIN TASK ONLY (not subtasks)
        user_query = None
        user_message_id = None
        user_query_timestamp = None
        agent_response = None
        agent_response_message_id = None
        agent_response_timestamp = None

        # Query messages for MAIN task only (task_id == interaction_id)
        main_task_msgs = client.table('messages').select('*').eq('task_id', interaction_id).execute()

        for msg in main_task_msgs.data if main_task_msgs.data else []:
            if msg.get('role') == 'user':
                user_query = msg.get('content')
                user_message_id = msg.get('message_id')
                user_query_timestamp = msg.get('timestamp')
            elif msg.get('role') == 'agent':
                if msg.get('message_type') == 'final_response':
                    agent_response = msg.get('content')
                    agent_response_message_id = msg.get('message_id')
                    agent_response_timestamp = msg.get('timestamp')

        # Get subtasks count
        subtasks = client.table('tasks').select('task_id').eq('parent_task_id', interaction_id).execute()
        num_subtasks = len(subtasks.data) if subtasks.data else 0

        # Get delegated agents from subtasks
        delegated_agents = []
        if subtasks.data:
            for subtask in subtasks.data:
                subtask_id = subtask['task_id']
                task_detail = client.table('tasks').select('agent_name').eq('task_id', subtask_id).execute()
                if task_detail.data:
                    agent_name = task_detail.data[0].get('agent_name')
                    if agent_name and agent_name not in delegated_agents and agent_name != interaction.get('primary_agent'):
                        delegated_agents.append(agent_name)

        # Get tool calls count
        tool_calls = client.table('tool_calls').select('id').in_('message_id', [m['message_id'] for m in interaction_messages]).execute()
        num_tool_calls = len(tool_calls.data) if tool_calls.data else 0

        # Determine completion status
        response_state = 'in_progress'
        completed_at = None
        if agent_response:
            response_state = 'completed'
            completed_at = agent_response_timestamp

        # Update interaction
        update_data = {
            'total_messages': total_messages,
            'total_tokens': total_tokens,
            'total_input_tokens': total_input_tokens,
            'total_output_tokens': total_output_tokens,
            'num_subtasks': num_subtasks,
            'num_tool_calls': num_tool_calls,
            'response_state': response_state
        }

        if user_query:
            update_data['user_query'] = user_query
            update_data['user_message_id'] = user_message_id
            update_data['user_query_timestamp'] = user_query_timestamp

        if agent_response:
            update_data['agent_response'] = agent_response
            update_data['agent_response_message_id'] = agent_response_message_id
            update_data['agent_response_timestamp'] = agent_response_timestamp

        if completed_at:
            update_data['completed_at'] = completed_at

        if delegated_agents:
            update_data['delegated_agents'] = delegated_agents

        client.table('interactions').update(update_data).eq('interaction_id', interaction_id).execute()

        print(f"  ✅ Updated: {total_messages} msgs, {total_tokens} tokens, {num_subtasks} subtasks, {num_tool_calls} tools")
        print(f"  Status: {response_state}, Delegated: {delegated_agents}")

    print("\n" + "=" * 60)
    print("Backfill complete!")

if __name__ == "__main__":
    main()
