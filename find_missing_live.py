#!/usr/bin/env python3
"""Find the missing message between local files and Supabase (live query)."""

import os
import re
from supabase import create_client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase = create_client(supabase_url, supabase_key)

# Get Supabase message IDs
print("Fetching message IDs from Supabase...")
response = supabase.table("messages").select("message_id").execute()
supabase_ids = {record["message_id"] for record in response.data}
print(f"Supabase has {len(supabase_ids)} messages")

# Get local message IDs
local_ids = {}
messages_dir = "messages"

for filename in os.listdir(messages_dir):
    if filename.endswith(".json"):
        filepath = os.path.join(messages_dir, filename)
        with open(filepath, 'r') as f:
            content = f.read()
            # Use regex to find messageId values
            matches = re.findall(r'"messageId"\s*:\s*"([a-f0-9]{32})"', content)
            for message_id in matches:
                if message_id not in local_ids:  # Only store first occurrence
                    local_ids[message_id] = {
                        "filename": filename
                    }

print(f"Local folder has {len(local_ids)} messages")

# Find missing messages
missing_in_supabase = set(local_ids.keys()) - supabase_ids
missing_locally = supabase_ids - set(local_ids.keys())

if missing_in_supabase:
    print(f"\n{len(missing_in_supabase)} message(s) in local folder but NOT in Supabase:")
    for msg_id in sorted(missing_in_supabase):
        info = local_ids[msg_id]
        print(f"\n  Message ID: {msg_id}")
        print(f"  File: {info['filename']}")

if missing_locally:
    print(f"\n{len(missing_locally)} message(s) in Supabase but NOT in local folder:")
    for msg_id in sorted(missing_locally):
        print(f"  - {msg_id}")

if not missing_in_supabase and not missing_locally:
    print("\nAll messages match! No discrepancies found.")
