#!/usr/bin/env python3
"""
Calculate Statement Coverage by Time Interval
 Compare DuckDB statement count with Prometheus tidb_executor_statement_total metric
 Group by 30-minute intervals for the past 3 hours
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
import duckdb
import requests

DEBUG_MODE = False


def get_duckdb_connection(minio_endpoint):
    """Create DuckDB connection with S3 secret"""
    con = duckdb.connect(":memory:")

    endpoint = minio_endpoint.replace("http://", "").replace("https://", "")
    if ":" in endpoint:
        host, port = endpoint.split(":")
    else:
        host = endpoint
        port = "9000"

    con.execute(f"""
        CREATE SECRET (
            TYPE S3,
            KEY_ID 'minioadmin',
            SECRET 'minioadmin',
            REGION 'us-east-1',
            ENDPOINT '{host}:{port}',
            USE_SSL 'false',
            URL_STYLE 'path'
        );
    """)

    return con


def query_duckdb_count(minio_endpoint, tenant_id, cluster_id, start_time, end_time):
    """Query statement count from DuckDB (Delta Lake)"""
    table_path = f"s3://tidb-logs/deltalake/{tenant_id}/{cluster_id}/persisted_statements_summary"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT COALESCE(SUM(exec_count), 0) as total_count
        FROM delta_scan('{table_path}')
        WHERE summary_begin_time <= to_timestamp('{end_time}')
          AND summary_end_time >= to_timestamp('{start_time}')
    """

    if DEBUG_MODE:
        print(f"DuckDB Query:\n{query}")

    try:
        result = con.execute(query).fetchone()
        return result[0] if result and result[0] else 0
    except Exception as e:
        print(f"Error querying DuckDB: {e}")
        return 0
    finally:
        con.close()


def query_duckdb_windows(minio_endpoint, tenant_id, cluster_id, start_time, end_time):
    """Query all time windows with detailed statistics from DuckDB (Delta Lake)"""
    table_path = f"s3://tidb-logs/deltalake/{tenant_id}/{cluster_id}/persisted_statements_summary"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT 
            extract(epoch from summary_begin_time)::BIGINT as win_start,
            extract(epoch from summary_end_time)::BIGINT as win_end,
            SUM(exec_count) as total_log_count,
            SUM(CASE WHEN digest != '' AND digest IS NOT NULL THEN exec_count ELSE 0 END) as normal_log_count,
            COUNT(DISTINCT CASE WHEN digest != '' AND digest IS NOT NULL THEN digest END) as num_digests,
            SUM(CASE WHEN digest IS NULL THEN 1 ELSE 0 END) as null_digest_rows,
            SUM(CASE WHEN digest IS NULL THEN exec_count ELSE 0 END) as null_digest_exec_count,
            SUM(CASE WHEN digest = '' THEN 1 ELSE 0 END) as others_digest_rows,
            SUM(CASE WHEN digest = '' THEN exec_count ELSE 0 END) as others_digest_exec_count
        FROM delta_scan('{table_path}')
        WHERE summary_begin_time <= to_timestamp('{end_time}')
          AND summary_end_time >= to_timestamp('{start_time}')
        GROUP BY win_start, win_end
        ORDER BY win_start DESC
    """

    if DEBUG_MODE:
        print(f"DuckDB Windows Query:\n{query}")

    try:
        results = con.execute(query).fetchall()
        return results
    except Exception as e:
        print(f"Error querying DuckDB windows: {e}")
        return []
    finally:
        con.close()


def query_duckdb_count_from_raw_logs(minio_endpoint, start_time, end_time):
    """Query statement count from raw log files"""
    base_path = "s3://tidb-logs/statement/**/*"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT COALESCE(SUM(exec_count), 0) as total_count
        FROM (
            SELECT 
                try_cast(json_extract(message, '$.exec_count') as BIGINT) as exec_count
            FROM read_json_auto('{base_path}')
            WHERE message LIKE '%digest%'
              AND (
                  (try_cast(json_extract(message, '$.begin') as BIGINT) <= {end_time}
                  AND try_cast(json_extract(message, '$.end') as BIGINT) >= {start_time})
                  OR
                  (try_cast(json_extract(message, '$.begin') as BIGINT) BETWEEN {start_time} AND {end_time})
              )
        )
    """

    if DEBUG_MODE:
        print(f"DuckDB Query (Raw Logs):\n{query}")

    try:
        result = con.execute(query).fetchone()
        return result[0] if result and result[0] else 0
    except Exception as e:
        print(f"Error querying raw logs: {e}")
        return 0
    finally:
        con.close()


