#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parts Data Integrity Verification
Verifies that message parts contain FULL JSON data without sanitization
"""

import json
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def verify_parts_integrity():
    """Verify that parts field contains complete, unsanitized data"""

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

    client: Client = create_client(supabase_url, supabase_key)

    print("\n" + "="*80)
    print("PARTS DATA INTEGRITY VERIFICATION")
    print("="*80)
    print("\nThis test confirms that message parts contain FULL JSON data,")
    print("not sanitized summaries or truncated content.")

    # Get a message with large parts
    messages = client.table('messages').select('*').execute()

    if not messages.data:
        print("\nNo messages found in database")
        return

    # Find message with largest parts
    max_parts_msg = None
    max_parts_size = 0

    for msg in messages.data:
        parts = msg.get('parts', [])
        if parts and isinstance(parts, list):
            parts_size = len(json.dumps(parts))
            if parts_size > max_parts_size:
                max_parts_size = parts_size
                max_parts_msg = msg

    if not max_parts_msg:
        print("\nNo messages with parts found")
        return

    print(f"\nAnalyzing message with largest parts:")
    print(f"  Message ID: {max_parts_msg.get('message_id')}")
    print(f"  Role: {max_parts_msg.get('role')}")
    print(f"  Message Type: {max_parts_msg.get('message_type')}")

    parts = max_parts_msg.get('parts', [])
    print(f"\n  Parts count: {len(parts)}")
    print(f"  Total parts size: {max_parts_size} bytes")

    # Analyze each part
    for i, part in enumerate(parts):
        print(f"\n  Part {i+1}:")
        print(f"    Kind: {part.get('kind')}")

        if part.get('kind') == 'text':
            text = part.get('text', '')
            print(f"    Text length: {len(text)} characters")
            print(f"    Text preview: {text[:100]}...")

        elif part.get('kind') == 'data':
            data = part.get('data', {})
            data_type = data.get('type')
            print(f"    Data type: {data_type}")

            # Check for different data types
            if data_type == 'llm_invocation':
                request = data.get('request', {})
                config = request.get('config', {})

                # Check tools
                tools = config.get('tools', [])
                print(f"    Tools count: {len(tools)}")

                # Count function declarations
                total_functions = 0
                for tool_group in tools:
                    functions = tool_group.get('function_declarations', [])
                    total_functions += len(functions)
                print(f"    Total function declarations: {total_functions}")

                # Sample first function
                if tools and tools[0].get('function_declarations'):
                    first_func = tools[0]['function_declarations'][0]
                    print(f"    First function name: {first_func.get('name')}")
                    print(f"    First function has description: {bool(first_func.get('description'))}")
                    print(f"    First function has parameters: {bool(first_func.get('parameters'))}")

                    # Check if parameters are complete
                    params = first_func.get('parameters', {})
                    if params:
                        params_json = json.dumps(params)
                        print(f"    First function parameters size: {len(params_json)} bytes")
                        print(f"    Parameters appear: {'COMPLETE' if len(params_json) > 50 else 'INCOMPLETE/SANITIZED'}")

                # Check system instruction
                system_instruction = config.get('system_instruction', '')
                if system_instruction:
                    print(f"    System instruction length: {len(system_instruction)} characters")
                    print(f"    System instruction present: {'YES - FULL DATA' if len(system_instruction) > 100 else 'POSSIBLY TRUNCATED'}")

            elif data_type == 'llm_response':
                response_data = data.get('data', {})
                print(f"    Response data keys: {list(response_data.keys())}")

                # Check candidates
                candidates = response_data.get('candidates', [])
                print(f"    Candidates count: {len(candidates)}")

                if candidates:
                    first_candidate = candidates[0]
                    content = first_candidate.get('content', {})
                    parts_in_response = content.get('parts', [])
                    print(f"    Parts in first candidate: {len(parts_in_response)}")

                # Check usage metadata
                usage_metadata = response_data.get('usage_metadata', {})
                if usage_metadata:
                    print(f"    Token usage present: YES")
                    print(f"      Total tokens: {usage_metadata.get('total_token_count')}")

    # Show a sample of the actual JSON
    print("\n" + "="*80)
    print("SAMPLE OF ACTUAL PARTS JSON (first 1000 characters):")
    print("="*80)
    parts_json = json.dumps(parts, indent=2)
    print(parts_json[:1000])
    if len(parts_json) > 1000:
        print(f"\n... (truncated for display, {len(parts_json) - 1000} more characters)")

    # Verification checks
    print("\n" + "="*80)
    print("INTEGRITY VERIFICATION RESULTS:")
    print("="*80)

    checks_passed = []
    checks_failed = []

    # Check 1: Parts size
    if max_parts_size > 1000:
        checks_passed.append("Parts contain substantial data (> 1000 bytes)")
    else:
        checks_failed.append("Parts are suspiciously small")

    # Check 2: Contains complete function declarations
    has_complete_funcs = False
    for part in parts:
        if part.get('kind') == 'data':
            data = part.get('data', {})
            if data.get('type') == 'llm_invocation':
                request = data.get('request', {})
                config = request.get('config', {})
                tools = config.get('tools', [])
                for tool_group in tools:
                    functions = tool_group.get('function_declarations', [])
                    if functions and functions[0].get('parameters'):
                        has_complete_funcs = True
                        break

    if has_complete_funcs:
        checks_passed.append("Function declarations include complete parameters")
    else:
        checks_failed.append("Function declarations may be incomplete")

    # Check 3: Contains system instructions
    has_system_instruction = False
    for part in parts:
        if part.get('kind') == 'data':
            data = part.get('data', {})
            if data.get('type') == 'llm_invocation':
                request = data.get('request', {})
                config = request.get('config', {})
                system_instruction = config.get('system_instruction', '')
                if len(system_instruction) > 100:
                    has_system_instruction = True
                    break

    if has_system_instruction:
        checks_passed.append("System instructions present and complete")
    else:
        checks_failed.append("System instructions missing or truncated")

    # Print results
    print("\nPASSED CHECKS:")
    for check in checks_passed:
        print(f"  ✓ {check}")

    if checks_failed:
        print("\nFAILED CHECKS:")
        for check in checks_failed:
            print(f"  ✗ {check}")
    else:
        print("\nFAILED CHECKS:")
        print("  None - All checks passed!")

    # Final verdict
    print("\n" + "="*80)
    if len(checks_passed) >= 2 and not checks_failed:
        print("VERDICT: Parts field contains FULL, UNSANITIZED data ✓")
    elif len(checks_passed) > len(checks_failed):
        print("VERDICT: Parts field appears mostly complete, minor issues detected")
    else:
        print("VERDICT: Parts field may be sanitized or incomplete ✗")
    print("="*80 + "\n")


if __name__ == "__main__":
    try:
        verify_parts_integrity()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
