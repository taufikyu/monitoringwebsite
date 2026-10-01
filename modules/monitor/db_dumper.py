import os
import time
import logging
import gzip
import psycopg2

log = logging.getLogger(__name__)

def dump_postgres_to_sql(host, port, user, password, dbname, output_file, schema=None):
    """
    Ekstraksi database PostgreSQL murni menggunakan koneksi psycopg2 dengan streaming
    native COPY ... TO STDOUT (tanpa row-by-row loop), format standar PostgreSQL.
    Mendukung format kompresi gzip langsung (.sql.gz) maupun teks biasa (.sql).
    Mendukung deteksi multi-schema (public, webr, aset, pad, dll.) serta penanganan toleran
    jika terdapat tabel backup/unreadable tanpa menggagalkan dump tabel lainnya.
    """
    log.info(f"Connecting to PostgreSQL for dump: {user}@{host}:{port}/{dbname}")
    
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        connect_timeout=15
    )
    conn.autocommit = True
    cur = conn.cursor()
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    file_ctx = gzip.open(output_file, 'wb', compresslevel=1) if output_file.endswith('.gz') else open(output_file, 'wb', buffering=128 * 1024)
    
    with file_ctx as f:
        def write_str(s):
            f.write(s.encode('utf-8'))

        write_str(f"-- ==========================================================\n")
        write_str(f"-- PostgreSQL Database Dump (COPY Bulk-Stream via MANDB)\n")
        write_str(f"-- Target Database: {dbname}\n")
        write_str(f"-- Host / Port    : {host}:{port}\n")
        write_str(f"-- Generated at   : {timestamp}\n")
        write_str(f"-- ==========================================================\n\n")
        write_str("SET statement_timeout = 0;\n")
        write_str("SET lock_timeout = '10s';\n")
        write_str("SET client_encoding = 'UTF8';\n")
        write_str("SET standard_conforming_strings = on;\n")
        write_str("SET check_function_bodies = false;\n\n")
        write_str("BEGIN;\n\n")
        
        target_schemas = []
        if schema and schema not in ['all', '*']:
            target_schemas = [schema]
        else:
            cur.execute("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name NOT IN ('information_schema', 'pg_catalog') 
                  AND schema_name NOT LIKE 'pg_temp%%' 
                  AND schema_name NOT LIKE 'pg_toast%%'
                ORDER BY (schema_name = 'public') DESC, schema_name ASC;
            """)
            target_schemas = [r[0] for r in cur.fetchall()]
            if not target_schemas:
                target_schemas = ['public']

        all_constraints = []
        all_indexes = []

        for sc in target_schemas:
            if sc != 'public':
                write_str(f"CREATE SCHEMA IF NOT EXISTS {sc};\n\n")

            log.info(f"Extracting sequences for schema '{sc}'...")
            try:
                cur.execute("""
                    SELECT sequence_name 
                    FROM information_schema.sequences 
                    WHERE sequence_schema = %s
                    ORDER BY sequence_name;
                """, (sc,))
                sequences = [row[0] for row in cur.fetchall()]
                
                if sequences:
                    write_str(f"-- ----------------------------\n")
                    write_str(f"-- Sequences for schema {sc}\n")
                    write_str(f"-- ----------------------------\n")
                    for seq in sequences:
                        write_str(f"CREATE SEQUENCE IF NOT EXISTS {sc}.{seq};\n")
                        try:
                            cur.execute(f"SELECT last_value, is_called FROM {sc}.{seq}")
                            last_val, is_called = cur.fetchone()
                            write_str(f"SELECT pg_catalog.setval('{sc}.{seq}', {last_val}, {str(is_called).lower()});\n")
                        except Exception as seq_err:
                            log.warning(f"Could not read sequence value for {seq}: {seq_err}")
                    write_str("\n")
            except Exception as e_seq:
                log.warning(f"Could not list sequences for {sc}: {e_seq}")

            log.info(f"Extracting schema metadata in bulk for '{sc}'...")
            cols_by_table = {}
            try:
                cur.execute("""
                    SELECT 
                        table_name,
                        column_name, 
                        data_type, 
                        character_maximum_length, 
                        numeric_precision, 
                        numeric_scale,
                        is_nullable, 
                        column_default,
                        udt_name
                    FROM information_schema.columns 
                    WHERE table_schema = %s
                    ORDER BY table_name, ordinal_position;
                """, (sc,))
                for row in cur.fetchall():
                    t_name = row[0]
                    col_info = row[1:]
                    if t_name not in cols_by_table:
                        cols_by_table[t_name] = []
                    cols_by_table[t_name].append(col_info)
            except Exception as e_col_bulk:
                log.warning(f"Could not bulk query columns for schema {sc}: {e_col_bulk}")

            constraints_by_table = {}
            try:
                cur.execute("""
                    SELECT 
                        t.relname,
                        c.conname, 
                        pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    JOIN pg_namespace n ON t.relnamespace = n.oid
                    WHERE n.nspname = %s;
                """, (sc,))
                for t_name, conname, condef in cur.fetchall():
                    if t_name not in constraints_by_table:
                        constraints_by_table[t_name] = []
                    constraints_by_table[t_name].append(f"ALTER TABLE {sc}.\"{t_name}\" ADD CONSTRAINT \"{conname}\" {condef};")
            except Exception as e_con_bulk:
                log.warning(f"Could not bulk query constraints for schema {sc}: {e_con_bulk}")

            indexes_by_table = {}
            try:
                cur.execute("""
                    SELECT tablename, indexdef 
                    FROM pg_indexes 
                    WHERE schemaname = %s AND indexname NOT LIKE '%%_pkey';
                """, (sc,))
                for t_name, idxdef in cur.fetchall():
                    if t_name not in indexes_by_table:
                        indexes_by_table[t_name] = []
                    indexes_by_table[t_name].append(f"{idxdef};")
            except Exception as e_idx_bulk:
                log.warning(f"Could not bulk query indexes for schema {sc}: {e_idx_bulk}")

            try:
                cur.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name;
                """, (sc,))
                tables = [row[0] for row in cur.fetchall()]
            except Exception as e_tbl:
                log.warning(f"Could not list tables for schema {sc}: {e_tbl}")
                continue

            for tbl in tables:
                cols = cols_by_table.get(tbl, [])
                if not cols:
                    continue

                if tbl in constraints_by_table:
                    all_constraints.extend(constraints_by_table[tbl])
                if tbl in indexes_by_table:
                    all_indexes.extend(indexes_by_table[tbl])

                try:
                    write_str(f"-- ----------------------------\n")
                    write_str(f"-- Table structure for {sc}.{tbl}\n")
                    write_str(f"-- ----------------------------\n")
                    
                    col_defs = []
                    col_names = []
                    for col in cols:
                        c_name, c_type, max_len, num_prec, num_scale, is_null, c_def, udt = col
                        col_names.append(c_name)
                        
                        type_str = c_type.upper()
                        if c_type == 'character varying':
                            type_str = f"VARCHAR({max_len})" if max_len else "VARCHAR"
                        elif c_type == 'character':
                            type_str = f"CHAR({max_len})" if max_len else "CHAR"
                        elif c_type == 'numeric':
                            type_str = f"NUMERIC({num_prec},{num_scale})" if num_prec else "NUMERIC"
                        elif c_type == 'USER-DEFINED':
                            type_str = udt.upper()
                            
                        null_str = "NOT NULL" if is_null == 'NO' else ""
                        def_str = f"DEFAULT {c_def}" if c_def is not None else ""
                        
                        parts = [f'"{c_name}"', type_str, def_str, null_str]
                        col_defs.append(" ".join(p for p in parts if p))

                    write_str(f"CREATE TABLE IF NOT EXISTS {sc}.\"{tbl}\" (\n")
                    write_str("    " + ",\n    ".join(col_defs) + "\n")
                    write_str(");\n\n")
                        
                    if col_names:
                        col_header = ", ".join(f'"{c}"' for c in col_names)
                        write_str(f"-- ----------------------------\n")
                        write_str(f"-- Data stream for {sc}.{tbl}\n")
                        write_str(f"-- ----------------------------\n")
                        write_str(f"COPY {sc}.\"{tbl}\" ({col_header}) FROM stdin;\n")
                        cur.copy_expert(f"COPY {sc}.\"{tbl}\" ({col_header}) TO STDOUT", f)
                        write_str("\\.\n\n")
                except Exception as tbl_err:
                    log.warning(f"Skipping table {sc}.{tbl} due to error: {tbl_err}")
                    write_str(f"-- SKIPPED table {sc}.\"{tbl}\" (Error: {tbl_err})\n\n")
                
        if all_constraints:
            write_str("-- ----------------------------\n")
            write_str("-- Constraints & Keys\n")
            write_str("-- ----------------------------\n")
            for con in all_constraints:
                write_str(f"{con}\n")
            write_str("\n")
            
        if all_indexes:
            write_str("-- ----------------------------\n")
            write_str("-- Indexes\n")
            write_str("-- ----------------------------\n")
            for idx in all_indexes:
                write_str(f"{idx}\n")
            write_str("\n")
            
        write_str("COMMIT;\n")
        write_str("-- ====================== END OF DUMP ======================\n")

    cur.close()
    conn.close()
    log.info(f"Database dump completed successfully: {output_file}")
    return output_file
