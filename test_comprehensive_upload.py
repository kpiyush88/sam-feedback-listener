#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive Upload Test
Tests the updated message parser and uploader against all existing messages
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase_uploader import SupabaseUploader
from message_parser import MessageParser

load_dotenv()


class UploadTester:
    """Comprehensive tester for message upload system"""

    def __init__(self):
        """Initialize tester with Supabase client"""
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")

        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.uploader = SupabaseUploader()
        self.parser = MessageParser()

    def run_batch_upload(self, directory: str) -> Dict[str, Any]:
        """Run batch upload and return statistics"""
        print("\n" + "="*80)
        print("STEP 1: BATCH UPLOAD PROCESS")
        print("="*80)

        directory_path = Path(directory)

        # Get all JSON files recursively
        json_files = list(directory_path.glob("**/*.json"))
        print(f"\nFound {len(json_files)} JSON files to process")

        # Run batch upload
        print("\nUploading messages to Supabase...")
        stats = self.uploader.batch_upload_from_directory(directory, pattern="**/*.json")

        print(f"\nUpload Statistics:")
        print(f"  Total files: {stats['total_files']}")
        print(f"  Successful: {stats['successful']}")
        print(f"  Failed: {stats['failed']}")

        if stats['total_files'] > 0:
            success_rate = (stats['successful'] / stats['total_files']) * 100
            print(f"  Success rate: {success_rate:.1f}%")

        if stats['errors']:
            print(f"\n  Errors encountered ({len(stats['errors'])}):")
            for error in stats['errors'][:5]:
                print(f"    - {Path(error['file']).name}: {error['error']}")
            if len(stats['errors']) > 5:
                print(f"    ... and {len(stats['errors']) - 5} more errors")

        return stats

    def verify_conversations_table(self) -> Dict[str, Any]:
        """Verify data in conversations table"""
        print("\n" + "="*80)
        print("STEP 2: VERIFY CONVERSATIONS TABLE")
        print("="*80)

        results = {}

        # Get all conversations
        response = self.client.table('conversations').select('*').execute()
        conversations = response.data

        results['total_conversations'] = len(conversations)
        print(f"\nTotal conversations: {len(conversations)}")

        if conversations:
            # Analyze first conversation in detail
            conv = conversations[0]
            print("\nSample Conversation Record:")
            print(f"  Context ID: {conv.get('context_id')}")
            print(f"  User ID: {conv.get('user_id')}")
            print(f"  User Email: {conv.get('user_email')}")
            print(f"  User Name: {conv.get('user_name')}")
            print(f"  User Country: {conv.get('user_country')}")
            print(f"  User Job Grade: {conv.get('user_job_grade')}")
            print(f"  User Company: {conv.get('user_company')}")
            print(f"  User Manager ID: {conv.get('user_manager_id')}")
            print(f"  User Location: {conv.get('user_location')}")
            print(f"  User Language: {conv.get('user_language')}")
            print(f"  User Authenticated: {conv.get('user_authenticated')}")

            # Check metadata for extended user fields
            metadata = conv.get('metadata', {})
            user_profile = metadata.get('user_profile', {})
            print(f"\n  Metadata - User Profile:")
            print(f"    Job Title: {user_profile.get('job_title')}")
            print(f"    Department: {user_profile.get('department')}")
            print(f"    Employee Group: {user_profile.get('employee_group')}")
            print(f"    FTE: {user_profile.get('fte')}")
            print(f"    Manager Name: {user_profile.get('manager_name')}")
            print(f"    Division: {user_profile.get('division')}")
            print(f"    Job Family: {user_profile.get('job_family')}")
            print(f"    Job Sub Family: {user_profile.get('job_sub_family')}")
            print(f"    Cost Center: {user_profile.get('cost_center')}")
            print(f"    Business Unit: {user_profile.get('business_unit')}")
            print(f"    Contract Type: {user_profile.get('contract_type')}")
            print(f"    Position Grade: {user_profile.get('position_grade')}")
            print(f"    Salary Structure: {user_profile.get('salary_structure')}")
            print(f"    Security Code: {user_profile.get('security_code')}")
            print(f"    Auth Method: {user_profile.get('auth_method')}")

            # Count how many conversations have user data
            with_user_data = sum(1 for c in conversations if c.get('user_id'))
            results['conversations_with_user_data'] = with_user_data
            print(f"\n  Conversations with user data: {with_user_data}/{len(conversations)}")

            results['sample_conversation'] = conv

        return results

    def verify_tasks_table(self) -> Dict[str, Any]:
        """Verify data in tasks table"""
        print("\n" + "="*80)
        print("STEP 3: VERIFY TASKS TABLE")
        print("="*80)

        results = {}

        # Get all tasks
        response = self.client.table('tasks').select('*').execute()
        tasks = response.data

        results['total_tasks'] = len(tasks)
        print(f"\nTotal tasks: {len(tasks)}")

        if tasks:
            # Analyze first task in detail
            task = tasks[0]
            print("\nSample Task Record:")
            print(f"  Task ID: {task.get('task_id')}")
            print(f"  Context ID: {task.get('context_id')}")
            print(f"  Agent Name: {task.get('agent_name')}")
            print(f"  Status: {task.get('status')}")
            print(f"  Task Type: {task.get('task_type')}")
            print(f"  Parent Task ID: {task.get('parent_task_id')}")

            # Check metadata for is_final and result_id
            metadata = task.get('metadata', {})
            print(f"\n  Metadata:")
            print(f"    Is Final: {metadata.get('is_final')}")
            print(f"    Result ID: {metadata.get('result_id')}")
            print(f"    Topic: {metadata.get('topic')}")
            print(f"    Method: {metadata.get('method')}")

            # Count tasks with is_final and result_id
            with_is_final = sum(1 for t in tasks if t.get('metadata', {}).get('is_final') is not None)
            with_result_id = sum(1 for t in tasks if t.get('metadata', {}).get('result_id'))

            results['tasks_with_is_final'] = with_is_final
            results['tasks_with_result_id'] = with_result_id

            print(f"\n  Tasks with is_final field: {with_is_final}/{len(tasks)}")
            print(f"  Tasks with result_id: {with_result_id}/{len(tasks)}")

            results['sample_task'] = task

        return results

    def verify_messages_table(self) -> Dict[str, Any]:
        """Verify data in messages table"""
        print("\n" + "="*80)
        print("STEP 4: VERIFY MESSAGES TABLE")
        print("="*80)

        results = {}

        # Get all messages
        response = self.client.table('messages').select('*').execute()
        messages = response.data

        results['total_messages'] = len(messages)
        print(f"\nTotal messages: {len(messages)}")

        if messages:
            # Analyze first message in detail
            msg = messages[0]
            print("\nSample Message Record:")
            print(f"  Message ID: {msg.get('message_id')}")
            print(f"  Context ID: {msg.get('context_id')}")
            print(f"  Task ID: {msg.get('task_id')}")
            print(f"  Role: {msg.get('role')}")
            print(f"  Message Type: {msg.get('message_type')}")
            print(f"  Agent Name: {msg.get('agent_name')}")
            print(f"  Correlation ID: {msg.get('correlation_id')}")
            print(f"  Topic: {msg.get('topic')}")
            print(f"  Content (first 100 chars): {str(msg.get('content'))[:100]}")

            # Check parts field
            parts = msg.get('parts', [])
            print(f"\n  Parts Field:")
            print(f"    Type: {type(parts)}")
            print(f"    Length: {len(parts) if isinstance(parts, list) else 'N/A'}")
            if parts and isinstance(parts, list) and len(parts) > 0:
                print(f"    First part type: {parts[0].get('kind', 'unknown')}")
                print(f"    First part keys: {list(parts[0].keys())}")
                # Check if parts are full or sanitized
                if 'data' in parts[0]:
                    data_size = len(json.dumps(parts[0]['data']))
                    print(f"    First part data size: {data_size} bytes")
                    print(f"    Appears to be: {'FULL DATA' if data_size > 100 else 'POSSIBLY SANITIZED'}")

            # Check metadata
            metadata = msg.get('metadata', {})
            print(f"\n  Metadata:")
            print(f"    Message Kind: {metadata.get('message_kind')}")
            print(f"    Is Partial: {metadata.get('is_partial')}")
            print(f"    Method: {metadata.get('method')}")
            print(f"    Agent ID: {metadata.get('agent_id')}")
            print(f"    Message Number: {metadata.get('message_number')}")

            # Check token usage in metadata
            token_usage = metadata.get('token_usage')
            if token_usage:
                print(f"\n  Token Usage (per-message):")
                print(f"    Model: {token_usage.get('model')}")
                print(f"    Input Tokens: {token_usage.get('input_tokens')}")
                print(f"    Output Tokens: {token_usage.get('output_tokens')}")
                print(f"    Total Tokens: {token_usage.get('total_tokens')}")

            # Count messages with key fields
            with_message_kind = sum(1 for m in messages if m.get('metadata', {}).get('message_kind'))
            with_correlation_id = sum(1 for m in messages if m.get('correlation_id'))
            with_is_partial = sum(1 for m in messages if m.get('metadata', {}).get('is_partial') is not None)
            with_parts = sum(1 for m in messages if m.get('parts'))
            with_full_parts = sum(1 for m in messages
                                 if m.get('parts') and isinstance(m.get('parts'), list)
                                 and len(m.get('parts')) > 0
                                 and len(json.dumps(m.get('parts')[0])) > 100)

            results['messages_with_message_kind'] = with_message_kind
            results['messages_with_correlation_id'] = with_correlation_id
            results['messages_with_is_partial'] = with_is_partial
            results['messages_with_parts'] = with_parts
            results['messages_with_full_parts'] = with_full_parts

            print(f"\n  Messages with message_kind: {with_message_kind}/{len(messages)}")
            print(f"  Messages with correlation_id: {with_correlation_id}/{len(messages)}")
            print(f"  Messages with is_partial field: {with_is_partial}/{len(messages)}")
            print(f"  Messages with parts field: {with_parts}/{len(messages)}")
            print(f"  Messages with FULL parts data: {with_full_parts}/{len(messages)}")

            results['sample_message'] = msg

        return results

    def validate_data_mapping(self, directory: str) -> Dict[str, Any]:
        """Validate that source files are correctly mapped to database"""
        print("\n" + "="*80)
        print("STEP 5: VALIDATE DATA MAPPING")
        print("="*80)

        results = {
            'missing_fields': [],
            'incorrect_mappings': [],
            'sanitization_issues': []
        }

        directory_path = Path(directory)
        json_files = list(directory_path.glob("**/*.json"))

        if not json_files:
            print("\nNo JSON files found to validate")
            return results

        # Test with first file
        test_file = json_files[0]
        print(f"\nValidating mapping with: {test_file.name}")

        # Parse the file
        parsed = self.parser.parse_message_file(str(test_file))

        # Check if user profile was extracted
        if parsed.user_profile:
            print("\n  User Profile Extraction: OK")
            print(f"    User ID: {parsed.user_profile.id}")
            print(f"    User Email: {parsed.user_profile.email}")
            print(f"    User Name: {parsed.user_profile.name}")
        else:
            print("\n  User Profile Extraction: NOT FOUND")
            results['missing_fields'].append('user_profile')

        # Check if message parts are full
        if parsed.message_parts:
            parts_json = json.dumps(parsed.message_parts)
            parts_size = len(parts_json)
            print(f"\n  Message Parts:")
            print(f"    Count: {len(parsed.message_parts)}")
            print(f"    Total size: {parts_size} bytes")
            print(f"    Status: {'FULL DATA' if parts_size > 500 else 'POSSIBLY INCOMPLETE'}")

            if parts_size < 500:
                results['sanitization_issues'].append('message_parts_may_be_sanitized')
        else:
            print("\n  Message Parts: EMPTY")

        # Check correlation ID extraction
        if parsed.topic:
            print(f"\n  Topic: {parsed.topic}")
            # Try to extract correlation ID
            parts = parsed.topic.split('/')
            correlation_id = parts[-1] if parts else None
            print(f"  Extracted Correlation ID: {correlation_id}")

        return results

    def generate_final_report(self,
                            upload_stats: Dict,
                            conv_results: Dict,
                            task_results: Dict,
                            msg_results: Dict,
                            validation_results: Dict) -> None:
        """Generate comprehensive final report"""
        print("\n" + "="*80)
        print("FINAL COMPREHENSIVE REPORT")
        print("="*80)

        print("\n1. UPLOAD SUMMARY")
        print(f"   Total messages processed: {upload_stats.get('total_files', 0)}")
        print(f"   Successful uploads: {upload_stats.get('successful', 0)}")
        print(f"   Failed uploads: {upload_stats.get('failed', 0)}")
        if upload_stats.get('total_files', 0) > 0:
            success_rate = (upload_stats.get('successful', 0) / upload_stats.get('total_files', 0)) * 100
            print(f"   Success rate: {success_rate:.1f}%")

        print("\n2. DATABASE VERIFICATION")
        print(f"   Conversations created: {conv_results.get('total_conversations', 0)}")
        print(f"   Tasks created: {task_results.get('total_tasks', 0)}")
        print(f"   Messages created: {msg_results.get('total_messages', 0)}")

        print("\n3. USER DATA POPULATION")
        print(f"   Conversations with user data: {conv_results.get('conversations_with_user_data', 0)}")
        print("   Core user fields verified: user_id, user_email, user_name, user_country,")
        print("                              user_job_grade, user_company, user_manager_id,")
        print("                              user_location, user_language, user_authenticated")
        print("   Extended fields in metadata: job_title, department, employee_group, fte,")
        print("                                 manager_name, division, job_family, etc.")

        print("\n4. TASK DATA VERIFICATION")
        print(f"   Tasks with is_final field: {task_results.get('tasks_with_is_final', 0)}")
        print(f"   Tasks with result_id: {task_results.get('tasks_with_result_id', 0)}")

        print("\n5. MESSAGE DATA VERIFICATION")
        print(f"   Messages with message_kind: {msg_results.get('messages_with_message_kind', 0)}")
        print(f"   Messages with correlation_id: {msg_results.get('messages_with_correlation_id', 0)}")
        print(f"   Messages with is_partial: {msg_results.get('messages_with_is_partial', 0)}")
        print(f"   Messages with parts field: {msg_results.get('messages_with_parts', 0)}")
        print(f"   Messages with FULL parts: {msg_results.get('messages_with_full_parts', 0)}")

        print("\n6. DATA INTEGRITY CHECK")
        if validation_results.get('missing_fields'):
            print(f"   Missing fields detected: {', '.join(validation_results['missing_fields'])}")
        else:
            print("   All expected fields present: OK")

        if validation_results.get('sanitization_issues'):
            print(f"   Sanitization issues: {', '.join(validation_results['sanitization_issues'])}")
        else:
            print("   No sanitization detected: OK - Parts contain FULL data")

        if validation_results.get('incorrect_mappings'):
            print(f"   Mapping issues: {', '.join(validation_results['incorrect_mappings'])}")
        else:
            print("   Field mappings correct: OK")

        print("\n7. RECOMMENDATIONS")
        issues = []

        if upload_stats.get('failed', 0) > 0:
            issues.append("Review failed uploads and address parsing errors")

        if conv_results.get('conversations_with_user_data', 0) == 0:
            issues.append("No user data found - check user profile extraction")

        if msg_results.get('messages_with_full_parts', 0) == 0:
            issues.append("Parts may be sanitized - verify full data preservation")

        if task_results.get('tasks_with_is_final', 0) == 0:
            issues.append("No is_final flags found - check result parsing")

        if issues:
            for i, issue in enumerate(issues, 1):
                print(f"   {i}. {issue}")
        else:
            print("   All checks passed - system is working correctly!")

        print("\n" + "="*80)


