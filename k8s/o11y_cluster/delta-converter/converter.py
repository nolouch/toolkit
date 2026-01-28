#!/usr/bin/env python3
"""
Delta Lake Converter for TiDB Logs
Converts JSON logs from MinIO to Delta Lake format
Compatible with diagnosis-query path structure
Uses dynamic schema inference (same as TiDB Cloud original method)
"""
import os
import sys
import json
import logging
import gzip
from datetime import datetime

import pandas as pd
import pyarrow as pa
from deltalake import write_deltalake
from s3fs import S3FileSystem

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeltaConverter:
    """Convert JSON logs to Delta Lake format using dynamic schema inference"""

    def __init__(self):
        self.minio_endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
        self.minio_access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        self.minio_secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        self.bucket_name = os.getenv("BUCKET_NAME", "tidb-logs")
        self.tenant_id = os.getenv("TENANT_ID", "default")
        self.cluster_id = os.getenv("CLUSTER_ID", "tc")

        # Initialize S3 filesystem for MinIO
        self.s3 = S3FileSystem(
            key=self.minio_access_key,
            secret=self.minio_secret_key,
            client_kwargs={
                "endpoint_url": self.minio_endpoint
            }
        )

        # Track processed files for incremental processing
        self.processed_files = set()
        self._load_processed_files()

    def _get_processed_files_path(self, log_type):
        """Get path for processed files tracking"""
        return f"{self.bucket_name}/deltalake/{self.tenant_id}/{self.cluster_id}/_processed_files_{log_type}.txt"

    def _load_processed_files(self):
        """Load list of already processed files"""
        for log_type in ['statement', 'slowlog']:
            path = self._get_processed_files_path(log_type)
            try:
                if self.s3.exists(path):
                    with self.s3.open(path, 'r') as f:
                        files = f.read().split('\n')
                        self.processed_files.update([f for f in files if f.strip()])
                    logger.info(f"Loaded {len(self.processed_files)} processed files for {log_type}")
            except Exception as e:
                logger.debug(f"No processed files tracking for {log_type}: {e}")

    def _save_processed_files(self, log_type, new_files):
        """Save newly processed files to tracking"""
        path = self._get_processed_files_path(log_type)
        try:
            content = '\n'.join(new_files) + '\n'
            # Append to existing file or create new
            if self.s3.exists(path):
                with self.s3.open(path, 'a') as f:
                    f.write(content)
            else:
                with self.s3.open(path, 'w') as f:
                    f.write(content)
            logger.info(f"Saved {len(new_files)} processed files for {log_type}")
        except Exception as e:
            logger.warning(f"Failed to save processed files: {e}")

    def _infer_column_type(self, series):
        """
        Infer column data type (same as TiDB Cloud parse_logs_to_delta.py)

        Args:
            series: pandas Series to infer type for

        Returns:
            pyarrow.DataType: Inferred data type
        """
        non_null = series.dropna()
        if len(non_null) == 0:
            return pa.string()

        # Check if contains list or dict types (already converted to JSON string)
        if any(isinstance(x, (list, dict)) for x in non_null):
            return pa.string()

        # Try to convert to integer
        try:
            if all(isinstance(x, (int, bool)) or
                 (isinstance(x, str) and x.replace('-', '').isdigit())
                 for x in non_null if x not in ['None', '']):
                return pa.int64()
        except (ValueError, TypeError):
            pass

        # Try to convert to float
        try:
            if all(isinstance(x, (int, float)) or self._is_float(x)
                   for x in non_null if x not in ['None', '']):
                return pa.float64()
        except (ValueError, TypeError):
            pass

        # Default to string type
        return pa.string()

    def _is_float(self, x):
        """Check if string represents a float value"""
        try:
            float(x)
            return True
        except (ValueError, TypeError):
            return False

    def _get_delta_schema(self, df):
        """
        Generate Delta Lake schema from DataFrame (same as TiDB Cloud generate_delta_data.py for statement)

        Args:
            df: Input DataFrame

        Returns:
            pa.Schema: PyArrow schema
        """
        schema_fields = []

        for column in df.columns:
            if column in ['processed_at', 'batch_num']:
                # Timestamp columns with UTC
                schema_fields.append(pa.field(column, pa.timestamp('us', tz='UTC'), nullable=True))
            elif column in ['summary_begin_time', 'summary_end_time']:
                # Converted from Unix timestamp to datetime (nanosecond precision)
                schema_fields.append(pa.field(column, pa.timestamp('ns', tz='UTC'), nullable=True))
            elif column in ['first_seen', 'last_seen']:
                # Convert ISO format string to timestamp if still string, otherwise timestamp
                if df[column].dtype == 'object' or df[column].dtype == 'string':
                    schema_fields.append(pa.field(column, pa.string(), nullable=True))
                else:
                    # Already converted to datetime (nanosecond precision)
                    schema_fields.append(pa.field(column, pa.timestamp('ns', tz='UTC'), nullable=True))
            elif column in ['is_internal', 'prepared', 'plan_in_cache', 'plan_in_binding']:
                # Boolean values
                schema_fields.append(pa.field(column, pa.bool_(), nullable=True))
            elif column in ['index_names', 'backoff_types', 'auth_users']:
                # Complex types, stored as JSON string
                schema_fields.append(pa.field(column, pa.string(), nullable=True))
            elif df[column].dtype in ['int64', 'int32']:
                # Integer types
                schema_fields.append(pa.field(column, pa.int64(), nullable=True))
            elif df[column].dtype in ['float64', 'float32']:
                # Float types
                schema_fields.append(pa.field(column, pa.float64(), nullable=True))
            else:
                # Default string type
                schema_fields.append(pa.field(column, pa.string(), nullable=True))

        return pa.schema(schema_fields)

    def _truncate_string(self, x, max_length=100):
        """Truncate string if too long"""
        if pd.isna(x) or not isinstance(x, str):
            return x
        s = str(x)
        if len(s) > max_length:
            return s[:max_length] + "..."
        return s

    def _parse_record(self, record):
        """
        Parse record and extract nested message JSON

        Args:
            record: Raw record dict

        Returns:
            Parsed dict (only message content)
        """
        # Parse message field if it's a JSON string
        if 'message' in record and isinstance(record['message'], str):
            try:
                message_data = json.loads(record['message'])
                # Return only message content
                return message_data
            except json.JSONDecodeError:
                # If message is not JSON, keep as is
                return record
        return record

    def _find_json_files(self, prefix):
        """
        Recursively find all log files under a prefix

        Args:
            prefix: S3 path prefix (e.g., 'tidb-logs/statement/')

        Returns:
            List of S3 file paths
        """
        files = []
        try:
            # Use find to recursively walk the directory
            for path in self.s3.find(prefix, prefix=''):
                if path.endswith('.json') or path.endswith('.json.gz') or path.endswith('.log.gz'):
                    files.append(path)
            logger.info(f"Found {len(files)} log files under {prefix}")
        except Exception as e:
            logger.error(f"Failed to list files under {prefix}: {e}")
        return files

    def convert_statements(self):
        """Convert statement logs to Delta Lake (same as TiDB Cloud logic)"""
        logger.info("Converting statement logs...")

        source_prefix = f"{self.bucket_name}/statement/"
        logger.info(f"Reading from: {source_prefix}")

        files = self._find_json_files(source_prefix)

        # Filter out already processed files (incremental processing)
        new_files = [f for f in files if f not in self.processed_files]
        logger.info(f"Found {len(files)} total files, {len(new_files)} new files to process")

        if not new_files:
            logger.info("No new statement files to process")
            return

        batch_size = 5  # Process 5 files per batch
        total_records = 0
        batch_num = 1

        # Process files in batches (same as TiDB Cloud)
        for i, file_path in enumerate(new_files):
            logger.info(f"Processing {file_path}...")
            batch_data = []

            try:
                with self.s3.open(file_path, 'rb') as f:
                    if file_path.endswith('.gz'):
                        with gzip.GzipFile(fileobj=f, mode='rb') as gz:
                            content = gz.read().decode('utf-8')
                    else:
                        content = f.read().decode('utf-8')

                    for line in content.split('\n'):
                        if line.strip():
                            try:
                                data = json.loads(line)
                                if isinstance(data, list):
                                    for item in data:
                                        parsed = self._parse_record(item)
                                        # Convert complex fields to JSON strings before flattening
                                        parsed = self._preprocess_complex_fields(parsed)
                                        flattened = self._flatten_dict(parsed)
                                        batch_data.append(flattened)
                                else:
                                    parsed = self._parse_record(data)
                                    # Convert complex fields to JSON strings before flattening
                                    parsed = self._preprocess_complex_fields(parsed)
                                    flattened = self._flatten_dict(parsed)
                                    batch_data.append(flattened)
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.debug(f"Failed to parse line: {e}")
                                continue
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")
                continue

            # Write batch immediately (same as TiDB Cloud)
            if batch_data:
                self._write_batch_to_delta(batch_data, "persisted_statements_summary", batch_num)
                total_records += len(batch_data)
                batch_num += 1

            # Save processed file immediately after successful write
            self._save_processed_files('statement', [file_path])
            logger.info(f"Marked as processed: {file_path}")

        logger.info(f"Conversion completed. Total records written: {total_records}")

    def convert_slowlogs(self):
        """Convert slow query logs to Delta Lake (same as TiDB Cloud logic)"""
        logger.info("Converting slow query logs...")

        source_prefix = f"{self.bucket_name}/slowlog/"
        logger.info(f"Reading from: {source_prefix}")

        files = self._find_json_files(source_prefix)

        # Filter out already processed files (incremental processing)
        new_files = [f for f in files if f not in self.processed_files]
        logger.info(f"Found {len(files)} total files, {len(new_files)} new files to process")

        if not new_files:
            logger.info("No new slowlog files to process")
            return

        batch_size = 5  # Process 5 files per batch
        total_records = 0
        batch_num = 1

        # Process files in batches (same as TiDB Cloud)
        for i, file_path in enumerate(new_files):
            logger.info(f"Processing {file_path}...")
            batch_data = []

            try:
                with self.s3.open(file_path, 'rb') as f:
                    if file_path.endswith('.gz'):
                        with gzip.GzipFile(fileobj=f, mode='rb') as gz:
                            content = gz.read().decode('utf-8')
                    else:
                        content = f.read().decode('utf-8')

                    for line in content.split('\n'):
                        if line.strip():
                            try:
                                data = json.loads(line)
                                if isinstance(data, list):
                                    for item in data:
                                        parsed = self._parse_record(item)
                                        # Convert complex fields to JSON strings before flattening
                                        parsed = self._preprocess_complex_fields(parsed)
                                        flattened = self._flatten_dict(parsed)
                                        batch_data.append(flattened)
                                else:
                                    parsed = self._parse_record(data)
                                    # Convert complex fields to JSON strings before flattening
                                    parsed = self._preprocess_complex_fields(parsed)
                                    flattened = self._flatten_dict(parsed)
                                    batch_data.append(flattened)
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.debug(f"Failed to parse line: {e}")
                                continue
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")
                continue

            # Write batch immediately (same as TiDB Cloud)
            if batch_data:
                self._write_batch_to_delta(batch_data, "slowlogs", batch_num)
                total_records += len(batch_data)
                batch_num += 1

            # Save processed file immediately after successful write
            self._save_processed_files('slowlog', [file_path])
            logger.info(f"Marked as processed: {file_path}")

        logger.info(f"Conversion completed. Total records written: {total_records}")

    def _get_existing_schema(self, table_path):
        """
        Get existing Delta Lake table schema

        Args:
            table_path: S3 path to Delta Lake table

        Returns:
            pa.Schema: Existing schema, or None if table doesn't exist
        """
        try:
            from deltalake import DeltaTable
            dt = DeltaTable(
                table_path,
                storage_options={
                    "AWS_ACCESS_KEY_ID": self.minio_access_key,
                    "AWS_SECRET_ACCESS_KEY": self.minio_secret_key,
                    "AWS_ENDPOINT_URL": self.minio_endpoint,
                    "AWS_REGION": "us-east-1",
                    "AWS_ALLOW_HTTP": "true"
                }
            )
            # Convert to PyArrow schema to access field names
            return dt.schema().to_pyarrow()
        except Exception:
            # Table doesn't exist yet
            return None

    def _flatten_dict(self, d, parent_key='', sep='.'):
        """Flatten nested dictionary"""
        if not isinstance(d, dict):
            # If not a dict, return as is
            return {'value': d}
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def _preprocess_complex_fields(self, d):
        """
        Preprocess complex fields to JSON strings before flattening.
        Fields like auth_users, backoff_types should remain as JSON strings.
        """
        if not isinstance(d, dict):
            return d

        processed = {}
        for k, v in d.items():
            if k in ['auth_users', 'backoff_types', 'index_names'] and isinstance(v, dict):
                # Convert these to JSON strings to prevent flattening
                if v:
                    processed[k] = json.dumps(v, ensure_ascii=False, default=str)
                else:
                    processed[k] = None
            else:
                processed[k] = v
        return processed

    def _write_batch_to_delta(self, batch_data, table_name, batch_num):
        """
        Write a batch of data to Delta Lake table (same as TiDB Cloud generate_delta_data.py)

        Args:
            batch_data: Batch data
            table_name: Table name
            batch_num: Batch number for progress display
        """
        try:
            # Preprocess data, convert list/dict to JSON strings (same as generate_delta_data.py)
            processed_data = []
            for item in batch_data:
                if item is None:
                    continue

                processed_item = {}
                for key, value in item.items():
                    if value is None:
                        processed_item[key] = None
                    elif isinstance(value, dict):
                        # Recursively process nested dictionaries and convert to JSON string
                        cleaned_dict = {k: v for k, v in value.items() if v is not None}
                        processed_item[key] = json.dumps(cleaned_dict, ensure_ascii=False, default=str) if cleaned_dict else None
                    elif isinstance(value, list):
                        # Handle None values in lists and convert to JSON string
                        cleaned_list = [v for v in value if v is not None]
                        processed_item[key] = json.dumps(cleaned_list, ensure_ascii=False, default=str) if cleaned_list else None
                    else:
                        processed_item[key] = value
                processed_data.append(processed_item)

            if not processed_data:
                logger.warning(f"Batch {batch_num} has no valid data, skipping")
                return

            # Convert to DataFrame
            df = pd.DataFrame(processed_data)

            # Time columns: convert Unix timestamp (int) to datetime, then rename
            # 'begin' and 'end' are Unix timestamps (seconds), need to convert to timestamp for DuckDB
            if 'begin' in df.columns:
                df['begin'] = pd.to_datetime(df['begin'], unit='s', errors='coerce')
                df = df.rename(columns={'begin': 'summary_begin_time'})
            if 'end' in df.columns:
                df['end'] = pd.to_datetime(df['end'], unit='s', errors='coerce')
                df = df.rename(columns={'end': 'summary_end_time'})

            # SQL text columns: match diagnosis service expected column names
            if 'normalized_sql' in df.columns:
                df = df.rename(columns={'normalized_sql': 'digest_text'})
            if 'sample_sql' in df.columns:
                df = df.rename(columns={'sample_sql': 'QUERY_SAMPLE_TEXT'})
            if 'prev_sql' in df.columns:
                df = df.rename(columns={'prev_sql': 'prev_sample_text'})

            # Plan columns: match diagnosis service expected column names
            if 'sample_plan' in df.columns:
                df = df.rename(columns={'sample_plan': 'plan'})
            if 'sample_binary_plan' in df.columns:
                df = df.rename(columns={'sample_binary_plan': 'binary_plan'})

            # Other columns: match diagnosis service expected column names
            if 'resource_group_name' in df.columns:
                df = df.rename(columns={'resource_group_name': 'resource_group'})
            if 'sum_num_cop_tasks' in df.columns:
                df = df.rename(columns={'sum_num_cop_tasks': 'sum_cop_task_num'})
            if 'max_prewrite_region_num' in df.columns:
                df = df.rename(columns={'max_prewrite_region_num': 'max_prewrite_regions'})

            # Request Unit (RU) columns: match diagnosis service expected column names
            if 'sum_rru' in df.columns:
                df = df.rename(columns={'sum_rru': 'AVG_REQUEST_UNIT_READ'})
            if 'sum_wru' in df.columns:
                df = df.rename(columns={'sum_wru': 'AVG_REQUEST_UNIT_WRITE'})
            if 'max_rru' in df.columns:
                df = df.rename(columns={'max_rru': 'MAX_REQUEST_UNIT_READ'})
            if 'max_wru' in df.columns:
                df = df.rename(columns={'max_wru': 'MAX_REQUEST_UNIT_WRITE'})
            if 'sum_ru_wait_duration' in df.columns:
                df = df.rename(columns={'sum_ru_wait_duration': 'AVG_QUEUED_RC_TIME'})
            if 'max_ru_wait_duration' in df.columns:
                df = df.rename(columns={'max_ru_wait_duration': 'MAX_QUEUED_RC_TIME'})

            # Special column renames: add "wait" suffix to match diagnosis service expectations
            if 'max_local_latch_time' in df.columns:
                df = df.rename(columns={'max_local_latch_time': 'max_local_latch_wait_time'})

            # Extract sample_user from auth_users dict (auth_users is like {"root": {}})
            if 'auth_users' in df.columns:
                # auth_users is stored as JSON string like '{"root": {}}'
                # Extract the first key as the user name
                def extract_first_user(auth_users_str):
                    try:
                        import json
                        if pd.notna(auth_users_str) and auth_users_str:
                            auth_dict = json.loads(auth_users_str)
                            if isinstance(auth_dict, dict) and auth_dict:
                                return list(auth_dict.keys())[0]
                    except:
                        pass
                    return None
                df['sample_user'] = df['auth_users'].apply(extract_first_user)

            # Convert timestamp strings to timestamp type for epoch() function compatibility
            # first_seen and last_seen are stored as ISO format strings, need to be timestamps
            if 'first_seen' in df.columns:
                df['first_seen'] = pd.to_datetime(df['first_seen'], errors='coerce')
            if 'last_seen' in df.columns:
                df['last_seen'] = pd.to_datetime(df['last_seen'], errors='coerce')

            # Calculate avg_* columns from sum_* columns (for diagnosis service compatibility)
            # avg_X = sum_X / exec_count
            if 'exec_count' in df.columns:
                exec_count = df['exec_count'].astype(float).replace(0, float('nan'))
                avg_columns_map = {
                    'sum_latency': 'avg_latency',
                    'sum_parse_latency': 'avg_parse_latency',
                    'sum_compile_latency': 'avg_compile_latency',
                    'sum_process_time': 'avg_process_time',
                    'sum_wait_time': 'avg_wait_time',
                    'sum_backoff_time': 'avg_backoff_time',
                    'sum_total_keys': 'avg_total_keys',
                    'sum_processed_keys': 'avg_processed_keys',
                    'sum_prewrite_time': 'avg_prewrite_time',
                    'sum_commit_time': 'avg_commit_time',
                    'sum_get_commit_ts_time': 'avg_get_commit_ts_time',
                    'sum_commit_backoff_time': 'avg_commit_backoff_time',
                    'sum_resolve_lock_time': 'avg_resolve_lock_time',
                    'sum_local_latch_time': 'avg_local_latch_wait_time',
                    'sum_write_keys': 'avg_write_keys',
                    'sum_write_size': 'avg_write_size',
                    'sum_prewrite_region_num': 'avg_prewrite_regions',
                    'sum_txn_retry': 'avg_txn_retry',
                    'sum_mem': 'avg_mem',
                    'sum_disk': 'avg_disk',
                    'sum_affected_rows': 'avg_affected_rows',
                    'sum_rocksdb_delete_skipped_count': 'avg_rocksdb_delete_skipped_count',
                    'sum_rocksdb_key_skipped_count': 'avg_rocksdb_key_skipped_count',
                    'sum_rocksdb_block_cache_hit_count': 'avg_rocksdb_block_cache_hit_count',
                    'sum_rocksdb_block_read_count': 'avg_rocksdb_block_read_count',
                    'sum_rocksdb_block_read_byte': 'avg_rocksdb_block_read_byte',
                    # Additional avg columns from MySQL STATEMENTS_SUMMARY
                    'sum_kv_total': 'avg_kv_time',
                    'sum_pd_total': 'avg_pd_time',
                    'sum_backoff_total': 'avg_backoff_total_time',
                    'sum_write_sql_resp_total': 'avg_write_sql_resp_time',
                    'sum_tidb_cpu': 'avg_tidb_cpu_time',
                    'sum_tikv_cpu': 'avg_tikv_cpu_time',
                    'sum_result_rows': 'avg_result_rows',
                }
                for sum_col, avg_col in avg_columns_map.items():
                    if sum_col in df.columns:
                        df[avg_col] = df[sum_col].astype(float) / exec_count

            # Add processing timestamp and batch number
            df['processed_at'] = datetime.now()
            df['batch_num'] = batch_num

            # Generate Delta Lake schema
            delta_schema = self._get_delta_schema(df)

            # Check existing table schema for merge info
            table_path = f"s3://{self.bucket_name}/deltalake/{self.tenant_id}/{self.cluster_id}/{table_name}"
            existing_schema = self._get_existing_schema(table_path)
            if existing_schema:
                existing_columns = set(existing_schema.names)
                new_columns = set(df.columns)
                added_columns = new_columns - existing_columns
                if added_columns:
                    logger.info(f"🔄 SCHEMA MERGE: Adding {len(added_columns)} new columns: {sorted(added_columns)}")
                    logger.info(f"   Existing columns: {len(existing_columns)}, New columns: {len(new_columns)}")
                else:
                    logger.info(f"✓ Schema compatible ({len(existing_columns)} columns)")
            else:
                logger.info(f"🆕 Creating new table with {len(df.columns)} columns")

            logger.info(f"Writing batch {batch_num} with {len(df)} records to {table_path}")

            # Convert to PyArrow Table
            pa_table = pa.Table.from_pandas(df, schema=delta_schema, preserve_index=False)

            # Write with retry logic for concurrent write conflicts
            max_retries = 3
            retry_delay = 1  # seconds

            for attempt in range(max_retries):
                try:
                    write_deltalake(
                        table_path,
                        pa_table,
                        mode="append",
                        schema_mode="merge",  # Merge schema to handle different columns across batches
                        storage_options={
                            "AWS_ACCESS_KEY_ID": self.minio_access_key,
                            "AWS_SECRET_ACCESS_KEY": self.minio_secret_key,
                            "AWS_ENDPOINT_URL": self.minio_endpoint,
                            "AWS_REGION": "us-east-1",
                            "AWS_ALLOW_HTTP": "true"
                        }
                    )
                    logger.info(f"✅ Batch {batch_num} write completed with {len(df.columns)} columns")
                    break
                except Exception as write_err:
                    if attempt < max_retries - 1 and "Metadata changed" in str(write_err):
                        # Retry on metadata conflict
                        wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Batch {batch_num}: Metadata conflict, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        import time
                        time.sleep(wait_time)
                    else:
                        raise

        except Exception as e:
            logger.error(f"Failed to write batch {batch_num}: {e}")
            raise

    def run(self):
        """Run the conversion process for all log types"""
        logger.info("Starting Delta Lake conversion with dynamic schema inference...")

        # Convert statements
        self.convert_statements()

        # Convert slowlogs
        self.convert_slowlogs()

        logger.info("Conversion completed successfully")


def main():
    """Main entry point"""
    converter = DeltaConverter()

    try:
        converter.run()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Conversion failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
