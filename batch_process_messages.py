#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch Process Messages
Process existing message files and upload them to Supabase
"""

import sys
import json
from pathlib import Path
from dotenv import load_dotenv
from supabase_uploader import SupabaseUploader

load_dotenv()


def main():
    """Batch process messages from a directory"""
    if len(sys.argv) < 2:
        print("Usage: python batch_process_messages.py <directory>")
        print("\nExample:")
        print("  python batch_process_messages.py \"messages/message 2\"")
        sys.exit(1)

    directory = sys.argv[1]
    directory_path = Path(directory)

    if not directory_path.exists():
        print(f"Error: Directory '{directory}' does not exist")
        sys.exit(1)

    if not directory_path.is_dir():
        print(f"Error: '{directory}' is not a directory")
        sys.exit(1)

    print(f"Batch processing messages from: {directory}")
    print("="*60)

    # Initialize uploader
    uploader = SupabaseUploader()

    # Process all JSON files
    stats = uploader.batch_upload_from_directory(directory, pattern="**/*.json")

    # Print results
    print("\n" + "="*60)
    print("BATCH PROCESSING COMPLETE")
    print("="*60)
    print(f"Total files processed: {stats['total_files']}")
    print(f"Successful uploads: {stats['successful']}")
    print(f"Failed uploads: {stats['failed']}")

    if stats['total_files'] > 0:
        success_rate = (stats['successful'] / stats['total_files']) * 100
        print(f"Success rate: {success_rate:.1f}%")

    if stats['errors']:
        print("\nErrors encountered:")
        for error in stats['errors'][:10]:  # Show first 10 errors
            print(f"  - {error['file']}: {error['error']}")

    # Show a sample of successful mappings if available
    if stats.get('samples'):
        print("\nSample inserted rows (UUIDs):")
        for sample in stats['samples'][:5]:
            print(json.dumps(sample, indent=2))

        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more errors")

    print("="*60)


if __name__ == "__main__":
    main()