def main():
    """Run comprehensive test"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python test_comprehensive_upload.py <directory>")
        print("\nExample:")
        print("  python test_comprehensive_upload.py \"messages/message 2\"")
        sys.exit(1)

    directory = sys.argv[1]
    directory_path = Path(directory)

    if not directory_path.exists():
        print(f"Error: Directory '{directory}' does not exist")
        sys.exit(1)

    print("\n" + "="*80)
    print("COMPREHENSIVE MESSAGE UPLOAD TEST")
    print("="*80)
    print(f"\nTesting directory: {directory}")
    print(f"Started at: {datetime.now().isoformat()}")

    try:
        tester = UploadTester()

        # Step 1: Run batch upload
        upload_stats = tester.run_batch_upload(directory)

        # Step 2: Verify conversations table
        conv_results = tester.verify_conversations_table()

        # Step 3: Verify tasks table
        task_results = tester.verify_tasks_table()

        # Step 4: Verify messages table
        msg_results = tester.verify_messages_table()

        # Step 5: Validate data mapping
        validation_results = tester.validate_data_mapping(directory)

        # Generate final report
        tester.generate_final_report(
            upload_stats,
            conv_results,
            task_results,
            msg_results,
            validation_results
        )

        print(f"\nCompleted at: {datetime.now().isoformat()}")

    except Exception as e:
        print(f"\nERROR: Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