def query_duckdb_windows_from_raw_logs(minio_endpoint, start_time, end_time):
    """Query all time windows with detailed statistics from raw log files"""
    base_path = "s3://tidb-logs/statement/**/*"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        WITH all_records AS (
            SELECT
                try_cast(json_extract(message, '$.begin') as BIGINT) as win_start,
                try_cast(json_extract(message, '$.end') as BIGINT) as win_end,
                try_cast(json_extract(message, '$.digest') as VARCHAR) as digest,
                try_cast(json_extract(message, '$.exec_count') as BIGINT) as exec_count
            FROM read_json_auto('{base_path}')
            WHERE message LIKE '%digest%'
              AND (
                  (try_cast(json_extract(message, '$.begin') as BIGINT) <= {end_time}
                  AND try_cast(json_extract(message, '$.end') as BIGINT) >= {start_time})
                  OR
                  (try_cast(json_extract(message, '$.begin') as BIGINT) BETWEEN {start_time} AND {end_time})
              )
        )
        SELECT
            win_start,
            win_end,
            SUM(exec_count) as total_log_count,
            SUM(CASE WHEN digest != '' AND digest IS NOT NULL THEN exec_count ELSE 0 END) as normal_log_count,
            COUNT(DISTINCT CASE WHEN digest != '' AND digest IS NOT NULL THEN digest END) as num_digests,
            SUM(CASE WHEN digest IS NULL THEN 1 ELSE 0 END) as null_digest_rows,
            SUM(CASE WHEN digest IS NULL THEN exec_count ELSE 0 END) as null_digest_exec_count,
            SUM(CASE WHEN digest = '' THEN 1 ELSE 0 END) as others_digest_rows,
            SUM(CASE WHEN digest = '' THEN exec_count ELSE 0 END) as others_digest_exec_count
        FROM all_records
        GROUP BY win_start, win_end
        ORDER BY win_start DESC
    """

    if DEBUG_MODE:
        print(f"DuckDB Windows Query (Raw Logs):\n{query}")

    try:
        results = con.execute(query).fetchall()
        return results
    except Exception as e:
        print(f"Error querying raw logs windows: {e}")
        return []
    finally:
        con.close()


def query_duckdb_top_sqls(minio_endpoint, tenant_id, cluster_id, start_time, end_time, limit=10):
    """Query top SQLs from DuckDB"""
    table_path = f"s3://tidb-logs/deltalake/{tenant_id}/{cluster_id}/persisted_statements_summary"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT 
            digest,
            stmt_type,
            digest_text,
            exec_count,
            sum_latency
        FROM delta_scan('{table_path}')
        WHERE summary_begin_time <= to_timestamp('{end_time}')
          AND summary_end_time >= to_timestamp('{start_time}')
        ORDER BY exec_count DESC
        LIMIT {limit}
    """

    if DEBUG_MODE:
        print(f"DuckDB Top SQLs Query:\n{query}")

    try:
        result = con.execute(query).fetchall()
        return result
    except Exception as e:
        print(f"Error querying DuckDB top SQLs: {e}")
        return []
    finally:
        con.close()


