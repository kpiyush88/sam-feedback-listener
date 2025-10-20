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


class TopicFilter:
    """Handles topic filtering using Solace wildcard patterns"""

    def __init__(self, filter_patterns: List[str]):
        """
        Initialize with list of filter patterns

        Args:
            filter_patterns: List of patterns supporting Solace wildcards (> and *)
        """
        self.filter_patterns = filter_patterns

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

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(message_obj, f, indent=2, ensure_ascii=False)

        return filepath


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
        max_workers: int = 10
    ):
        """
        Initialize the message handler

        Args:
            output_dir: Directory where JSON files will be saved
            filter_topics: List of topics to exclude from saving
            enable_supabase: Whether to upload to Supabase
            max_workers: Maximum number of parallel workers for processing
        """
        self.message_count = 0
        self.enable_supabase = enable_supabase

        # Initialize components
        self.file_writer = MessageFileWriter(Path(output_dir))
        self.topic_filter = TopicFilter(filter_topics or [])
        self.payload_extractor = PayloadExtractor()
        self.upload_stats = UploadStatistics()

        # Thread pool executor for parallel processing
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="MessageProcessor")

        # Initialize Supabase uploader if enabled
        self.uploader: Optional[SupabaseUploader] = None
        self.parser: Optional[MessageParser] = None

        if self.enable_supabase:
            try:
                self.uploader = SupabaseUploader()
                self.parser = MessageParser()
                print("Supabase uploader initialized successfully")
            except Exception as e:
                print(f"Warning: Could not initialize Supabase uploader: {e}")
                print("Continuing without Supabase upload...")
                self.enable_supabase = False

    def on_message(self, message: InboundMessage) -> None:
        """Called when a message is received - processes file write and upload in parallel"""
        self.message_count += 1

        # Extract message details
        topic = message.get_destination_name()
        timestamp = datetime.now()

        # Check if topic should be filtered
        if self.topic_filter.matches(topic):
            self._print_filtered_message(topic)
            return

        # Extract payload
        payload_data = self.payload_extractor.extract(message)

        # Extract agent ID from topic
        agent_id = self._extract_agent_id(topic)

        # Extract user properties
        user_properties = self._extract_user_properties(message)

        # Create message object
        message_obj = self._create_message_object(
            topic, agent_id, timestamp, payload_data, message, user_properties
        )

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

        # Wait for file write to complete and print success message
        try:
            filepath = file_future.result()  # Block until file write completes
            self._print_success_message(
                topic, agent_id, message, user_properties,
                payload_data, filepath, supabase_future
            )
        except Exception as e:
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
        """Upload message to Supabase (runs in thread pool)"""
        try:
            parsed = self.parser.parse_message(message_obj)
            result = self.uploader.upload_message(parsed)

            if 'error' in result:
                self.upload_stats.record_failure()
            else:
                self.upload_stats.record_success()

            return result

        except Exception as e:
            self.upload_stats.record_failure()
            return {'error': str(e)}

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

        if user_properties:
            print(f"User Properties: {json.dumps(user_properties, indent=2)}")

        print(f"Saved to: {filepath}")
        print(f"Payload preview: {str(payload_data)[:200]}...")

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
        print(f"Payload: {payload_data}")
        print(f"{'='*60}\n")

    def print_stats(self) -> None:
        """Print upload statistics"""
        if self.enable_supabase:
            self.upload_stats.print_stats()

    def shutdown(self) -> None:
        """Shutdown the executor and wait for all tasks to complete"""
        print("Waiting for all background tasks to complete...")
        self.executor.shutdown(wait=True)
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

        # Load filter topics (comma-separated list)
        filter_topics_str = os.getenv("FILTER_TOPICS", "")
        self.filter_topics = [topic.strip() for topic in filter_topics_str.split(",") if topic.strip()]

    def validate(self) -> None:
        """Validate required configuration"""
        required_vars = {
            "SOLACE_HOST": self.broker_host,
            "SOLACE_VPN": self.vpn_name,
            "SOLACE_USERNAME": self.username,
            "SOLACE_PASSWORD": self.password,
            "SOLACE_TOPIC": self.topic_subscription
        }

        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

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
        # Build the messaging service with TLS
        self.messaging_service = MessagingService.builder() \
            .from_properties(self.config.to_broker_properties()) \
            .with_transport_security_strategy(TLS.create().without_certificate_validation()) \
            .build()

        print(f"Connecting to {self.config.broker_host}...")
        self.messaging_service.connect()
        print("Connected successfully!")

    def setup_receiver(self) -> None:
        """Set up message receiver"""
        print("Setting up message receiver...")
        self.direct_receiver = self.messaging_service.create_direct_message_receiver_builder().build()

        # Start the receiver
        self.direct_receiver.start()
        print("Message receiver started.")

    def subscribe(self) -> None:
        """Subscribe to topic"""
        topic = TopicSubscription.of(self.config.topic_subscription)

        print(f"Subscribing to topic: {self.config.topic_subscription}")
        self.direct_receiver.add_subscription(topic)

        # Set up message handler
        self.message_handler = FeedbackMessageHandler(
            output_dir=self.config.output_dir,
            filter_topics=self.config.filter_topics,
            enable_supabase=self.config.enable_supabase
        )
        self.direct_receiver.receive_async(self.message_handler)

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
        print("Press Ctrl+C to exit")
        print("="*60 + "\n")

    def run(self) -> None:
        """Run the listener (blocks indefinitely)"""
        import threading
        threading.Event().wait()

    def cleanup(self) -> None:
        """Clean up resources"""
        print("\nCleaning up resources...")

        if self.message_handler:
            # Shutdown executor and wait for background tasks
            self.message_handler.shutdown()
            # Print statistics after all tasks complete
            self.message_handler.print_stats()

        if self.direct_receiver and self.direct_receiver.is_running():
            self.direct_receiver.terminate()
            print("Direct receiver terminated.")

        if self.messaging_service and self.messaging_service.is_connected():
            self.messaging_service.disconnect()
            print("Disconnected from broker.")

        print("Shutdown complete.")


def main():
    """Main function to connect and listen to Solace topic"""

    # Load and validate configuration
    try:
        config = SolaceConfig()
        config.validate()

        print("Initializing Solace messaging service...")
        print(f"Configuration loaded from environment variables")
        print(f"Supabase integration: {'ENABLED' if config.enable_supabase else 'DISABLED'}")

        # Create and run listener
        listener = SolaceListener(config)

        try:
            listener.connect()
            listener.setup_receiver()
            listener.subscribe()
            listener.print_status()
            listener.run()

        except KeyboardInterrupt:
            print("\n\nShutting down...")

        except Exception as e:
            print(f"Error occurred: {e}")
            sys.exit(1)

        finally:
            listener.cleanup()

    except ValueError as e:
        print(f"Configuration error: {e}")
        print("Please create a .env file or set these environment variables.")
        sys.exit(1)


if __name__ == "__main__":
    main()
