"""
Supabase client for bot integration.
Handles writing bot trading data to Supabase database.
"""
import os
from typing import Optional
from supabase import create_client, Client
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_USER_ID = os.getenv("SUPABASE_USER_ID")
SUPABASE_ENABLED = os.getenv("SUPABASE_ENABLED", "False").lower() == "true"

# Initialize Supabase client
supabase: Optional[Client] = None

if SUPABASE_ENABLED and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Supabase client: {e}")
        supabase = None
else:
    logger.info("ℹ️  Supabase integration disabled")


def get_user_id() -> str:
    """Get the configured Supabase user ID."""
    return SUPABASE_USER_ID


def is_enabled() -> bool:
    """Check if Supabase integration is enabled and working."""
    return SUPABASE_ENABLED and supabase is not None


def safe_insert(table: str, data: dict) -> bool:
    """
    Safely insert data into Supabase table.
    Returns True if successful, False otherwise.
    Logs errors but doesn't raise exceptions to avoid breaking bot operation.
    """
    if not is_enabled():
        return False
    
    try:
        # Add user_id to data if not present
        if 'user_id' not in data:
            data['user_id'] = get_user_id()
        
        result = supabase.table(table).insert(data).execute()
        logger.debug(f"✅ Inserted into {table}: {data.get('token_symbol', data.get('name', 'record'))}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to insert into {table}: {e}")
        return False


def safe_update(table: str, data: dict, match_column: str, match_value: any) -> bool:
    """
    Safely update data in Supabase table.
    Returns True if successful, False otherwise.
    """
    if not is_enabled():
        return False
    
    try:
        result = supabase.table(table).update(data).eq(match_column, match_value).eq('user_id', get_user_id()).execute()
        logger.debug(f"✅ Updated {table} where {match_column}={match_value}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to update {table}: {e}")
        return False


def safe_upsert(table: str, data: dict, conflict_columns: list = None) -> bool:
    """
    Safely upsert (insert or update) data in Supabase table.
    Returns True if successful, False otherwise.
    """
    if not is_enabled():
        return False
    
    try:
        # Add user_id to data if not present
        if 'user_id' not in data:
            data['user_id'] = get_user_id()
        
        if conflict_columns:
            result = supabase.table(table).upsert(data, on_conflict=','.join(conflict_columns)).execute()
        else:
            result = supabase.table(table).upsert(data).execute()
        
        logger.debug(f"✅ Upserted into {table}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to upsert into {table}: {e}")
        return False


import time
from datetime import datetime, timezone

class SupabaseLogHandler(logging.Handler):
    """
    Custom logging handler that sends logs to Supabase 'logs' table.
    """
    def __init__(self, batch_size=10, flush_interval=5.0):
        super().__init__()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.buffer = []
        self.last_flush = time.time()
        
    def emit(self, record):
        if not is_enabled():
            return
            
        try:
            # Format the message
            msg = self.format(record)
            
            # Create log entry
            entry = {
                'level': record.levelname,
                'module': record.name,
                'message': msg,
                'created_at': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                'metadata': {
                    'filename': record.filename,
                    'lineno': record.lineno,
                    'funcName': record.funcName
                },
                'user_id': get_user_id()
            }
            
            self.buffer.append(entry)
            
            # Flush if buffer full or time elapsed
            if len(self.buffer) >= self.batch_size or (time.time() - self.last_flush) >= self.flush_interval:
                self.flush()
                
        except Exception:
            self.handleError(record)
            
    def flush(self):
        """Send buffered logs to Supabase."""
        if not self.buffer or not is_enabled():
            return
            
        try:
            # Send batch insert
            supabase.table('logs').insert(self.buffer).execute()
            self.buffer = []
            self.last_flush = time.time()
        except Exception as e:
            # Don't use logger here to avoid infinite recursion
            print(f"Failed to send logs to Supabase: {e}")
            # Keep buffer but remove oldest if too big to avoid memory leak
            if len(self.buffer) > 1000:
                self.buffer = self.buffer[-500:]
