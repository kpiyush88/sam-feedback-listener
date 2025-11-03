#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase Uploader for SAM Agent Messages
Ultra-simple uploader that stores raw payload with no parsing
"""

import os
from typing import Dict, Any

try:
    from supabase import create_client, Client  # type: ignore
except ImportError:
    class Client:  # type: ignore
        pass
    def create_client(url: str, key: str):  # type: ignore
        raise ImportError("supabase package not installed. Install with 'pip install supabase'.")


class SupabaseUploader:
    """Ultra-simple uploader - stores only raw payload and metadata"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None):
        """Initialize Supabase client"""
        self.url = supabase_url or os.getenv("SUPABASE_URL")
        self.key = supabase_key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be provided")

        self.client: Client = create_client(self.url, self.key)

    def upload_message(self, message_obj: Dict[str, Any]) -> Dict[str, str]:
        """
        Upload a message object to Supabase messages table.
        Stores only: raw_payload, topic, user_context_raw, message_id, created_at

        Args:
            message_obj: Message object with 'metadata' and 'payload' keys

        Returns:
            Dict with status ('success' or 'error')
        """
        try:
            metadata = message_obj.get('metadata', {})
            payload = message_obj.get('payload', {})

            # Prepare minimal record - only raw data, no parsing
            record = {
                'topic': metadata.get('topic'),
                'raw_payload': payload,
                'user_context_raw': metadata.get('user_properties')
            }

            # Insert into messages table
            self.client.table('messages').insert(record).execute()

            return {'status': 'success'}

        except Exception as e:
            return {'status': 'error', 'error': str(e)}
