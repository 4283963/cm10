"""
主界面模块
包含实时图表、质控标定UI和串口配置界面
"""

import time
from collections import deque
from typing import Optional, Dict, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QGroupBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QProgressBar, QTextEdit, QSplitter,
    QStatusBar, QFrame, QHeaderView, QSizePolicy, QMessageBox, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QColor, QPalette, QIcon, QAction

import pyqtgraph as pg
from pyqtgraph import PlotWidget, ScatterPlotItem, mkPen, mkBrush

from serial_worker import SerialWorker, FlowCytometryData, list_available_ports
from calibration import CalibrationEngine, CalibrationResult, CalibrationProgress
from database import DatabaseManager, CalibrationRecord, CHANNELS


MAX_HISTORY_POINTS = 300


WBC_COLOR = "#2563eb"
RBC_COLOR = "#dc2626"
PLT_COLOR = "#16a34a"

DIFF_COLORS = {
    "neutrophil": "#f59e0b",
    "lymphocyte": "#3b82f6",
    "monocyte": "#8b5cf6",
    "eosinophil": "#ef4444",
    "basophil": "#06b6d4",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.serial_worker: Optional[SerialWorker] = None
        self.calibration_engine = CalibrationEngine()
        self.db = DatabaseManager()

        self._history_timestamps = deque(maxlen=MAX_HISTORY_POINTS)
        self._history_wbc = deque(maxlen=MAX_HISTORY_POINTS)
        self._history_rbc = deque(maxlen=MAX_HISTORY_POINTS)
        self._history_plt = deque(maxlen=MAX_HISTORY_POINTS)

        self._setup_dark_theme()
        self._init_ui()
        self._setup_connections()

        self.setWindowTitle("全自动血液细胞流式分析仪 - 检验科技师工作站")
        self.resize(1400, 900)

    def _setup_dark_theme(self):
        pg.setConfigOptions(antialias=True, background="#1e293b", foreground="#e2e8f0")

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        top_bar = self._build_top_bar()
        root_layout.addWidget(top_bar)

        splitter = QSplitter(Qt.Vertical)
        root_layout.addWidget(splitter, 1)

        upper = QWidget()
        upper_layout = QHBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(6)

        left_panel = self._build_cell_count_panel()
        right_panel = self._build_scatter_panel()
        upper_layout.addWidget(left_panel, 3)
        upper_layout.addWidget(right_panel, 2)

        lower = QWidget()
        lower_layout = QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)

        calib_panel = self._build_calibration_panel()
        diff_panel = self._build_differential_panel()
        lower_layout.addWidget(calib_panel, 3)
        lower_layout.addWidget(diff_panel, 2)

        splitter.addWidget(upper)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("就绪")
        self.connection_label = QLabel("● 未连接")
        self.connection_label.setStyleSheet("color: #94a3b8; font-weight: bold;")
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.connection_label)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("background: #0f172a; border-radius: 6px; padding: 4px;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        title = QLabel("🔬 全自动血液细胞流式分析仪")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #f1f5f9;")
        layout.addWidget(title)

        layout.addStretch(1)

        layout.addWidget(QLabel("串口:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(120)
        self._refresh_ports()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self._refresh_ports)
        layout.addWidget(self.port_combo)
        layout.addWidget(btn_refresh)

        layout.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setMinimumWidth(90)
        layout.addWidget(self.baud_combo)

        self.sim_check = QCheckBox("模拟模式")
        self.sim_check.setChecked(True)
        self.sim_check.setStyleSheet("color: #e2e8f0;")
        layout.addWidget(self.sim_check)

        self.btn_start = QPushButton("▶ 启动采集")
        self.btn_start.setStyleSheet("""
            QPushButton { background: #2563eb; color: white; padding: 6px 14px;
                         border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background: #3b82f6; }
            QPushButton:disabled { background: #475569; }
        """)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("""
            QPushButton { background: #dc2626; color: white; padding: 6px 14px;
                         border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background: #ef4444; }
            QPushButton:disabled { background: #475569; }
        """)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)

        return bar

    def _build_cell_count_panel(self) -> QWidget:
        group = QGroupBox("📊 实时细胞计数趋势")
        group.setStyleSheet("""
            QGroupBox { color: #e2e8f0; border: 1px solid #334155; border-radius: 6px;
                       margin-top: 10px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        value_row = QHBoxLayout()

        self.wbc_value = self._make_count_card("WBC 白细胞", "×10⁹/L", WBC_COLOR, "3.5 - 9.5")
        self.rbc_value = self._make_count_card("RBC 红细胞", "×10¹²/L", RBC_COLOR, "4.0 - 5.5")
        self.plt_value = self._make_count_card("PLT 血小板", "×10⁹/L", PLT_COLOR, "150 - 300")

        value_row.addWidget(self.wbc_value)
        value_row.addWidget(self.rbc_value)
        value_row.addWidget(self.plt_value)
        layout.addLayout(value_row)

        self.plot_counts = PlotWidget()
        self.plot_counts.addLegend(offset=(-10, 10))
        self.plot_counts.setLabel("left", "计数")
        self.plot_counts.setLabel("bottom", "样本")
        self.plot_counts.showGrid(x=True, y=True, alpha=0.2)
        self.plot_counts.getAxis("left").setPen(mkPen("#64748b"))
        self.plot_counts.getAxis("bottom").setPen(mkPen("#64748b"))

        self.curve_wbc = self.plot_counts.plot(
            pen=mkPen(WBC_COLOR, width=2), name="WBC"
        )
        self.curve_rbc = self.plot_counts.plot(
            pen=mkPen(RBC_COLOR, width=2), name="RBC"
        )
        self.curve_plt = self.plot_counts.plot(
            pen=mkPen(PLT_COLOR, width=2), name="PLT"
        )

        layout.addWidget(self.plot_counts, 1)

        self.vr_layout = pg.GraphicsLayout()

        return group

    def _make_count_card(self, title: str, unit: str, color: str, ref: str) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(f"""
            QFrame {{ background: #1e293b; border: 1px solid #334155;
                     border-radius: 8px; border-left: 4px solid {color}; }}
        """)
        card.setMinimumWidth(180)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(title_lbl)

        val_lbl = QLabel("--")
        val_lbl.setStyleSheet(f"color: {color}; font-size: 28px; font-weight: bold;")
        val_lbl.setObjectName("value_label")
        layout.addWidget(val_lbl)

        unit_row = QHBoxLayout()
        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        unit_row.addWidget(unit_lbl)
        unit_row.addStretch(1)
        ref_lbl = QLabel(f"参考: {ref}")
        ref_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        unit_row.addWidget(ref_lbl)
        layout.addLayout(unit_row)

        card.val_label = val_lbl
        return card

    def _build_scatter_panel(self) -> QWidget:
        group = QGroupBox("🔵 白细胞散射光图 (FSC vs SSC)")
        group.setStyleSheet("""
            QGroupBox { color: #e2e8f0; border: 1px solid #334155; border-radius: 6px;
                       margin-top: 10px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        self.plot_scatter = PlotWidget()
        self.plot_scatter.setLabel("left", "SSC (侧向散射)")
        self.plot_scatter.setLabel("bottom", "FSC (前向散射)")
        self.plot_scatter.showGrid(x=True, y=True, alpha=0.2)
        self.plot_scatter.setXRange(0, 600, padding=0)
        self.plot_scatter.setYRange(0, 600, padding=0)
        self.plot_scatter.getAxis("left").setPen(mkPen("#64748b"))
        self.plot_scatter.getAxis("bottom").setPen(mkPen("#64748b"))

        legend = self.plot_scatter.addLegend(offset=(10, 10))

        self.scatter_items: Dict[str, ScatterPlotItem] = {}
        cell_types = [
            ("neutrophil", "中性粒"),
            ("lymphocyte", "淋巴细胞"),
            ("monocyte", "单核细胞"),
            ("eosinophil", "嗜酸性"),
            ("basophil", "嗜碱性"),
        ]
        for key, name in cell_types:
            color = DIFF_COLORS[key]
            item = ScatterPlotItem(
                size=7, pen=mkPen(color, width=0.5),
                brush=mkBrush(color + "80"), name=name
            )
            self.scatter_items[key] = item
            self.plot_scatter.addItem(item)

        layout.addWidget(self.plot_scatter, 1)

        return group

    def _build_calibration_panel(self) -> QWidget:
        group = QGroupBox("🎯 临床细胞质控标定")
        group.setStyleSheet("""
            QGroupBox { color: #e2e8f0; border: 1px solid #334155; border-radius: 6px;
                       margin-top: 10px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("采集样本数:"))
        self.calib_samples_spin = QSpinBox()
        self.calib_samples_spin.setRange(60, 1000)
        self.calib_samples_spin.setValue(300)
        self.calib_samples_spin.setSingleStep(30)
        control_row.addWidget(self.calib_samples_spin)

        self.btn_start_calib = QPushButton("🚀 启动标定")
        self.btn_start_calib.setStyleSheet("""
            QPushButton { background: #0d9488; color: white; padding: 6px 16px;
                         border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background: #14b8a6; }
            QPushButton:disabled { background: #475569; }
        """)
        self.btn_stop_calib = QPushButton("取消标定")
        self.btn_stop_calib.setEnabled(False)
        self.btn_stop_calib.setStyleSheet("""
            QPushButton { background: #ca8a04; color: white; padding: 6px 14px;
                         border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background: #eab308; }
            QPushButton:disabled { background: #475569; }
        """)
        self.btn_export_calib = QPushButton("📋 查看历史")
        self.btn_export_calib.setStyleSheet("""
            QPushButton { background: #475569; color: white; padding: 6px 14px;
                         border-radius: 4px; }
            QPushButton:hover { background: #64748b; }
        """)
        control_row.addSpacing(16)
        control_row.addWidget(self.btn_start_calib)
        control_row.addWidget(self.btn_stop_calib)
        control_row.addWidget(self.btn_export_calib)
        control_row.addStretch(1)
        layout.addLayout(control_row)

        progress_row = QHBoxLayout()
        self.calib_progress = QProgressBar()
        self.calib_progress.setRange(0, 100)
        self.calib_progress.setValue(0)
        self.calib_progress.setStyleSheet("""
            QProgressBar { border: 1px solid #475569; border-radius: 4px; text-align: center;
                          background: #0f172a; color: #e2e8f0; height: 20px; }
            QProgressBar::chunk { background: #0d9488; border-radius: 3px; }
        """)
        self.calib_status = QLabel("空闲")
        self.calib_status.setStyleSheet("color: #94a3b8;")
        progress_row.addWidget(self.calib_progress, 1)
        progress_row.addWidget(self.calib_status)
        layout.addLayout(progress_row)

        self.calib_table = QTableWidget(0, 9)
        self.calib_table.setHorizontalHeaderLabels([
            "通道", "目标值", "测量均值", "标准差", "CV%",
            "偏差%", "增益", "偏移", "判定"
        ])
        self.calib_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.calib_table.setStyleSheet("""
            QTableWidget { background: #0f172a; color: #e2e8f0;
                          gridline-color: #334155; border: 1px solid #334155; }
            QTableWidget::item { padding: 3px; }
            QHeaderView::section { background: #1e293b; color: #e2e8f0;
                                   padding: 4px; border: 1px solid #334155; }
        """)
        self.calib_table.verticalHeader().setVisible(False)
        self.calib_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.calib_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.calib_table, 1)

        return group

    def _build_differential_panel(self) -> QWidget:
        group = QGroupBox("🧬 白细胞5分类计数")
        group.setStyleSheet("""
            QGroupBox { color: #e2e8f0; border: 1px solid #334155; border-radius: 6px;
                       margin-top: 10px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        self.diff_table = QTableWidget(5, 3)
        self.diff_table.setHorizontalHeaderLabels(["细胞类型", "比例(%)", "参考范围(%)"])
        self.diff_table.horizontalHeader().setStretchLastSection(True)
        self.diff_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.diff_table.setStyleSheet("""
            QTableWidget { background: #0f172a; color: #e2e8f0;
                          gridline-color: #334155; border: 1px solid #334155; }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section { background: #1e293b; color: #e2e8f0;
                                   padding: 4px; border: 1px solid #334155; }
        """)
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.setEditTriggers(QTableWidget.NoEditTriggers)

        rows = [
            ("中性粒细胞 (NEUT)", "40 - 75", DIFF_COLORS["neutrophil"]),
            ("淋巴细胞 (LYMPH)", "20 - 50", DIFF_COLORS["lymphocyte"]),
            ("单核细胞 (MONO)", "1 - 10", DIFF_COLORS["monocyte"]),
            ("嗜酸性粒细胞 (EO)", "0.4 - 8", DIFF_COLORS["eosinophil"]),
            ("嗜碱性粒细胞 (BASO)", "0 - 2", DIFF_COLORS["basophil"]),
        ]
        for i, (name, ref, color) in enumerate(rows):
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(color))
            self.diff_table.setItem(i, 0, name_item)
            val_item = QTableWidgetItem("--")
            val_item.setTextAlignment(Qt.AlignCenter)
            self.diff_table.setItem(i, 1, val_item)
            ref_item = QTableWidgetItem(ref)
            ref_item.setTextAlignment(Qt.AlignCenter)
            ref_item.setForeground(QColor("#94a3b8"))
            self.diff_table.setItem(i, 2, ref_item)

        self.diff_table.setMinimumHeight(230)
        layout.addWidget(self.diff_table, 1)

        info_group = QGroupBox("ℹ️ 系统信息")
        info_group.setStyleSheet("""
            QGroupBox { color: #94a3b8; border: 1px dashed #334155; border-radius: 4px;
                       margin-top: 8px; padding: 6px; font-size: 11px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
        """)
        info_layout = QVBoxLayout(info_group)
        self.info_text = QLabel("串口: 未连接 | 数据库: 就绪 | 标定算法: Pandas")
        self.info_text.setStyleSheet("color: #64748b; font-size: 11px;")
        self.info_text.setWordWrap(True)
        info_layout.addWidget(self.info_text)
        layout.addWidget(info_group)

        return group

    def _setup_connections(self):
        self.btn_start.clicked.connect(self._start_collection)
        self.btn_stop.clicked.connect(self._stop_collection)
        self.btn_start_calib.clicked.connect(self._start_calibration)
        self.btn_stop_calib.clicked.connect(self._stop_calibration)
        self.btn_export_calib.clicked.connect(self._show_calibration_history)

        self.calibration_engine.set_progress_callback(self._on_calibration_progress)
        self.calibration_engine.set_finished_callback(self._on_calibration_finished)

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = list_available_ports()
        if ports:
            self.port_combo.addItems(ports)
        else:
            self.port_combo.addItem("COM1")
            self.port_combo.addItem("/dev/ttyUSB0")

    @Slot()
    def _start_collection(self):
        if self.serial_worker is not None and self.serial_worker.isRunning():
            return

        port_name = self.port_combo.currentText()
        try:
            baudrate = int(self.baud_combo.currentText())
        except ValueError:
            baudrate = 115200
        use_sim = self.sim_check.isChecked()

        self.serial_worker = SerialWorker()
        self.serial_worker.set_serial_config(port_name, baudrate, use_sim)
        self.serial_worker.data_received.connect(self._on_data_received)
        self.serial_worker.error_occurred.connect(self._on_serial_error)
        self.serial_worker.connection_status_changed.connect(self._on_connection_changed)
        self.serial_worker.finished.connect(self._on_worker_finished)
        self.serial_worker.start_work()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText(f"正在采集数据 ({port_name})...")

    @Slot()
    def _stop_collection(self):
        if self.serial_worker is not None:
            self.serial_worker.stop_work()
            self.serial_worker.wait(2000)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_label.setText("已停止采集")
        self.connection_label.setText("● 未连接")
        self.connection_label.setStyleSheet("color: #94a3b8; font-weight: bold;")

    @Slot(object)
    def _on_data_received(self, data: FlowCytometryData):
        corr = self.calibration_engine.get_current_correction

        wbc = data.wbc_count * corr("wbc").get("gain", 1.0)
        rbc = data.rbc_count * corr("rbc").get("gain", 1.0)
        plt = data.plt_count * corr("plt").get("gain", 1.0)

        self._update_count_cards(wbc, rbc, plt)
        self._update_count_plots(wbc, rbc, plt)
        self._update_scatter(data)
        self._update_differential(data)

        if self.calibration_engine.is_running():
            self.calibration_engine.process_data(data)

        conn_status = self.connection_label.text()
        mode = "模拟" if self.sim_check.isChecked() else "硬件"
        self.info_text.setText(
            f"串口: {conn_status} | 模式: {mode} | "
            f"样本速率: 10Hz | 数据库: 已连接"
        )

    def _update_count_cards(self, wbc, rbc, plt):
        self.wbc_value.val_label.setText(f"{wbc:.2f}")
        self.rbc_value.val_label.setText(f"{rbc:.2f}")
        self.plt_value.val_label.setText(f"{plt:.0f}")

        if wbc < 3.5 or wbc > 9.5:
            self.wbc_value.val_label.setStyleSheet(
                "color: #ef4444; font-size: 28px; font-weight: bold;"
            )
        else:
            self.wbc_value.val_label.setStyleSheet(
                f"color: {WBC_COLOR}; font-size: 28px; font-weight: bold;"
            )

        if rbc < 4.0 or rbc > 5.5:
            self.rbc_value.val_label.setStyleSheet(
                "color: #ef4444; font-size: 28px; font-weight: bold;"
            )
        else:
            self.rbc_value.val_label.setStyleSheet(
                f"color: {RBC_COLOR}; font-size: 28px; font-weight: bold;"
            )

        if plt < 150 or plt > 300:
            self.plt_value.val_label.setStyleSheet(
                "color: #ef4444; font-size: 28px; font-weight: bold;"
            )
        else:
            self.plt_value.val_label.setStyleSheet(
                f"color: {PLT_COLOR}; font-size: 28px; font-weight: bold;"
            )

    def _update_count_plots(self, wbc, rbc, plt):
        t = time.time()
        self._history_timestamps.append(t)
        self._history_wbc.append(wbc)
        self._history_rbc.append(rbc)
        self._history_plt.append(plt)

        idx = list(range(len(self._history_wbc)))
        self.curve_wbc.setData(idx, list(self._history_wbc))
        self.curve_rbc.setData(idx, list(self._history_rbc))
        self.curve_plt.setData(idx, list(self._history_plt))

    def _update_scatter(self, data: FlowCytometryData):
        cell_data_map = {
            "neutrophil": (data.neutrophil_fsc, data.neutrophil_ssc),
            "lymphocyte": (data.lymphocyte_fsc, data.lymphocyte_ssc),
            "monocyte": (data.monocyte_fsc, data.monocyte_ssc),
            "eosinophil": (data.eosinophil_fsc, data.eosinophil_ssc),
            "basophil": (data.basophil_fsc, data.basophil_ssc),
        }
        for key, (fsc_list, ssc_list) in cell_data_map.items():
            if not fsc_list or not ssc_list:
                continue
            n = min(len(fsc_list), len(ssc_list))
            pts = [{"pos": (fsc_list[i], ssc_list[i])} for i in range(n)]
            self.scatter_items[key].setPoints(pts)

    def _update_differential(self, data: FlowCytometryData):
        values = [
            data.neutrophil_pct,
            data.lymphocyte_pct,
            data.monocyte_pct,
            data.eosinophil_pct,
            data.basophil_pct,
        ]
        refs = [(40, 75), (20, 50), (1, 10), (0.4, 8), (0, 2)]

        for i, (v, (low, high)) in enumerate(zip(values, refs)):
            item = self.diff_table.item(i, 1)
            if item:
                item.setText(f"{v:.1f}")
                if v < low or v > high:
                    item.setForeground(QColor("#ef4444"))
                else:
                    item.setForeground(QColor("#22c55e"))

    @Slot(str)
    def _on_serial_error(self, msg: str):
        self.status_label.setText(f"错误: {msg}")

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, msg: str):
        if connected:
            self.connection_label.setText(f"● {msg}")
            self.connection_label.setStyleSheet("color: #22c55e; font-weight: bold;")
        else:
            self.connection_label.setText(f"● {msg}")
            self.connection_label.setStyleSheet("color: #94a3b8; font-weight: bold;")

    @Slot()
    def _on_worker_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    @Slot()
    def _start_calibration(self):
        if self.serial_worker is None or not self.serial_worker.is_running():
            QMessageBox.warning(
                self, "无法启动标定",
                "请先启动数据采集，再进行质控标定。"
            )
            return

        samples = self.calib_samples_spin.value()
        self.calib_table.setRowCount(0)
        ok = self.calibration_engine.start_calibration(samples)
        if ok:
            self.btn_start_calib.setEnabled(False)
            self.btn_stop_calib.setEnabled(True)
            self.calib_status.setText("正在进行标定...")

    @Slot()
    def _stop_calibration(self):
        self.calibration_engine.stop_calibration()
        self.btn_start_calib.setEnabled(True)
        self.btn_stop_calib.setEnabled(False)
        self.calib_status.setText("标定已取消")

    @Slot(object)
    def _on_calibration_progress(self, progress: CalibrationProgress):
        self.calib_progress.setValue(int(progress.progress_pct))
        self.calib_status.setText(progress.status_message)

    def _on_calibration_finished(self, results: List[CalibrationResult]):
        self.btn_start_calib.setEnabled(True)
        self.btn_stop_calib.setEnabled(False)

        self.calib_table.setRowCount(0)
        for r in results:
            row = self.calib_table.rowCount()
            self.calib_table.insertRow(row)

            items = [
                (r.label, Qt.AlignLeft),
                (f"{r.target_value:.3f}", Qt.AlignCenter),
                (f"{r.measured_mean:.3f}", Qt.AlignCenter),
                (f"{r.measured_std:.4f}", Qt.AlignCenter),
                (f"{r.measured_cv:.2f}%", Qt.AlignCenter),
                (f"{r.deviation:+.2f}%", Qt.AlignCenter),
                (f"{r.gain:.6f}", Qt.AlignCenter),
                (f"{r.offset:+.3f}", Qt.AlignCenter),
                ("✓ PASS" if r.passed else "✗ FAIL", Qt.AlignCenter),
            ]
            for col, (text, align) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(align | Qt.AlignVCenter)
                if col == 5:
                    color = QColor("#22c55e") if abs(r.deviation) <= 3.0 else QColor("#ef4444")
                    item.setForeground(color)
                if col == 8:
                    color = QColor("#22c55e") if r.passed else QColor("#ef4444")
                    item.setForeground(color)
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.calib_table.setItem(row, col, item)

        passed = sum(1 for r in results if r.passed)
        total = len(results)
        self.calib_status.setText(
            f"标定完成: {passed}/{total} 通道通过 "
            f"(偏差≤±3%, CV≤5%) | 参数已保存至数据库"
        )

    @Slot()
    def _show_calibration_history(self):
        dlg = QWidget(self)
        dlg.setWindowTitle("标定历史记录")
        dlg.resize(900, 500)
        dlg.setWindowFlags(Qt.Window)

        layout = QVBoxLayout(dlg)

        row = QHBoxLayout()
        row.addWidget(QLabel("选择通道:"))
        ch_combo = QComboBox()
        from calibration import CHANNEL_LABELS
        ch_combo.addItems([CHANNEL_LABELS.get(c, c) for c in CHANNELS])
        row.addWidget(ch_combo)
        btn_load = QPushButton("加载记录")
        row.addWidget(btn_load)
        row.addStretch(1)
        layout.addLayout(row)

        table = QTableWidget(0, 9)
        table.setHorizontalHeaderLabels([
            "ID", "时间", "目标值", "测量均值", "Std",
            "偏差%", "增益", "样本数", "标定员"
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setStyleSheet("""
            QTableWidget { background: #0f172a; color: #e2e8f0;
                          gridline-color: #334155; }
            QHeaderView::section { background: #1e293b; color: #e2e8f0; padding: 4px; }
        """)
        layout.addWidget(table, 1)

        def load_history():
            channel = CHANNELS[ch_combo.currentIndex()]
            recs = self.db.get_calibration_history(channel, 100)
            table.setRowCount(0)
            for rec in recs:
                r = table.rowCount()
                table.insertRow(r)
                row_items = [
                    str(rec.id),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.created_at)),
                    f"{rec.target_value:.3f}",
                    f"{rec.measured_mean:.3f}",
                    f"{rec.measured_std:.4f}",
                    f"{rec.deviation:+.2f}%",
                    f"{rec.gain:.6f}",
                    str(rec.sample_count),
                    rec.operator,
                ]
                for c, text in enumerate(row_items):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(Qt.AlignCenter)
                    if c == 5:
                        color = QColor("#22c55e") if abs(rec.deviation) <= 3.0 else QColor("#ef4444")
                        it.setForeground(color)
                    table.setItem(r, c, it)

        btn_load.clicked.connect(load_history)
        load_history()

        dlg.show()

    def closeEvent(self, event):
        if self.serial_worker is not None and self.serial_worker.isRunning():
            self.serial_worker.stop_work()
            self.serial_worker.wait(2000)
        super().closeEvent(event)
