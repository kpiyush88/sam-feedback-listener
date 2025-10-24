#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAM Listener with Supabase Integration
Downloads messages as JSON files AND uploads them to Supabase for analysis
"""

import sys
import io
import json
import os
import re
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock
from dotenv import load_dotenv
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.topic_subscription import TopicSubscription
from solace.messaging.receiver.message_receiver import MessageHandler, InboundMessage
from solace.messaging.config.transport_security_strategy import TLS

from message_parser import MessageParser
from supabase_uploader import SupabaseUploader

# Load environment variables from .env file
load_dotenv()

# Set stdout encoding to UTF-8 for Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Configure logging
def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """
    Setup logging configuration

    Args:
        log_dir: Directory for log files

    Returns:
        Configured logger
    """
    # Create logs directory if it doesn't exist
    Path(log_dir).mkdir(exist_ok=True)

    # Create log filename with timestamp
    log_filename = f"sam_listener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_filepath = Path(log_dir) / log_filename

    # Configure logging format
    log_format = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(name)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # Create logger
    logger = logging.getLogger('SAMListener')
    logger.setLevel(logging.DEBUG)

    # File handler (DEBUG level - everything)
    file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Console handler (INFO level - less verbose)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {log_filepath}")

    return logger

# Initialize logger
logger = setup_logging()


class TopicFilter:
    """Handles topic filtering using Solace wildcard patterns"""

    def __init__(self, filter_patterns: List[str], log_matches: bool = True):
        """
        Initialize with list of filter patterns

        Args:
            filter_patterns: List of patterns supporting Solace wildcards (> and *)
            log_matches: Whether to log when topics match filter patterns
        """
        self.filter_patterns = filter_patterns
        self.log_matches = log_matches
        if filter_patterns:
            logger.info(f"Initialized TopicFilter with {len(filter_patterns)} pattern(s): {filter_patterns}")
        else:
            logger.info("Initialized TopicFilter with no filter patterns (all topics will be processed)")

    def matches(self, topic: str) -> bool:
        """
        Check if a topic matches any filter pattern

        Args:
            topic: The topic string to check

        Returns:
            True if topic matches any filter pattern, False otherwise
        """
        for filter_pattern in self.filter_patterns:
            # Convert Solace wildcard pattern to regex
            # '>' matches one or more levels (everything after this point)
            # '*' matches exactly one level

            # First replace wildcards with placeholders before escaping
            pattern = filter_pattern.replace('>', '<<<WILDCARD_GT>>>')
            pattern = pattern.replace('*', '<<<WILDCARD_STAR>>>')

            # Escape special regex characters
            pattern = re.escape(pattern)

            # Replace placeholders with regex equivalents
            # '>' means match everything from this point
            pattern = pattern.replace('<<<WILDCARD_GT>>>', '.*')
            # '*' means match one level (anything except '/')
            pattern = pattern.replace('<<<WILDCARD_STAR>>>', '[^/]+')

            # Match the entire string
            if re.fullmatch(pattern, topic):
                if self.log_matches:
                    logger.debug(f"Topic '{topic}' matched filter pattern '{filter_pattern}'")
                return True

        return False


class MessageFileWriter:
    """Handles writing messages to JSON files"""

    def __init__(self, output_dir: Path):
        """
        Initialize the file writer

        Args:
            output_dir: Directory where JSON files will be saved
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)
        logger.info(f"Initialized MessageFileWriter with output directory: {self.output_dir}")

    def write(self, message_obj: Dict[str, Any], agent_id: str, timestamp: datetime) -> Path:
        """
        Write message object to JSON file

        Args:
            message_obj: Message dictionary to write
            agent_id: Agent ID for filename
            timestamp: Timestamp for filename

        Returns:
            Path to written file
        """
        # Generate filename with timestamp and agent ID
        # Format: msg_<agent_id>_<timestamp>.json
        filename = f"msg_{agent_id}_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.json"
        filepath = self.output_dir / filename

        logger.debug(f"Writing message to file: {filename}")

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(message_obj, f, indent=2, ensure_ascii=False)

            logger.debug(f"Successfully wrote message to: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"Failed to write message to {filepath}: {e}", exc_info=True)
            raise


class PayloadExtractor:
    """Extracts and processes message payloads"""

    @staticmethod
    def extract(message: InboundMessage) -> Any:
        """
        Extract payload from Solace message

        Args:
            message: Inbound message from Solace

        Returns:
            Extracted payload (JSON, string, or binary)
        """
        # Try to get payload as string first
        payload_str = message.get_payload_as_string()

        if payload_str:
            return PayloadExtractor._process_string_payload(payload_str)
        else:
            # Handle binary payload
            payload_bytes = message.get_payload_as_bytes()
            return PayloadExtractor._process_binary_payload(payload_bytes)

    @staticmethod
    def _process_string_payload(payload_str: str) -> Any:
        """Process string payload"""
        try:
            # Try to parse as JSON
            return json.loads(payload_str)
        except json.JSONDecodeError:
            # If not JSON, store as plain string
            return payload_str

    @staticmethod
    def _process_binary_payload(payload_bytes: bytes) -> Any:
        """Process binary payload"""
        if not payload_bytes:
            return None

        try:
            # Try to decode as UTF-8 text
            decoded_str = payload_bytes.decode('utf-8')
            try:
                # Try to parse as JSON
                return json.loads(decoded_str)
            except json.JSONDecodeError:
                # If not JSON, store as plain string
                return decoded_str
        except UnicodeDecodeError:
            # If not valid UTF-8, store as hex
            return {
                "binary_data": payload_bytes.hex(),
                "note": "Binary payload converted to hex (not valid UTF-8)"
            }


class UploadStatistics:
    """Tracks upload statistics (thread-safe)"""

    def __init__(self):
        self.total = 0
        self.successful = 0
        self.failed = 0
        self._lock = Lock()  # Thread-safe counter updates

    def record_success(self) -> None:
        """Record a successful upload (thread-safe)"""
        with self._lock:
            self.total += 1
            self.successful += 1

    def record_failure(self) -> None:
        """Record a failed upload (thread-safe)"""
        with self._lock:
            self.total += 1
            self.failed += 1

    def get_success_rate(self) -> float:
        """Get success rate as percentage"""
        with self._lock:
            if self.total == 0:
                return 0.0
            return (self.successful / self.total) * 100

    def print_stats(self) -> None:
        """Print upload statistics"""
        with self._lock:
            print(f"\n{'='*60}")
            print("Supabase Upload Statistics:")
            print(f"Total attempts: {self.total}")
            print(f"Successful: {self.successful}")
            print(f"Failed: {self.failed}")
            if self.total > 0:
                success_rate = (self.successful / self.total) * 100
                print(f"Success rate: {success_rate:.1f}%")
            print(f"{'='*60}\n")


class FeedbackMessageHandler(MessageHandler):
    """Handler for processing received messages and uploading to Supabase"""

    def __init__(
        self,
        output_dir: str = "messages",
        filter_topics: Optional[List[str]] = None,
        enable_supabase: bool = True,
        log_filtered_topics: bool = True,
        max_workers: int = 5
    ):
        """
        Initialize the message handler

        Args:
            output_dir: Directory where JSON files will be saved
            filter_topics: List of topics to exclude from saving
            enable_supabase: Whether to upload to Supabase
            log_filtered_topics: Whether to log/print filtered topic messages
            max_workers: Maximum number of parallel workers for processing
        """
        logger.info(f"Initializing FeedbackMessageHandler (Supabase: {enable_supabase}, Workers: {max_workers}, Log Filtered: {log_filtered_topics})")

        self.message_count = 0
        self.enable_supabase = enable_supabase
        self.log_filtered_topics = log_filtered_topics

        # Initialize components
        self.file_writer = MessageFileWriter(Path(output_dir))
        self.topic_filter = TopicFilter(filter_topics or [], log_matches=log_filtered_topics)
        self.payload_extractor = PayloadExtractor()
        self.upload_stats = UploadStatistics()

        # Thread pool executor for parallel processing
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="MessageProcessor")
        self.max_workers = max_workers
        self.queue_warning_threshold = 50  # Warn when queue has 50+ pending tasks
        self.queue_critical_threshold = 100  # Critical when queue has 100+ pending tasks
        logger.info(f"Thread pool executor initialized with {max_workers} workers")

        # Initialize Supabase uploader if enabled
        self.uploader: Optional[SupabaseUploader] = None
        self.parser: Optional[MessageParser] = None

        if self.enable_supabase:
            try:
                logger.info("Initializing Supabase uploader...")
                self.uploader = SupabaseUploader()
                self.parser = MessageParser()
                logger.info("Supabase uploader initialized successfully")
                print("Supabase uploader initialized successfully")
            except Exception as e:
                logger.error(f"Could not initialize Supabase uploader: {e}", exc_info=True)
                print(f"Warning: Could not initialize Supabase uploader: {e}")
                print("Continuing without Supabase upload...")
                self.enable_supabase = False
        else:
            logger.info("Supabase upload disabled")

    def check_queue_status(self) -> Dict[str, Any]:
        """
        Check the status of the thread pool executor queue

        Returns:
            Dictionary with queue status information
        """
        try:
            # Access the internal work queue
            pending_tasks = self.executor._work_queue.qsize()

            status = {
                'pending_tasks': pending_tasks,
                'max_workers': self.max_workers,
                'status': 'normal'
            }

            # Determine status level
            if pending_tasks >= self.queue_critical_threshold:
                status['status'] = 'critical'
                logger.error(f"🚨 CRITICAL: Thread pool queue heavily backed up with {pending_tasks} pending tasks!")
                print(f"⚠️  WARNING: Processing queue has {pending_tasks} pending messages (CRITICAL)")
            elif pending_tasks >= self.queue_warning_threshold:
                status['status'] = 'warning'
                logger.warning(f"⚠️  Thread pool queue backed up: {pending_tasks} pending tasks")
                print(f"⚠️  WARNING: Processing queue has {pending_tasks} pending messages")
            elif pending_tasks > 0:
                logger.debug(f"Thread pool queue: {pending_tasks} pending tasks")

            return status

        except Exception as e:
            logger.error(f"Failed to check queue status: {e}")
            return {'error': str(e), 'status': 'unknown'}

    def on_message(self, message: InboundMessage) -> None:
        """Called when a message is received - processes file write and upload in parallel"""
        self.message_count += 1

        # Check queue status every 10 messages
        if self.message_count % 10 == 0:
            self.check_queue_status()

        # Extract message details
        topic = message.get_destination_name()
        timestamp = datetime.now()

        # Check if topic should be filtered
        if self.topic_filter.matches(topic):
            if self.log_filtered_topics:
                logger.info(f"Message #{self.message_count} received on topic: {topic}")
                logger.info(f"Message #{self.message_count} filtered out (matches filter pattern)")
                self._print_filtered_message(topic)
            # If log_filtered_topics is False, silently skip (no logging at all)
            return

        logger.info(f"Message #{self.message_count} received on topic: {topic}")

        # Extract payload
        logger.debug(f"Extracting payload for message #{self.message_count}")
        payload_data = self.payload_extractor.extract(message)

        # Extract agent ID from topic
        agent_id = self._extract_agent_id(topic)
        logger.debug(f"Message #{self.message_count} - Agent ID: {agent_id}")

        # Extract user properties
        user_properties = self._extract_user_properties(message)

        # Create message object
        message_obj = self._create_message_object(
            topic, agent_id, timestamp, payload_data, message, user_properties
        )

        logger.info(f"Message #{self.message_count} - Submitting file write and Supabase upload tasks")

        # Process file write and Supabase upload in parallel
        # Submit both tasks to the thread pool executor
        file_future = self.executor.submit(
            self._write_to_file,
            message_obj, agent_id, timestamp
        )

        supabase_future = None
        if self.enable_supabase and self.uploader and self.parser:
            supabase_future = self.executor.submit(
                self._upload_to_supabase,
                message_obj
            )
            logger.debug(f"Message #{self.message_count} - Supabase upload task submitted")
        else:
            logger.debug(f"Message #{self.message_count} - Supabase upload skipped (disabled)")

        # Wait for file write to complete and print success message
        try:
            filepath = file_future.result()  # Block until file write completes
            logger.info(f"Message #{self.message_count} - File write completed: {filepath}")
            self._print_success_message(
                topic, agent_id, message, user_properties,
                payload_data, filepath, supabase_future
            )
        except Exception as e:
            logger.error(f"Message #{self.message_count} - Processing failed: {e}", exc_info=True)
            self._print_error_message(topic, payload_data, e)

    def _write_to_file(self, message_obj: Dict[str, Any], agent_id: str, timestamp: datetime) -> Path:
        """Write message to file (runs in thread pool)"""
        return self.file_writer.write(message_obj, agent_id, timestamp)

    def _extract_agent_id(self, topic: str) -> str:
        """Extract agent ID from topic (last part after the last /)"""
        topic_parts = topic.split('/')
        return topic_parts[-1] if topic_parts else "unknown"

    def _extract_user_properties(self, message: InboundMessage) -> Dict[str, Any]:
        """Extract user properties from message"""
        try:
            if hasattr(message, 'get_properties'):
                props = message.get_properties()
                if props and isinstance(props, dict):
                    return props
        except Exception:
            pass
        return {}

    def _create_message_object(
        self,
        topic: str,
        agent_id: str,
        timestamp: datetime,
        payload_data: Any,
        message: InboundMessage,
        user_properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create message object with metadata"""
        return {
            "metadata": {
                "topic": topic,
                "feedback_id": agent_id,
                "timestamp": timestamp.isoformat(),
                "message_number": self.message_count,
                "sender_id": message.get_sender_id() if hasattr(message, 'get_sender_id') else None,
                "correlation_id": message.get_correlation_id() if hasattr(message, 'get_correlation_id') else None,
                "user_properties": user_properties if user_properties else None
            },
            "payload": payload_data
        }

    def _upload_to_supabase(self, message_obj: Dict[str, Any]) -> Dict[str, Any]:
        """Upload message to Supabase with retry logic (runs in thread pool)"""
        message_id = message_obj.get('metadata', {}).get('message_number', 'unknown')
        topic = message_obj.get('metadata', {}).get('topic', 'unknown')

        logger.debug(f"Starting Supabase upload for message #{message_id}")

        # Retry configuration
        max_retries = 3
        retry_delay = 0.5  # Start with 500ms

        for attempt in range(max_retries):
            try:
                logger.debug(f"Parsing message #{message_id} (attempt {attempt + 1}/{max_retries})")
                parsed = self.parser.parse_message(message_obj)

                logger.debug(f"Uploading parsed message #{message_id} to Supabase (context_id: {parsed.context_id}, message_id: {parsed.message_id})")
                result = self.uploader.upload_message(parsed)

                if 'error' in result:
                    # Extract context_id and message_id from parsed message for better error reporting
                    context_id = parsed.context_id if parsed and parsed.context_id else 'unknown'
                    db_message_id = parsed.message_id if parsed and parsed.message_id else 'unknown'

                    # Retry on any error if attempts remaining
                    if attempt < max_retries - 1:
                        logger.warning(f"Upload error on message #{message_id} (attempt {attempt + 1}/{max_retries}): {result['error']}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        # Final attempt failed
                        logger.error(f"Supabase upload failed for message #{message_id}: {result['error']} (context_id: {context_id[:16] if len(context_id) > 16 else context_id}..., message_id: {db_message_id[:16] if len(db_message_id) > 16 else db_message_id}...)")
                        self.upload_stats.record_failure()
                        return result
                else:
                    # Success!
                    if attempt > 0:
                        logger.info(f"Supabase upload successful for message #{message_id} after {attempt + 1} attempts (DB message_id: {result.get('message_id', 'N/A')}, is_new: {result.get('message_is_new', 'N/A')})")
                    else:
                        logger.info(f"Supabase upload successful for message #{message_id} (DB message_id: {result.get('message_id', 'N/A')}, is_new: {result.get('message_is_new', 'N/A')})")
                    self.upload_stats.record_success()
                    return result

            except Exception as e:
                # Extract context for error logging from parsed message
                context_id = 'unknown'
                db_message_id = 'unknown'
                try:
                    if 'parsed' in locals():
                        context_id = parsed.context_id if parsed.context_id else 'unknown'
                        db_message_id = parsed.message_id if parsed.message_id else 'unknown'
                except:
                    pass

                # Retry on any exception if attempts remaining
                if attempt < max_retries - 1:
                    logger.warning(f"Exception during upload on message #{message_id} (attempt {attempt + 1}/{max_retries}): {e}, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    # Final attempt failed
                    logger.error(f"Exception during Supabase upload for message #{message_id}: {e} (context_id: {context_id[:16] if len(context_id) > 16 else context_id}..., message_id: {db_message_id[:16] if len(db_message_id) > 16 else db_message_id}...)", exc_info=True)
                    self.upload_stats.record_failure()
                    return {'error': str(e)}

        # Should never reach here, but just in case
        logger.error(f"All retry attempts exhausted for message #{message_id}")
        self.upload_stats.record_failure()
        return {'error': 'All retry attempts exhausted'}

    def _print_filtered_message(self, topic: str) -> None:
        """Print filtered message info"""
        print(f"\n{'='*60}")
        print(f"Message #{self.message_count} Received (FILTERED - Not Saved)")
        print(f"Topic: {topic}")
        print(f"Reason: Topic matches filter pattern")
        print(f"{'='*60}\n")

    def _print_success_message(
        self,
        topic: str,
        agent_id: str,
        message: InboundMessage,
        user_properties: Dict[str, Any],
        payload_data: Any,
        filepath: Path,
        supabase_future: Optional[Future] = None
    ) -> None:
        """Print successful message processing info"""
        print(f"\n{'='*60}")
        print(f"Message #{self.message_count} Received and Saved!")
        print(f"Topic: {topic}")
        print(f"Agent ID: {agent_id}")

        if hasattr(message, 'get_correlation_id') and message.get_correlation_id():
            print(f"Correlation ID: {message.get_correlation_id()}")

        print(f"Saved to: {filepath}")

        # Check Supabase upload result if it was submitted
        if supabase_future:
            try:
                # Non-blocking check if completed, otherwise show "in progress"
                if supabase_future.done():
                    result = supabase_future.result()
                    if 'error' in result:
                        print(f"⚠️  Supabase upload failed: {result['error']}")
                    else:
                        print(f"✓ Uploaded to Supabase (Conv: {result.get('conversation_id', 'N/A')[:8]}...)")
                else:
                    print(f"⏳ Supabase upload in progress...")
            except Exception as e:
                print(f"⚠️  Supabase upload error: {e}")

        print(f"{'='*60}\n")

    def _print_error_message(self, topic: str, payload_data: Any, error: Exception) -> None:
        """Print error message info"""
        print(f"Error saving message to JSON: {error}")
        print(f"\n{'='*60}")
        print(f"Message #{self.message_count} Received (Save Failed)")
        print(f"Topic: {topic}")
        print(f"Error: {error}")
        print(f"{'='*60}\n")

    def print_stats(self) -> None:
        """Print upload statistics"""
        if self.enable_supabase:
            logger.info("Printing upload statistics...")
            self.upload_stats.print_stats()

    def shutdown(self) -> None:
        """Shutdown the executor and wait for all tasks to complete"""
        logger.info("Shutting down executor and waiting for background tasks to complete...")
        print("Waiting for all background tasks to complete...")

        # Check queue status before shutdown
        queue_status = self.check_queue_status()
        if queue_status.get('pending_tasks', 0) > 0:
            print(f"Processing {queue_status['pending_tasks']} remaining queued messages...")

        self.executor.shutdown(wait=True)
        logger.info("All background tasks completed")
        print("All background tasks completed.")


class SolaceConfig:
    """Configuration for Solace connection"""

    def __init__(self):
        self.broker_host = os.getenv("SOLACE_HOST")
        self.vpn_name = os.getenv("SOLACE_VPN")
        self.username = os.getenv("SOLACE_USERNAME")
        self.password = os.getenv("SOLACE_PASSWORD")
        self.topic_subscription = os.getenv("SOLACE_TOPIC")
        self.output_dir = os.getenv("OUTPUT_DIR", "messages")
        self.enable_supabase = os.getenv("ENABLE_SUPABASE", "true").lower() == "true"
        self.log_filtered_topics = os.getenv("LOG_FILTERED_TOPICS", "true").lower() == "true"

        # Load filter topics (comma-separated list)
        filter_topics_str = os.getenv("FILTER_TOPICS", "")
        self.filter_topics = [topic.strip() for topic in filter_topics_str.split(",") if topic.strip()]

    def validate(self) -> None:
        """Validate required configuration"""
        logger.info("Validating Solace configuration...")

        required_vars = {
            "SOLACE_HOST": self.broker_host,
            "SOLACE_VPN": self.vpn_name,
            "SOLACE_USERNAME": self.username,
            "SOLACE_PASSWORD": self.password,
            "SOLACE_TOPIC": self.topic_subscription
        }

        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        logger.info(f"Configuration validated - Host: {self.broker_host}, VPN: {self.vpn_name}, Topic: {self.topic_subscription}")

    def to_broker_properties(self) -> Dict[str, str]:
        """Convert to broker properties dict"""
        return {
            "solace.messaging.transport.host": self.broker_host,
            "solace.messaging.service.vpn-name": self.vpn_name,
            "solace.messaging.authentication.scheme.basic.username": self.username,
            "solace.messaging.authentication.scheme.basic.password": self.password,
        }


class SolaceListener:
    """Main listener class that connects to Solace and receives messages"""

    def __init__(self, config: SolaceConfig):
        """
        Initialize the listener

        Args:
            config: Solace configuration
        """
        self.config = config
        self.messaging_service: Optional[MessagingService] = None
        self.direct_receiver = None
        self.message_handler: Optional[FeedbackMessageHandler] = None

    def connect(self) -> None:
        """Connect to Solace broker"""
        logger.info(f"Building messaging service for {self.config.broker_host}...")

        # Build the messaging service with TLS
        self.messaging_service = MessagingService.builder() \
            .from_properties(self.config.to_broker_properties()) \
            .with_transport_security_strategy(TLS.create().without_certificate_validation()) \
            .build()

        logger.info(f"Connecting to {self.config.broker_host}...")
        print(f"Connecting to {self.config.broker_host}...")
        self.messaging_service.connect()
        logger.info("Connected successfully to Solace broker")
        print("Connected successfully!")

    def setup_receiver(self) -> None:
        """Set up message receiver"""
        logger.info("Setting up message receiver...")
        print("Setting up message receiver...")
        self.direct_receiver = self.messaging_service.create_direct_message_receiver_builder().build()

        # Start the receiver
        logger.info("Starting message receiver...")
        self.direct_receiver.start()
        logger.info("Message receiver started successfully")
        print("Message receiver started.")

    def subscribe(self) -> None:
        """Subscribe to topic"""
        topic = TopicSubscription.of(self.config.topic_subscription)

        logger.info(f"Subscribing to topic: {self.config.topic_subscription}")
        print(f"Subscribing to topic: {self.config.topic_subscription}")
        self.direct_receiver.add_subscription(topic)
        logger.info(f"Successfully subscribed to topic: {self.config.topic_subscription}")

        # Set up message handler
        logger.info("Setting up message handler...")
        self.message_handler = FeedbackMessageHandler(
            output_dir=self.config.output_dir,
            filter_topics=self.config.filter_topics,
            enable_supabase=self.config.enable_supabase,
            log_filtered_topics=self.config.log_filtered_topics
        )
        self.direct_receiver.receive_async(self.message_handler)
        logger.info("Message handler registered for async message reception")

    def print_status(self) -> None:
        """Print listener status"""
        print("\n" + "="*60)
        print("Listening for messages on SAM agent topics...")
        print(f"Topic pattern: {self.config.topic_subscription}")
        print(f"Saving messages to: ./{self.config.output_dir}/")
        if self.config.enable_supabase:
            print(f"Uploading to Supabase: ENABLED")
        if self.config.filter_topics:
            print(f"Filtered topics (not saved): {', '.join(self.config.filter_topics)}")
            print(f"Log filtered topics: {'ENABLED' if self.config.log_filtered_topics else 'DISABLED'}")
        print("Press Ctrl+C to exit")
        print("="*60 + "\n")

    def run(self) -> None:
        """Run the listener (blocks indefinitely)"""
        logger.info("Listener is now running and waiting for messages...")
        try:
            # Use a loop with timeout to allow Ctrl+C to work
            import threading
            event = threading.Event()
            while not event.wait(timeout=1.0):
                pass
        except KeyboardInterrupt:
            # Re-raise to be caught by main()
            raise

    def cleanup(self) -> None:
        """Clean up resources"""
        logger.info("Starting cleanup process...")
        print("\nCleaning up resources...")

        if self.message_handler:
            # Shutdown executor and wait for background tasks
            logger.info("Shutting down message handler...")
            self.message_handler.shutdown()
            # Print statistics after all tasks complete
            self.message_handler.print_stats()

        if self.direct_receiver and self.direct_receiver.is_running():
            logger.info("Terminating direct receiver...")
            self.direct_receiver.terminate()
            logger.info("Direct receiver terminated")
            print("Direct receiver terminated.")

        if self.messaging_service and self.messaging_service.is_connected():
            logger.info("Disconnecting from Solace broker...")
            self.messaging_service.disconnect()
            logger.info("Disconnected from broker")
            print("Disconnected from broker.")

        logger.info("Shutdown complete")
        print("Shutdown complete.")


def main():
    """Main function to connect and listen to Solace topic"""

    logger.info("=" * 60)
    logger.info("SAM Listener Starting...")
    logger.info("=" * 60)

    # Load and validate configuration
    try:
        logger.info("Loading configuration from environment variables...")
        config = SolaceConfig()
        config.validate()

        print("Initializing Solace messaging service...")
        print(f"Configuration loaded from environment variables")
        print(f"Supabase integration: {'ENABLED' if config.enable_supabase else 'DISABLED'}")
        logger.info(f"Supabase integration: {'ENABLED' if config.enable_supabase else 'DISABLED'}")

        # Create and run listener
        logger.info("Creating SolaceListener instance...")
        listener = SolaceListener(config)

        try:
            listener.connect()
            listener.setup_receiver()
            listener.subscribe()
            listener.print_status()
            listener.run()

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt (Ctrl+C)")
            print("\n\nShutting down...")

        except Exception as e:
            logger.error(f"Error occurred during listener operation: {e}", exc_info=True)
            print(f"Error occurred: {e}")
            sys.exit(1)

        finally:
            listener.cleanup()

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print(f"Configuration error: {e}")
        print("Please create a .env file or set these environment variables.")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"Unexpected error: {e}")
        sys.exit(1)

    logger.info("SAM Listener terminated successfully")


if __name__ == "__main__":
    main()
