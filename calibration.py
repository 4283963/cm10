"""
临床细胞质控标定算法模块
基于Pandas实现各通道测量偏差值计算
"""

import time
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

import numpy as np
import pandas as pd

from serial_worker import FlowCytometryData
from database import (
    DatabaseManager,
    CalibrationRecord,
    create_default_calibration,
    CHANNELS,
)


TARGET_VALUES: Dict[str, float] = {
    "wbc": 6.5,
    "rbc": 4.8,
    "plt": 220.0,
    "neutrophil_fsc": 350.0,
    "neutrophil_ssc": 280.0,
    "lymphocyte_fsc": 180.0,
    "lymphocyte_ssc": 120.0,
    "monocyte_fsc": 420.0,
    "monocyte_ssc": 200.0,
    "eosinophil_fsc": 360.0,
    "eosinophil_ssc": 380.0,
    "basophil_fsc": 200.0,
    "basophil_ssc": 450.0,
}


CHANNEL_LABELS: Dict[str, str] = {
    "wbc": "白细胞计数 (×10⁹/L)",
    "rbc": "红细胞计数 (×10¹²/L)",
    "plt": "血小板计数 (×10⁹/L)",
    "neutrophil_fsc": "中性粒细胞-前向散射",
    "neutrophil_ssc": "中性粒细胞-侧向散射",
    "lymphocyte_fsc": "淋巴细胞-前向散射",
    "lymphocyte_ssc": "淋巴细胞-侧向散射",
    "monocyte_fsc": "单核细胞-前向散射",
    "monocyte_ssc": "单核细胞-侧向散射",
    "eosinophil_fsc": "嗜酸性粒细胞-前向散射",
    "eosinophil_ssc": "嗜酸性粒细胞-侧向散射",
    "basophil_fsc": "嗜碱性粒细胞-前向散射",
    "basophil_ssc": "嗜碱性粒细胞-侧向散射",
}


@dataclass
class CalibrationResult:
    channel: str
    label: str
    target_value: float
    measured_mean: float
    measured_std: float
    measured_cv: float
    deviation: float
    gain: float
    offset: float
    sample_count: int
    passed: bool
    message: str = ""


@dataclass
class CalibrationProgress:
    is_running: bool = False
    sample_count: int = 0
    target_samples: int = 0
    current_channel: str = ""
    progress_pct: float = 0.0
    status_message: str = ""


