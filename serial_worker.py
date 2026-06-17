"""
多线程串口通信模块
负责每隔100毫秒从流式分析仪硬件串口读取5分类白细胞原始散射光数据
"""

import time
import struct
import random
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


@dataclass
class FlowCytometryData:
    timestamp: float = 0.0

    wbc_count: float = 0.0
    rbc_count: float = 0.0
    plt_count: float = 0.0

    neutrophil_fsc: List[float] = field(default_factory=list)
    neutrophil_ssc: List[float] = field(default_factory=list)
    lymphocyte_fsc: List[float] = field(default_factory=list)
    lymphocyte_ssc: List[float] = field(default_factory=list)
    monocyte_fsc: List[float] = field(default_factory=list)
    monocyte_ssc: List[float] = field(default_factory=list)
    eosinophil_fsc: List[float] = field(default_factory=list)
    eosinophil_ssc: List[float] = field(default_factory=list)
    basophil_fsc: List[float] = field(default_factory=list)
    basophil_ssc: List[float] = field(default_factory=list)

    neutrophil_pct: float = 0.0
    lymphocyte_pct: float = 0.0
    monocyte_pct: float = 0.0
    eosinophil_pct: float = 0.0
    basophil_pct: float = 0.0


class SerialWorker(QThread):
    data_received = Signal(object)
    error_occurred = Signal(str)
    connection_status_changed = Signal(bool, str)

    READ_INTERVAL_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._running = False
        self._serial_port: Optional["serial.Serial"] = None
        self._port_name = ""
        self._baudrate = 115200
        self._use_simulation = True
        self._simulation_counter = 0

    def set_serial_config(self, port_name: str, baudrate: int = 115200,
                          use_simulation: bool = True):
        locker = QMutexLocker(self._mutex)
        self._port_name = port_name
        self._baudrate = baudrate
        self._use_simulation = use_simulation

    def start_work(self):
        locker = QMutexLocker(self._mutex)
        self._running = True

        if not self._use_simulation and SERIAL_AVAILABLE:
            try:
                self._serial_port = serial.Serial(
                    port=self._port_name,
                    baudrate=self._baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.05
                )
                self.connection_status_changed.emit(True, f"串口 {self._port_name} 已连接")
            except Exception as e:
                self._use_simulation = True
                self._serial_port = None
                self.error_occurred.emit(f"串口连接失败，启用模拟模式: {str(e)}")
                self.connection_status_changed.emit(True, "模拟模式已启动")
        else:
            self.connection_status_changed.emit(True, "模拟模式已启动")

        self.start()

    def stop_work(self):
        locker = QMutexLocker(self._mutex)
        self._running = False

    def is_running(self) -> bool:
        locker = QMutexLocker(self._mutex)
        return self._running

    def run(self):
        interval_sec = self.READ_INTERVAL_MS / 1000.0

        while True:
            locker = QMutexLocker(self._mutex)
            if not self._running:
                break
            locker.unlock()

            try:
                if self._use_simulation:
                    data = self._generate_simulation_data()
                else:
                    data = self._read_from_serial()

                if data is not None:
                    self.data_received.emit(data)

            except Exception as e:
                self.error_occurred.emit(f"数据读取错误: {str(e)}")

            time.sleep(interval_sec)

        if self._serial_port and self._serial_port.is_open:
            self._serial_port.close()
            self._serial_port = None

        self.connection_status_changed.emit(False, "串口已断开")

    def _generate_simulation_data(self) -> FlowCytometryData:
        self._simulation_counter += 1
        t = self._simulation_counter

        base_wbc = 6.5 + 0.3 * random.uniform(-1, 1)
        base_rbc = 4.8 + 0.15 * random.uniform(-1, 1)
        base_plt = 220 + 15 * random.uniform(-1, 1)

        data = FlowCytometryData()
        data.timestamp = time.time()

        data.wbc_count = max(3.5, min(9.5, base_wbc + 0.05 * (t % 50 - 25)))
        data.rbc_count = max(4.0, min(5.5, base_rbc + 0.02 * (t % 60 - 30)))
        data.plt_count = max(150, min(300, base_plt + 2 * (t % 40 - 20)))

        neu_pct = 50 + 3 * random.uniform(-1, 1)
        lym_pct = 35 + 2 * random.uniform(-1, 1)
        mon_pct = 8 + 1 * random.uniform(-1, 1)
        eos_pct = 4 + 0.8 * random.uniform(-1, 1)
        bas_pct = 3 + 0.5 * random.uniform(-1, 1)

        total = neu_pct + lym_pct + mon_pct + eos_pct + bas_pct
        data.neutrophil_pct = neu_pct / total * 100
        data.lymphocyte_pct = lym_pct / total * 100
        data.monocyte_pct = mon_pct / total * 100
        data.eosinophil_pct = eos_pct / total * 100
        data.basophil_pct = bas_pct / total * 100

        num_cells = 30
        data.neutrophil_fsc = [random.gauss(350, 60) for _ in range(num_cells)]
        data.neutrophil_ssc = [random.gauss(280, 50) for _ in range(num_cells)]
        data.lymphocyte_fsc = [random.gauss(180, 30) for _ in range(num_cells)]
        data.lymphocyte_ssc = [random.gauss(120, 25) for _ in range(num_cells)]
        data.monocyte_fsc = [random.gauss(420, 70) for _ in range(num_cells)]
        data.monocyte_ssc = [random.gauss(200, 40) for _ in range(num_cells)]
        data.eosinophil_fsc = [random.gauss(360, 55) for _ in range(num_cells)]
        data.eosinophil_ssc = [random.gauss(380, 65) for _ in range(num_cells)]
        data.basophil_fsc = [random.gauss(200, 35) for _ in range(num_cells)]
        data.basophil_ssc = [random.gauss(450, 70) for _ in range(num_cells)]

        return data

    def _read_from_serial(self) -> Optional[FlowCytometryData]:
        if self._serial_port is None or not self._serial_port.is_open:
            return None

        if self._serial_port.in_waiting < 128:
            return None

        raw_data = self._serial_port.read(self._serial_port.in_waiting)
        return self._parse_raw_data(raw_data)

    def _parse_raw_data(self, raw: bytes) -> Optional[FlowCytometryData]:
        try:
            if len(raw) < 4:
                return None

            header = raw[:2]
            if header != b'\xAA\x55':
                return None

            data = FlowCytometryData()
            data.timestamp = time.time()

            offset = 2
            if len(raw) >= offset + 24:
                values = struct.unpack('<6f', raw[offset:offset + 24])
                data.wbc_count = values[0]
                data.rbc_count = values[1]
                data.plt_count = values[2]
                data.neutrophil_pct = values[3]
                data.lymphocyte_pct = values[4]
                data.monocyte_pct = values[5]
                offset += 24

            if len(raw) >= offset + 8:
                values = struct.unpack('<2f', raw[offset:offset + 8])
                data.eosinophil_pct = values[0]
                data.basophil_pct = values[1]
                offset += 8

            def read_scatter_list(n: int) -> List[float]:
                nonlocal offset
                lst = []
                for _ in range(min(n, (len(raw) - offset) // 4)):
                    lst.append(struct.unpack('<f', raw[offset:offset + 4])[0])
                    offset += 4
                return lst

            data.neutrophil_fsc = read_scatter_list(30)
            data.neutrophil_ssc = read_scatter_list(30)
            data.lymphocyte_fsc = read_scatter_list(30)
            data.lymphocyte_ssc = read_scatter_list(30)
            data.monocyte_fsc = read_scatter_list(30)
            data.monocyte_ssc = read_scatter_list(30)
            data.eosinophil_fsc = read_scatter_list(30)
            data.eosinophil_ssc = read_scatter_list(30)
            data.basophil_fsc = read_scatter_list(30)
            data.basophil_ssc = read_scatter_list(30)

            return data
        except Exception:
            return None


def list_available_ports() -> List[str]:
    if not SERIAL_AVAILABLE:
        return []
    try:
        return [port.device for port in serial.tools.list_ports.comports()]
    except Exception:
        return []
