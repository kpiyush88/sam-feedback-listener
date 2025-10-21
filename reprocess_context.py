#!/usr/bin/env python3
"""
Reprocess messages for a specific context_id to populate interaction tracking
"""

import os
import json
import glob
from pathlib import Path
from dotenv import load_dotenv
from supabase_uploader import SupabaseUploader
from message_parser import MessageParser

load_dotenv()

# Target context ID
CONTEXT_ID = "web-session-f83e50f894164583b5a3afb33aacf241"
MESSAGES_DIR = "./messages-DirectAgent"

def main():
    print(f"Reprocessing messages for context_id: {CONTEXT_ID}")
    print("=" * 60)

    # Initialize uploader and parser
    uploader = SupabaseUploader()
    parser = MessageParser()

    # Find all JSON files with this context_id
    all_files = glob.glob(f"{MESSAGES_DIR}/*.json")
    matching_files = []

    for file_path in all_files:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                # Check if contextId matches in payload
                params = data.get('payload', {}).get('params', {})
                result = data.get('payload', {}).get('result', {})

                context_id = None
                if params:
                    msg = params.get('message', {})
                    context_id = msg.get('contextId')
                if not context_id and result:
                    context_id = result.get('contextId')

                if context_id == CONTEXT_ID:
                    matching_files.append(file_path)
        except Exception as e:
            continue

    # Sort by timestamp in filename
    matching_files.sort()

    print(f"Found {len(matching_files)} messages for this context")
    print("=" * 60)

    # Process each file
    success_count = 0
    error_count = 0

    for idx, file_path in enumerate(matching_files, 1):
        try:
            # Parse message
            parsed = parser.parse_message_file(file_path)

            # Upload with interaction tracking
            result = uploader.upload_message(parsed)

            if 'error' in result:
                print(f"❌ [{idx}/{len(matching_files)}] Error: {result['error']}")
                error_count += 1
            else:
                interaction_id = result.get('interaction_id', 'N/A')
                is_new = result.get('message_is_new', False)
                status = "NEW" if is_new else "EXISTS"
                print(f"✅ [{idx}/{len(matching_files)}] {status} | Interaction: {interaction_id} | {Path(file_path).name}")
                success_count += 1

        except Exception as e:
            print(f"❌ [{idx}/{len(matching_files)}] Exception: {e} | {Path(file_path).name}")
            error_count += 1

    print("=" * 60)
    print(f"Summary:")
    print(f"  Total processed: {len(matching_files)}")
    print(f"  Success: {success_count}")
    print(f"  Errors: {error_count}")
    print("=" * 60)

if __name__ == "__main__":
    main()