class CalibrationEngine:
    DEFAULT_SAMPLES = 300
    ACCEPTABLE_DEVIATION_PCT = 3.0
    ACCEPTABLE_CV_PCT = 5.0

    def __init__(self):
        self._db = DatabaseManager()
        self._lock = threading.Lock()
        self._is_running = False
        self._collected_data: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}
        self._target_samples = self.DEFAULT_SAMPLES
        self._results: List[CalibrationResult] = []
        self._progress_callback: Optional[Callable[[CalibrationProgress], None]] = None
        self._finished_callback: Optional[Callable[[List[CalibrationResult]], None]] = None
        self._progress = CalibrationProgress()

    def set_progress_callback(self, callback: Callable[[CalibrationProgress], None]):
        self._progress_callback = callback

    def set_finished_callback(self, callback: Callable[[List[CalibrationResult]], None]):
        self._finished_callback = callback

    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def start_calibration(self, target_samples: int = 300):
        with self._lock:
            if self._is_running:
                return False
            self._is_running = True
            self._target_samples = max(60, min(1000, target_samples))
            self._collected_data = {ch: [] for ch in CHANNELS}
            self._results = []
            self._progress = CalibrationProgress(
                is_running=True,
                sample_count=0,
                target_samples=self._target_samples,
                current_channel=CHANNELS[0],
                progress_pct=0.0,
                status_message="标定启动，开始采集数据..."
            )
        self._notify_progress()
        return True

    def process_data(self, data: FlowCytometryData):
        with self._lock:
            if not self._is_running:
                return

        samples_map = {
            "wbc": [data.wbc_count],
            "rbc": [data.rbc_count],
            "plt": [data.plt_count],
            "neutrophil_fsc": data.neutrophil_fsc,
            "neutrophil_ssc": data.neutrophil_ssc,
            "lymphocyte_fsc": data.lymphocyte_fsc,
            "lymphocyte_ssc": data.lymphocyte_ssc,
            "monocyte_fsc": data.monocyte_fsc,
            "monocyte_ssc": data.monocyte_ssc,
            "eosinophil_fsc": data.eosinophil_fsc,
            "eosinophil_ssc": data.eosinophil_ssc,
            "basophil_fsc": data.basophil_fsc,
            "basophil_ssc": data.basophil_ssc,
        }

        should_continue = False
        with self._lock:
            for ch, values in samples_map.items():
                self._collected_data[ch].extend(values)

            counts = [len(v) for v in self._collected_data.values()]
            min_count = min(counts) if counts else 0
            self._progress.sample_count = min_count
            self._progress.progress_pct = min(100.0, (min_count / self._target_samples) * 100.0)
            self._progress.status_message = f"已采集 {min_count}/{self._target_samples} 个样本"

            if min_count >= self._target_samples:
                self._progress.status_message = "数据采集完成，正在计算标定参数..."
                self._notify_progress()
                self._compute_and_save()
            else:
                should_continue = True

        if not should_continue:
            with self._lock:
                self._is_running = False
                self._progress.is_running = False
                self._progress.status_message = "标定完成"
            self._notify_progress()
            if self._finished_callback:
                try:
                    self._finished_callback(list(self._results))
                except Exception:
                    pass
        else:
            self._notify_progress()

    def stop_calibration(self):
        with self._lock:
            self._is_running = False
            self._progress.is_running = False
            self._progress.status_message = "标定已取消"
        self._notify_progress()

    def _compute_and_save(self):
        results: List[CalibrationResult] = []

        for channel in CHANNELS:
            raw_values = self._collected_data.get(channel, [])
            result = self._compute_channel_calibration(channel, raw_values)
            results.append(result)

            if result.passed:
                rec = create_default_calibration(
                    channel_name=channel,
                    target_value=result.target_value,
                    measured_mean=result.measured_mean,
                    measured_std=result.measured_std,
                    sample_count=result.sample_count,
                    operator="system",
                    remark=f"自动标定 {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                try:
                    self._db.insert_calibration(rec)
                except Exception:
                    pass

        self._results = results

    def _compute_channel_calibration(
        self, channel: str, raw_values: List[float]
    ) -> CalibrationResult:
        target = TARGET_VALUES.get(channel, 0.0)
        label = CHANNEL_LABELS.get(channel, channel)

        if len(raw_values) == 0:
            return CalibrationResult(
                channel=channel,
                label=label,
                target_value=target,
                measured_mean=0.0,
                measured_std=0.0,
                measured_cv=0.0,
                deviation=0.0,
                gain=1.0,
                offset=0.0,
                sample_count=0,
                passed=False,
                message="无有效数据"
            )

        df = pd.DataFrame({"value": raw_values})

        q1 = df["value"].quantile(0.01)
        q99 = df["value"].quantile(0.99)
        df_filtered = df[(df["value"] >= q1) & (df["value"] <= q99)]

        if df_filtered.empty:
            df_filtered = df

        measured_mean = float(df_filtered["value"].mean())
        measured_std = float(df_filtered["value"].std(ddof=1)) if len(df_filtered) > 1 else 0.0
        measured_cv = (measured_std / measured_mean * 100.0) if abs(measured_mean) > 1e-10 else 0.0

        if abs(target) > 1e-10:
            deviation = ((measured_mean - target) / target) * 100.0
            gain = target / measured_mean if abs(measured_mean) > 1e-10 else 1.0
        else:
            deviation = 0.0
            gain = 1.0

        offset = target - measured_mean

        deviation_ok = abs(deviation) <= self.ACCEPTABLE_DEVIATION_PCT
        cv_ok = measured_cv <= self.ACCEPTABLE_CV_PCT
        passed = deviation_ok and cv_ok and len(df_filtered) >= 30

        message_parts = []
        if not deviation_ok:
            message_parts.append(f"偏差={deviation:+.2f}% (允许±{self.ACCEPTABLE_DEVIATION_PCT}%)")
        if not cv_ok:
            message_parts.append(f"CV={measured_cv:.2f}% (允许≤{self.ACCEPTABLE_CV_PCT}%)")
        if len(df_filtered) < 30:
            message_parts.append(f"有效样本={len(df_filtered)} (需≥30)")
        message = "；".join(message_parts) if message_parts else "标定通过"

        return CalibrationResult(
            channel=channel,
            label=label,
            target_value=target,
            measured_mean=measured_mean,
            measured_std=measured_std,
            measured_cv=measured_cv,
            deviation=deviation,
            gain=gain,
            offset=offset,
            sample_count=len(df_filtered),
            passed=passed,
            message=message
        )

    def _notify_progress(self):
        if self._progress_callback:
            try:
                snapshot = CalibrationProgress(
                    is_running=self._progress.is_running,
                    sample_count=self._progress.sample_count,
                    target_samples=self._progress.target_samples,
                    current_channel=self._progress.current_channel,
                    progress_pct=self._progress.progress_pct,
                    status_message=self._progress.status_message
                )
                self._progress_callback(snapshot)
            except Exception:
                pass

    def get_current_correction(self, channel: str) -> Dict[str, float]:
        rec = self._db.get_latest_calibration(channel)
        if rec is None:
            return {"gain": 1.0, "offset": 0.0}
        return {"gain": rec.gain, "offset": rec.offset}

    def apply_correction(self, channel: str, raw_value: float) -> float:
        corr = self.get_current_correction(channel)
        return raw_value * corr["gain"] + corr["offset"] * 0.0
