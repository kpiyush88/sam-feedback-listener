# SAM Feedback Listener - Refactored Architecture

A modular, high-performance SAM agent message listener with parallel processing and Supabase integration.

## Overview

This application listens to Solace message topics, saves messages as JSON files, and uploads them to Supabase for analytics - all in parallel for maximum performance.

## Key Features

✅ **Parallel Processing**: File writes and Supabase uploads happen concurrently using thread pools
✅ **Modular Architecture**: Clean separation of concerns with dedicated classes for each responsibility
✅ **Thread-Safe**: All shared resources are protected with proper locking mechanisms
✅ **Non-Blocking**: Messages are processed immediately without waiting for uploads to complete
✅ **Robust Error Handling**: Failures in one operation don't affect the other
✅ **Statistics Tracking**: Real-time monitoring of upload success rates

## Architecture

### Core Components

#### 1. Message Parser (`message_parser.py`)
- **MessageParser**: Main parser with pluggable payload parsers
- **PayloadParser** (ABC): Abstract base for different payload types
  - **ParamsParser**: Handles message params
  - **ResultParser**: Handles message results
- **StatusUpdateParser**: Processes status updates
- **TaskResultParser**: Processes task completions
- **UserProfileExtractor**: Extracts user profile data
- **MessageAnalyzer**: Analyzes parsed messages

#### 2. Supabase Uploader (`supabase_uploader.py`)
- **SupabaseUploader**: Main uploader coordinator
- **DatabaseCache**: Thread-safe caching of IDs
- **DataMapper**: Maps parsed messages to database schema
- **ConversationManager**: Manages conversation records
- **TaskManager**: Manages task records
- **MessageManager**: Manages message records
- **ToolCallManager**: Manages tool call records

#### 3. SAM Listener (`sam_listener_with_supabase.py`)
- **SolaceListener**: Main listener class
- **FeedbackMessageHandler**: Handles incoming messages with parallel processing
- **TopicFilter**: Filters messages by topic patterns
- **MessageFileWriter**: Writes JSON files
- **PayloadExtractor**: Extracts and processes payloads
- **UploadStatistics**: Thread-safe statistics tracking
- **SolaceConfig**: Configuration management

## Parallel Processing Flow

When a message arrives:

```
Message Received
    ├─→ [Thread Pool] Write to JSON file
    │   └─→ Returns filepath
    │
    └─→ [Thread Pool] Upload to Supabase
        ├─→ Parse message
        ├─→ Ensure conversation exists
        ├─→ Ensure task exists
        ├─→ Insert message
        ├─→ Insert tool calls
        └─→ Update statistics
```

**Both operations run in parallel** - the file write doesn't wait for Supabase, and vice versa.

### Thread Pool Configuration

- Default: 10 worker threads
- Configurable via `max_workers` parameter
- Threads are reused across messages for efficiency
- Graceful shutdown waits for all pending operations

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file with the following variables:

```env
# Solace Configuration
SOLACE_HOST=your_solace_host
SOLACE_VPN=your_vpn_name
SOLACE_USERNAME=your_username
SOLACE_PASSWORD=your_password
SOLACE_TOPIC=your_topic_pattern

# Supabase Configuration
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key

# Optional Settings
OUTPUT_DIR=messages
ENABLE_SUPABASE=true
FILTER_TOPICS=topic1,topic2
```

## Usage

### Real-Time Listener

Listen to Solace messages and process them in parallel:

```bash
python sam_listener_with_supabase.py
```

**Output:**
```
Message #1 Received and Saved!
Saved to: ./messages/msg_agent_123_20251020_150000.json
⏳ Supabase upload in progress...
```

### Batch Processing

Process existing message files:

```bash
python batch_process_messages.py "messages/message 2"
```

### Single Message Processing

Process a single message file:

```bash
python message_parser.py "messages/msg_agent_123.json"
```

## Database Schema

The application uses 4 main tables in Supabase:

1. **conversations**: Groups messages by context/session
2. **tasks**: Tracks main tasks and subtasks
3. **messages**: Individual messages with content
4. **tool_calls**: Function calls made by agents

See `supabase_schema.sql` for the complete schema.

## Performance Benefits

### Sequential (Old Approach)
```
Message 1: [Write 100ms] → [Upload 500ms] = 600ms
Message 2: [Write 100ms] → [Upload 500ms] = 600ms
Total: 1200ms for 2 messages
```

### Parallel (New Approach)
```
Message 1: [Write 100ms] ║ [Upload 500ms]
Message 2: [Write 100ms] ║ [Upload 500ms]
Total: ~500ms for 2 messages (60% faster!)
```

The file write completes in 100ms, while upload continues in the background.

## Error Handling

- **File write failure**: Message is logged, Supabase upload continues
- **Supabase upload failure**: File is still saved, error is tracked in statistics
- **Both operations are independent**: One failure doesn't affect the other

## Thread Safety

All shared resources are protected:
- ✓ Upload statistics use locks
- ✓ Database cache uses locks
- ✓ Message counter is thread-safe
- ✓ File writes are isolated per thread

## Testing

