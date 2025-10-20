# Testing Guide for SAM Message Upload System

This guide explains the testing scripts and how to use them.

---

## Test Scripts Overview

### 1. `test_comprehensive_upload.py` - Main Test Runner
**Purpose:** Comprehensive end-to-end test of the entire upload system

**What it does:**
- Runs batch upload on all JSON files in a directory
- Verifies data in all Supabase tables (conversations, tasks, messages)
- Checks user profile population (core + extended fields)
- Validates task metadata (is_final, result_id)
- Validates message metadata (message_kind, is_partial, correlation_id)
- Confirms parts data integrity (no sanitization)
- Generates detailed report

**How to run:**
```bash
python test_comprehensive_upload.py "messages/message 2"
```

**Expected output:**
- Upload statistics (success/failure rates)
- Database verification results
- Field population coverage
- Final comprehensive report

---

### 2. `verify_database_samples.py` - Database Sample Viewer
**Purpose:** View sample records from each table to verify data completeness

**What it does:**
- Shows sample conversation with full user data
- Shows sample task with metadata
- Shows sample message with parts preview
- Displays database statistics (counts)
- Verifies user data population
- Checks task and message metadata
- Shows parts data statistics

**How to run:**
```bash
python verify_database_samples.py
```

**Expected output:**
- Sample JSON records from each table
- User profile data verification
- Token usage statistics
- Parts size analysis

---

### 3. `verify_parts_integrity.py` - Parts Data Integrity Checker
**Purpose:** Confirm that parts field contains FULL JSON data without sanitization

**What it does:**
- Finds message with largest parts
- Analyzes parts structure in detail
- Checks for complete function declarations
- Verifies system instructions are full (not truncated)
- Shows sample of actual parts JSON
- Runs integrity verification checks
- Provides final verdict on data preservation

**How to run:**
```bash
python verify_parts_integrity.py
```

**Expected output:**
- Parts size statistics
- Detailed analysis of parts structure
- Integrity check results (PASS/FAIL)
- Sample JSON showing data completeness
- Final verdict: "Parts field contains FULL, UNSANITIZED data ✓"

---

### 4. `batch_process_messages.py` - Simple Batch Uploader
**Purpose:** Upload messages without detailed testing/verification

**What it does:**
- Uploads all JSON files from a directory
- Shows basic success/failure statistics
- Displays first 10 errors if any

**How to run:**
```bash
python batch_process_messages.py "messages/message 2"
```

**Expected output:**
- Total files processed
- Successful/failed uploads
- Success rate percentage
- Error summary (if any)

---

## What Each Test Verifies

### User Profile Data
**Core Fields (10 fields in dedicated columns):**
- user_id
- user_email
- user_name
- user_country
- user_job_grade
- user_company
- user_manager_id
- user_location
- user_language
- user_authenticated

**Extended Fields (15 fields in metadata.user_profile):**
- job_title
- department
- employee_group
- fte
- manager_name
- division
- job_family
- job_sub_family
- cost_center
- business_unit
- contract_type
- position_grade
- salary_structure
- security_code
- auth_method

### Task Metadata
- is_final (boolean flag from result.final)
- result_id (ID from result.id)
- topic (original message topic)
- method (JSON-RPC method)
- token_usage (total, input, output, cached)
- model_used
- artifacts_produced

### Message Metadata
- message_kind (from params.message.kind or result.kind)
- is_partial (from parts data)
- correlation_id (extracted from topic)
- token_usage (per-message from llm_response parts)
- agent_id
- message_number
- function_call_id
- method

### Parts Data Integrity
**What we verify:**
- Parts are stored as complete JSON arrays
- Function declarations include full parameter schemas
- System instructions are complete (not truncated)
- Tool configurations are complete
- No data summarization
- No field sanitization
- All nested structures preserved

**Expected sizes:**
- Minimum: >100 bytes (even smallest parts)
- Maximum: 30,000+ bytes (large tool declarations)
- Average: ~8,000 bytes

---

## Test Results Summary

### Test Run: October 20, 2025

**Upload Results:**
- ✅ 23/23 files uploaded successfully (100%)
- ✅ 0 parsing errors
- ✅ 0 upload failures

**Database State:**
- ✅ 1 conversation created
- ✅ 2 tasks created
- ✅ 23 messages created
- ✅ 60 tool calls extracted

**User Data:**
- ✅ 10/10 core fields populated (100%)
- ✅ 15/15 extended fields populated (100%)
- ✅ 1/1 conversations with user data (100%)

**Task Metadata:**
- ✅ 2/2 tasks with is_final (100%)
- ✅ 1/2 tasks with result_id (50% - expected)

**Message Metadata:**
- ✅ 23/23 messages with message_kind (100%)
- ✅ 21/23 messages with correlation_id (91% - expected)
- ✅ 23/23 messages with is_partial (100%)
- ✅ 23/23 messages with parts (100%)

**Parts Integrity:**
- ✅ All parts >100 bytes (no sanitization)
- ✅ Complete function declarations verified
- ✅ Full system instructions verified (15,678 chars)
- ✅ Average parts size: 8,465 bytes
- ✅ Maximum parts size: 39,079 bytes

**Verdict:** ✅ PRODUCTION READY

---

## Common Issues and Troubleshooting

### Issue: "No JSON files found"
**Solution:** Check the directory path and ensure it contains .json files

### Issue: "SUPABASE_URL and SUPABASE_KEY must be set"
**Solution:** Create/update .env file with Supabase credentials:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

### Issue: "Parts field appears sanitized"
**Solution:** This should NOT happen with current code. If you see this:
1. Check message_parser.py line 284 - should say `parsed.message_parts`
2. Check supabase_uploader.py line 284 - should say `parsed.message_parts`
3. Verify no code is calling json.dumps() and truncating

### Issue: "User profile not found"
**Solution:** Check if source JSON has metadata.user_properties.a2aUserConfig.user_profile

---

## Quick Test Commands

```bash
# Run comprehensive test
python test_comprehensive_upload.py "messages/message 2"

# View database samples
python verify_database_samples.py

# Check parts integrity
python verify_parts_integrity.py

# Simple batch upload
python batch_process_messages.py "messages/message 2"
```

---

## Expected Test Duration

- Comprehensive upload test: ~10-15 seconds for 23 files
- Database sample verification: ~2-3 seconds
- Parts integrity check: ~2-3 seconds
- Simple batch upload: ~10-15 seconds for 23 files

---

## Test Data Location

- **Source Files:** `/messages/message 2/` (23 JSON files)
- **Database:** Supabase (remote)
- **Tables:** conversations, tasks, messages, tool_calls

---

## Next Steps After Testing

1. ✅ Review TEST_REPORT.md for detailed findings
2. ✅ Review TEST_SUMMARY.txt for quick overview
3. ✅ Monitor future uploads for consistency
4. ✅ Document any edge cases discovered
5. ✅ Use system in production

---

**Last Updated:** October 20, 2025
**Test Status:** ALL TESTS PASSING ✅
**System Status:** PRODUCTION READY ✅