def query_duckdb_top_sqls_from_raw_logs(minio_endpoint, start_time, end_time, limit=10):
    """Query top SQLs from raw log files"""
    base_path = "s3://tidb-logs/statement/**/*"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT 
            digest,
            stmt_type,
            digest_text,
            exec_count,
            sum_latency
        FROM (
            SELECT 
                try_cast(json_extract(message, '$.digest') as VARCHAR) as digest,
                try_cast(json_extract(message, '$.stmt_type') as VARCHAR) as stmt_type,
                try_cast(json_extract(message, '$.digest_text') as VARCHAR) as digest_text,
                try_cast(json_extract(message, '$.exec_count') as BIGINT) as exec_count,
                try_cast(json_extract(message, '$.sum_latency') as BIGINT) as sum_latency
            FROM read_json_auto('{base_path}')
            WHERE message LIKE '%digest%'
              AND (
                  (try_cast(json_extract(message, '$.begin') as BIGINT) <= {end_time}
                  AND try_cast(json_extract(message, '$.end') as BIGINT) >= {start_time})
                  OR
                  (try_cast(json_extract(message, '$.begin') as BIGINT) BETWEEN {start_time} AND {end_time})
              )
        )
        ORDER BY exec_count DESC
        LIMIT {limit}
    """

    if DEBUG_MODE:
        print(f"DuckDB Top SQLs Query (Raw Logs):\n{query}")

    try:
        result = con.execute(query).fetchall()
        return result
    except Exception as e:
        print(f"Error querying raw logs top SQLs: {e}")
        return []
    finally:
        con.close()


def query_duckdb_top_stmt_types(minio_endpoint, tenant_id, cluster_id, start_time, end_time, limit=10):
    """Query top statement types from DuckDB"""
    table_path = f"s3://tidb-logs/deltalake/{tenant_id}/{cluster_id}/persisted_statements_summary"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT 
            stmt_type,
            COUNT(*) as distinct_count,
            SUM(exec_count) as total_exec_count,
            SUM(sum_latency) as total_latency
        FROM delta_scan('{table_path}')
        WHERE summary_begin_time <= to_timestamp('{end_time}')
          AND summary_end_time >= to_timestamp('{start_time}')
        GROUP BY stmt_type
        ORDER BY total_exec_count DESC
        LIMIT {limit}
    """

    if DEBUG_MODE:
        print(f"DuckDB Top Stmt Types Query:\n{query}")

    try:
        result = con.execute(query).fetchall()
        return result
    except Exception as e:
        print(f"Error querying DuckDB top stmt types: {e}")
        return []
    finally:
        con.close()


def query_duckdb_top_stmt_types_from_raw_logs(minio_endpoint, start_time, end_time, limit=10):
    """Query top statement types from raw log files"""
    base_path = "s3://tidb-logs/statement/**/*"

    con = get_duckdb_connection(minio_endpoint)

    query = f"""
        SELECT 
            stmt_type,
            COUNT(*) as distinct_count,
            SUM(exec_count) as total_exec_count,
            SUM(sum_latency) as total_latency
        FROM (
            SELECT 
                try_cast(json_extract(message, '$.stmt_type') as VARCHAR) as stmt_type,
                try_cast(json_extract(message, '$.exec_count') as BIGINT) as exec_count,
                try_cast(json_extract(message, '$.sum_latency') as BIGINT) as sum_latency
            FROM read_json_auto('{base_path}')
            WHERE message LIKE '%digest%'
              AND (
                  (try_cast(json_extract(message, '$.begin') as BIGINT) <= {end_time}
                  AND try_cast(json_extract(message, '$.end') as BIGINT) >= {start_time})
                  OR
                  (try_cast(json_extract(message, '$.begin') as BIGINT) BETWEEN {start_time} AND {end_time})
              )
        )
        GROUP BY stmt_type
        ORDER BY total_exec_count DESC
        LIMIT {limit}
    """

    if DEBUG_MODE:
        print(f"DuckDB Top Stmt Types Query (Raw Logs):\n{query}")

    try:
        result = con.execute(query).fetchall()
        return result
    except Exception as e:
        print(f"Error querying raw logs top stmt types: {e}")
        return []
    finally:
        con.close()