### Test Supabase Connection
```bash
python test_supabase_connection.py
```

### Test Message Parser
```bash
python message_parser.py "path/to/message.json"
```

### Test Supabase Uploader
```bash
python supabase_uploader.py "path/to/message.json"
```

## Project Structure

```
.
├── message_parser.py              # Modular message parsing
├── supabase_uploader.py           # Modular Supabase integration
├── sam_listener_with_supabase.py  # Main listener with parallel processing
├── batch_process_messages.py      # Batch processing utility
├── test_supabase_connection.py    # Connection test utility
├── supabase_schema.sql            # Database schema
├── requirements.txt               # Dependencies
├── .env                           # Configuration (not in git)
├── .gitignore                     # Git ignore rules
├── PGVECTOR_IMPLEMENTATION_PLAN.md # Vector database plan
└── messages/                      # Message storage (not in git)
```

## Class Diagram

```
FeedbackMessageHandler
    ├── MessageFileWriter
    ├── TopicFilter
    ├── PayloadExtractor
    ├── UploadStatistics
    ├── ThreadPoolExecutor (parallel processing)
    ├── SupabaseUploader
    │   ├── DatabaseCache
    │   ├── ConversationManager
    │   ├── TaskManager
    │   ├── MessageManager
    │   └── ToolCallManager
    └── MessageParser
        ├── ParamsParser
        ├── ResultParser
        ├── StatusUpdateParser
        ├── TaskResultParser
        └── UserProfileExtractor
```

## Monitoring

The system provides real-time feedback:

```
Message #5 Received and Saved!
Saved to: ./messages/msg_agent_005.json
✓ Uploaded to Supabase (Conv: web-sess...)

Supabase Upload Statistics:
Total attempts: 5
Successful: 5
Failed: 0
Success rate: 100.0%
```

## Graceful Shutdown

On Ctrl+C:
1. Stop accepting new messages
2. Wait for all background tasks to complete
3. Print final statistics
4. Close connections
5. Exit cleanly

## Best Practices

1. **Monitor statistics**: Check success rates regularly
2. **Adjust thread pool size**: Based on message volume and network latency
3. **Handle backpressure**: If uploads are too slow, consider increasing `max_workers`
4. **Keep messages folder**: Don't delete JSON files - they're your backup

## Troubleshooting

### Slow processing
- Increase `max_workers` in FeedbackMessageHandler
- Check network latency to Supabase
- Verify database indexes exist

### Upload failures
- Check Supabase credentials in `.env`
- Verify tables exist (run `supabase_schema.sql`)
- Check network connectivity

### Memory issues
- Reduce `max_workers` to limit concurrent operations
- Monitor thread pool queue size
- Consider processing in smaller batches

## Future Enhancements

- [ ] Add retry logic for failed uploads
- [ ] Implement exponential backoff
- [ ] Add metrics dashboard
- [ ] Support batch inserts for efficiency
- [ ] Add message deduplication
- [ ] Implement rate limiting

## License

Internal use only.

## Recent Refactor Notes (2025-10-20)

The live production schema uses TEXT primary keys (not UUID row IDs). Adjustments performed:

1. Text Key Alignment
  - Primary keys: `conversations.context_id`, `tasks.task_id`, `messages.message_id`.
  - Foreign keys use these text identifiers directly; no UUID indirection.

2. Extended User Fields
  - Populated additional conversation columns (`user_id`, `user_company`, `user_location`, `user_language`, `user_authenticated`). Remaining profile depth kept in `metadata.user_profile`.

3. Metadata De-duplication
  - Removed redundant copies of `is_final`, `result_id`, `message_kind`, `is_partial`, token scalar fields from metadata when columns exist.
  - Cleanup SQL executed to strip duplicates from existing rows.

4. Token Usage Strategy
  - Per-message token counts stored in top-level columns when present; detailed breakdown retained under `metadata.token_usage` only when available.

5. Tool Calls
  - References: `tool_calls.message_id -> messages.message_id`, `tool_calls.task_id -> tasks.task_id` remain TEXT.

6. Tests Updated
  - `test_datamapper.py` reflects text-key mapping (context_id/task_id/message_id).

7. Audit Script
  - Added `audit_supabase.py` to report nulls and duplication; provides remediation SQL.

8. Auto Fallback for Missing messageId
  - Generates deterministic `auto-<taskId>-<unix_ts>` when absent.

### Running Mapping Tests
```powershell
python test_datamapper.py
```

### Batch Upload After Refactor
Use existing batch script (shows UUIDs in output if samples enabled):
```powershell
python batch_process_messages.py "messages/message 2"
```

### Adjusting Analytics Queries
When querying messages joined to conversations now use:
```sql
SELECT m.id AS message_row_uuid,
     m.message_id AS external_message_id,
     c.context_id,
     c.user_email,
     m.metadata->>'model_used' AS model_used
FROM messages m
JOIN conversations c ON m.conversation_id = c.id
WHERE c.context_id = 'ctx_001';
```

Refer to `supabase_schema.sql` if adding new top-level analytical columns; otherwise keep them in `metadata` for flexibility.
