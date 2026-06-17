"""
SQLite数据库模块
持久化临床细胞质控标定参数
"""

import os
import sqlite3
import time
import math
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "calibration_data.db"
)


CHANNELS = [
    "wbc", "rbc", "plt",
    "neutrophil_fsc", "neutrophil_ssc",
    "lymphocyte_fsc", "lymphocyte_ssc",
    "monocyte_fsc", "monocyte_ssc",
    "eosinophil_fsc", "eosinophil_ssc",
    "basophil_fsc", "basophil_ssc",
]


@dataclass
class CalibrationRecord:
    id: Optional[int]
    channel_name: str
    gain: float
    offset: float
    deviation: float
    target_value: float
    measured_mean: float
    measured_std: float
    sample_count: int
    operator: str
    remark: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DatabaseManager:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        cursor = self._conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calibration_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                gain REAL NOT NULL DEFAULT 1.0,
                offset REAL NOT NULL DEFAULT 0.0,
                deviation REAL NOT NULL DEFAULT 0.0,
                target_value REAL NOT NULL DEFAULT 0.0,
                measured_mean REAL NOT NULL DEFAULT 0.0,
                measured_std REAL NOT NULL DEFAULT 0.0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                operator TEXT NOT NULL DEFAULT '',
                remark TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_channel_name
            ON calibration_records(channel_name)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        self._conn.commit()

    def insert_calibration(self, record: CalibrationRecord) -> int:
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO calibration_records (
                channel_name, gain, offset, deviation, target_value,
                measured_mean, measured_std, sample_count, operator,
                remark, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.channel_name, record.gain, record.offset,
            record.deviation, record.target_value, record.measured_mean,
            record.measured_std, record.sample_count, record.operator,
            record.remark, record.created_at
        ))
        self._conn.commit()
        return cursor.lastrowid

    def get_latest_calibration(self, channel_name: str) -> Optional[CalibrationRecord]:
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM calibration_records
            WHERE channel_name = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (channel_name,))
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_all_latest_calibrations(self) -> Dict[str, CalibrationRecord]:
        result = {}
        for ch in CHANNELS:
            rec = self.get_latest_calibration(ch)
            if rec is not None:
                result[ch] = rec
        return result

    def get_calibration_history(
        self, channel_name: str, limit: int = 50
    ) -> List[CalibrationRecord]:
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM calibration_records
            WHERE channel_name = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (channel_name, limit))
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def delete_calibration(self, record_id: int) -> bool:
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM calibration_records WHERE id = ?",
            (record_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def set_setting(self, key: str, value: str):
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (key, value, time.time()))
        self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()
        return row["value"] if row else default

    def _row_to_record(self, row: sqlite3.Row) -> CalibrationRecord:
        return CalibrationRecord(
            id=row["id"],
            channel_name=row["channel_name"],
            gain=row["gain"],
            offset=row["offset"],
            deviation=row["deviation"],
            target_value=row["target_value"],
            measured_mean=row["measured_mean"],
            measured_std=row["measured_std"],
            sample_count=row["sample_count"],
            operator=row["operator"],
            remark=row["remark"],
            created_at=row["created_at"],
        )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()


def create_default_calibration(
    channel_name: str,
    target_value: float,
    measured_mean: float,
    measured_std: float,
    sample_count: int,
    operator: str = "",
    remark: str = "",
) -> CalibrationRecord:
    eps = 1e-12

    def _safe_float(v: float, default: float = 0.0) -> float:
        try:
            fv = float(v)
            if math.isfinite(fv):
                return fv
            return default
        except (TypeError, ValueError):
            return default

    target_value = _safe_float(target_value, 0.0)
    measured_mean = _safe_float(measured_mean, 0.0)
    measured_std = _safe_float(measured_std, 0.0)

    if abs(measured_mean) < eps:
        gain = 1.0
        offset = 0.0
        deviation = 100.0
    else:
        if abs(target_value) > eps:
            gain = target_value / measured_mean
            deviation = ((measured_mean - target_value) / target_value) * 100.0
        else:
            gain = 1.0
            deviation = 0.0
        if not math.isfinite(gain) or gain > 100.0 or gain < 0.01:
            gain = 1.0
        if not math.isfinite(deviation):
            deviation = 0.0
        offset = target_value - measured_mean
        if not math.isfinite(offset):
            offset = 0.0

    return CalibrationRecord(
        id=None,
        channel_name=channel_name,
        gain=gain,
        offset=offset,
        deviation=deviation,
        target_value=target_value,
        measured_mean=measured_mean,
        measured_std=measured_std,
        sample_count=sample_count,
        operator=operator,
        remark=remark,
        created_at=time.time(),
    )