def query_prometheus_count(prometheus_url, start_time, end_time):
    """Query tidb_executor_statement_total increment from Prometheus"""
    duration = end_time - start_time
    if duration <= 0:
        return 0

    query = f"sum(increase(tidb_executor_statement_total[{duration}s]))"
    api_url = f"{prometheus_url}/api/v1/query"
    params = {'query': query, 'time': end_time}

    if DEBUG_MODE:
        print(f"Prometheus Query: {query}")
        print(f"Prometheus URL: {api_url}")
        print(f"Request params: {params}")

    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data['status'] != 'success':
            return 0

        results = data['data']['result']
        if not results:
            return 0

        value = results[0]['value'][1]
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0

    except Exception as e:
        print(f"Error querying Prometheus: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description='Calculate Statement Coverage by Time Interval')
    parser.add_argument('--prometheus-url', default=os.getenv('PROMETHEUS_URL', 'http://10.97.115.151:9090'))
    parser.add_argument('--minio-endpoint', default=os.getenv('MINIO_ENDPOINT', 'http://minio:9000'))
    parser.add_argument('--tenant-id', default=os.getenv('TENANT_ID', 'default'))
    parser.add_argument('--cluster-id', default=os.getenv('CLUSTER_ID', 'tc'))
    parser.add_argument('--end-time', type=int, help='End time as Unix timestamp (default: now)')
    parser.add_argument('--use-raw-logs', action='store_true', help='Use raw log files instead of Delta Lake')
    parser.add_argument('--summary-report', action='store_true', default=True, help='Show summary report with window statistics')
    parser.add_argument('--detailed-report', action='store_true', help='Show detailed report with top SQLs and statement types')
    parser.add_argument('--debug', action='store_true', help='Print debug information including SQL queries')

    args = parser.parse_args()

    global DEBUG_MODE
    DEBUG_MODE = args.debug

    if args.end_time:
        end_time = args.end_time
    else:
        end_time = int(datetime.now().timestamp())

    start_time = end_time - 3 * 3600

    interval = 1800

    if not args.detailed_report:
        print(f"\n{'='*220}")
        print(f"Log Coverage & Digests Summary Report")
        print(f"{'='*220}")
        print(f"Time Range: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M')} to {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M')}")
        if not args.use_raw_logs:
            print(f"Tenant: {args.tenant_id}, Cluster: {args.cluster_id}")
        print(f"Data Source: {'Raw Log Files' if args.use_raw_logs else 'Delta Lake'}")
        print(f"Prometheus URL: {args.prometheus_url}")
        print(f"{'='*220}")
        print(f"{'Window Start':<20} | {'Window End':<20} | {'Total Log':<10} | {'Normal Log':<11} | {'Prom Count':<11} | {'Total Cov':<10} | {'Normal Cov':<11} | {'Digests':<8} | {'Others Rows':<12} | {'Others Exec':<12} | {'Others %':<10}")
        print(f"{'='*220}")

        if args.use_raw_logs:
            windows = query_duckdb_windows_from_raw_logs(args.minio_endpoint, start_time, end_time)
        else:
            windows = query_duckdb_windows(args.minio_endpoint, args.tenant_id, args.cluster_id, start_time, end_time)

        if not windows:
            print("No windows found in logs.")
            return

        for win_start, win_end, total_log_count, normal_log_count, num_digests, null_rows, null_exec, others_rows, others_exec in windows:
            if win_start is None or win_end is None:
                continue

            win_start = int(win_start)
            win_end = int(win_end)

            prom_count = query_prometheus_count(args.prometheus_url, win_start, win_end)

            total_log_count = int(total_log_count) if total_log_count else 0
            normal_log_count = int(normal_log_count) if normal_log_count else 0
            prom_count = int(prom_count)

            total_coverage = 0.0
            if prom_count > 0:
                total_coverage = (total_log_count / prom_count) * 100

            normal_coverage = 0.0
            if prom_count > 0:
                normal_coverage = (normal_log_count / prom_count) * 100

            start_str = datetime.fromtimestamp(win_start).strftime('%Y-%m-%d %H:%M:%S')
            end_str = datetime.fromtimestamp(win_end).strftime('%H:%M:%S')

            others_rows = int(others_rows) if others_rows else 0
            others_exec = int(others_exec) if others_exec else 0

            others_ratio = 0.0
            if prom_count > 0:
                others_ratio = (others_exec / prom_count) * 100

            print(f"{start_str:<20} | {end_str:<20} | {total_log_count:<10} | {normal_log_count:<11} | {prom_count:<11} | {total_coverage:>6.2f}%   | {normal_coverage:>6.2f}%    | {num_digests:<8} | {others_rows:<12} | {others_exec:<12} | {others_ratio:>6.2f}%")
        return

    time_ranges = []
    current_start = start_time
    while current_start < end_time:
        current_end = min(current_start + interval, end_time)
        time_ranges.append((current_start, current_end))
        current_start = current_end

    print(f"\n{'='*100}")
    print(f"Statement Coverage Report by Time Interval (Past 3 Hours)")
    print(f"{'='*100}")
    print(f"Time Range: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M')} to {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M')}")
    if not args.use_raw_logs:
        print(f"Tenant: {args.tenant_id}, Cluster: {args.cluster_id}")
    print(f"Data Source: {'Raw Log Files' if args.use_raw_logs else 'Delta Lake'}")
    print(f"Intervals: {len(time_ranges)} x 30 minutes")
    print(f"{'='*100}\n")

    for idx, (range_start, range_end) in enumerate(time_ranges, 1):
        start_dt = datetime.fromtimestamp(range_start)
        end_dt = datetime.fromtimestamp(range_end)

        print(f"\n{'='*100}")
        print(f"Interval {idx}/{len(time_ranges)}: {start_dt.strftime('%Y-%m-%d %H:%M')} - {end_dt.strftime('%H:%M')}")
        print(f"{'='*100}\n")

        if args.use_raw_logs:
            duckdb_count = query_duckdb_count_from_raw_logs(args.minio_endpoint, range_start, range_end)
        else:
            duckdb_count = query_duckdb_count(args.minio_endpoint, args.tenant_id, args.cluster_id, range_start, range_end)

        prometheus_count = query_prometheus_count(args.prometheus_url, range_start, range_end)
        coverage = (duckdb_count / prometheus_count * 100) if prometheus_count > 0 else 0

        print(f"Coverage:")
        print(f"  DuckDB Count:     {duckdb_count:,}")
        print(f"  Prometheus Count: {prometheus_count:,.0f}")
        print(f"  Coverage:         {coverage:.2f}%")

        print(f"\nTop 10 SQLs:")
        if args.use_raw_logs:
            top_sqls = query_duckdb_top_sqls_from_raw_logs(args.minio_endpoint, range_start, range_end, 10)
        else:
            top_sqls = query_duckdb_top_sqls(args.minio_endpoint, args.tenant_id, args.cluster_id, range_start, range_end, 10)

        if top_sqls:
            print(f"  {'Rank':<5} {'Type':<12} {'Exec Count':<12} {'Avg Latency (ms)':<18}")
            print(f"  {'-'*50}")
            for rank, (digest, stmt_type, digest_text, exec_count, sum_latency) in enumerate(top_sqls, 1):
                avg_latency = sum_latency / exec_count if exec_count > 0 else 0
                print(f"  {rank:<5} {stmt_type:<12} {exec_count:<12,} {avg_latency:<18.2f}")
                print(f"  SQL: {digest_text}")
                print(f"  Digest: {digest}")
                print()
        else:
            print("  No data")

        print(f"\nTop 10 Statement Types:")
        if args.use_raw_logs:
            top_stmt_types = query_duckdb_top_stmt_types_from_raw_logs(args.minio_endpoint, range_start, range_end, 10)
        else:
            top_stmt_types = query_duckdb_top_stmt_types(args.minio_endpoint, args.tenant_id, args.cluster_id, range_start, range_end, 10)

        if top_stmt_types:
            print(f"  {'Rank':<5} {'Type':<20} {'Distinct':<12} {'Total Exec':<15} {'Avg Latency (ms)':<18}")
            print(f"  {'-'*80}")
            for rank, (stmt_type, distinct_count, total_exec_count, total_latency) in enumerate(top_stmt_types, 1):
                avg_latency = total_latency / total_exec_count if total_exec_count > 0 else 0
                print(f"  {rank:<5} {stmt_type:<20} {distinct_count:<12,} {total_exec_count:<15,} {avg_latency:<18.2f}")
        else:
            print("  No data")


if __name__ == "__main__":
    main()
