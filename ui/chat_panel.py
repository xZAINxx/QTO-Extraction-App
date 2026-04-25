"""Chat panel — natural-language Q&A over the current takeoff.

Phase-6 deliverable. Wraps :class:`ai.chat_agent.ChatAgent` in a slim
PyQt6 dialog/dock with:

* a transcript view (most recent at the bottom)
* an input box + Ask button
* clickable citation chips that emit ``citation_clicked(page, sheet)``
  so :class:`MainWindow` can navigate the embedded PDF viewer

The heavy lifting (prompt assembly, parsing, history management) lives
in :mod:`ai.chat_agent`. This file is intentionally a thin view layer.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QThread, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QSizePolicy, QSpacerItem,
)

from ai.chat_agent import ChatAgent, ChatAnswer, Citation
from core.qto_row import QTORow
from ui.theme import (
    SURFACE_1, SURFACE_2, SURFACE_3, BORDER_HEX, TEXT_1, TEXT_2, TEXT_3,
    INDIGO, EMERALD, AMBER, FONT_MONO,
)


_LOG = logging.getLogger(__name__)


class _AskWorker(QObject):
    finished = pyqtSignal(object)        # ChatAnswer
    failed = pyqtSignal(str)

    def __init__(self, agent: ChatAgent, question: str):
        super().__init__()
        self._agent = agent
        self._question = question

    def run(self):
        try:
            ans = self._agent.ask(self._question)
            self.finished.emit(ans)
        except Exception as exc:
            _LOG.exception("chat ask failed")
            self.failed.emit(str(exc))


class _MessageBubble(QFrame):
    """One message in the transcript (user or assistant)."""

    citation_clicked = pyqtSignal(int, str)   # (page, sheet)

    def __init__(
        self,
        text: str,
        is_user: bool,
        citations: Optional[list[Citation]] = None,
        notes: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("chatBubble")
        bg = SURFACE_3 if is_user else SURFACE_2
        border = INDIGO if is_user else BORDER_HEX
        self.setStyleSheet(
            f"#chatBubble {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: 10px; padding: 8px 10px; }}"
            f"QLabel {{ background: transparent; color: {TEXT_1}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        role = QLabel("YOU" if is_user else "ASSISTANT")
        role_font = QFont()
        role_font.setBold(True)
        role_font.setPointSize(8)
        role.setFont(role_font)
        role.setStyleSheet(
            f"color: {INDIGO if is_user else EMERALD}; letter-spacing: 0.08em;"
        )
        layout.addWidget(role)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(body)

        if citations:
            chips = QFrame()
            chips_layout = QHBoxLayout(chips)
            chips_layout.setContentsMargins(0, 4, 0, 0)
            chips_layout.setSpacing(6)
            label = QLabel("Sources:")
            label.setStyleSheet(f"color: {TEXT_3}; font-size: 11px;")
            chips_layout.addWidget(label)
            for c in citations[:8]:
                btn = QPushButton(self._format_chip(c))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ background: {SURFACE_1}; color: {INDIGO}; "
                    f"border: 1px solid {INDIGO}; border-radius: 6px; "
                    f"padding: 2px 8px; font-size: 11px; font-family: {FONT_MONO}; "
                    "font-weight: 600; }}"
                    f"QPushButton:hover {{ background: {INDIGO}; color: white; }}"
                )
                btn.clicked.connect(
                    lambda _checked=False, page=c.page, sheet=c.sheet:
                    self.citation_clicked.emit(page, sheet)
                )
                chips_layout.addWidget(btn)
            chips_layout.addStretch()
            layout.addWidget(chips)

        if notes:
            warn = QLabel(notes)
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {AMBER}; font-size: 11px; padding-top: 4px;")
            layout.addWidget(warn)

    @staticmethod
    def _format_chip(c: Citation) -> str:
        if c.sheet and c.page:
            return f"{c.sheet} · p.{c.page}"
        if c.sheet:
            return c.sheet
        if c.page:
            return f"p.{c.page}"
        return f"row {c.row_id}"


class ChatPanel(QDialog):
    """Modeless dialog that hosts the chat experience.

    Constructed lazily from ``MainWindow._open_chat``. The dialog stays
    around between sessions so we can reuse cached prompts; calling
    :meth:`set_rows` after every extraction keeps the row table fresh.
    """

    citation_clicked = pyqtSignal(int, str)   # (page, sheet)

    def __init__(self, ai_client, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Chat with takeoff")
        self.resize(560, 680)
        self.setStyleSheet(
            f"QDialog {{ background: {SURFACE_1}; color: {TEXT_1}; }}"
            f"QLineEdit {{ background: {SURFACE_2}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {INDIGO}; }}"
        )

        self._agent = ChatAgent(ai_client)
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_AskWorker] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        header = QLabel("Ask anything about the current takeoff")
        header.setStyleSheet(f"color: {TEXT_2}; font-size: 12px;")
        outer.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"background: {SURFACE_1};")
        self._transcript = QWidget()
        self._transcript.setStyleSheet(f"background: {SURFACE_1};")
        self._transcript_layout = QVBoxLayout(self._transcript)
        self._transcript_layout.setContentsMargins(2, 2, 2, 2)
        self._transcript_layout.setSpacing(8)
        self._transcript_layout.addStretch()
        self._scroll.setWidget(self._transcript)
        outer.addWidget(self._scroll, 1)

        # Status row (shown while a question is in flight or after errors).
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {TEXT_3}; font-size: 11px;")
        outer.addWidget(self._status)

        # Input + send button.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "e.g. How many windows are on the south elevation?"
        )
        self._input.returnPressed.connect(self._on_send)
        bar.addWidget(self._input, 1)

        self._send_btn = QPushButton("Ask")
        self._send_btn.setFixedWidth(96)
        self._send_btn.clicked.connect(self._on_send)
        bar.addWidget(self._send_btn)
        outer.addLayout(bar)

        self._add_message(
            text=(
                "I can answer questions about the rows you've extracted. Try "
                "things like\n"
                "  • \"Total cast stone coping LF?\"\n"
                "  • \"Which sheets have allowances?\"\n"
                "  • \"How many windows on A-201?\""
            ),
            is_user=False,
        )

    # ── Public API ───────────────────────────────────────────────────────

    def set_rows(self, rows: list[QTORow]):
        self._agent.set_rows(rows)
        if not self._agent.has_rows:
            self._status.setText("No rows loaded yet — run a takeoff first.")
        else:
            count = len(self._agent._rows_payload)
            self._status.setText(f"Ready — {count} rows in context.")

    def reset(self):
        # Clear transcript widgets without trashing the trailing stretch.
        while self._transcript_layout.count() > 1:
            item = self._transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._agent.reset_history()
        self._add_message("History cleared.", is_user=False)

    # ── Internal slots ───────────────────────────────────────────────────

    def _on_send(self):
        question = self._input.text().strip()
        if not question:
            return
        self._input.clear()
        self._add_message(question, is_user=True)
        self._set_busy(True)

        # Run the AI call off the UI thread so the dialog stays responsive.
        self._worker_thread = QThread(self)
        self._worker = _AskWorker(self._agent, question)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_answer)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.start()

    def _on_answer(self, answer: ChatAnswer):
        self._set_busy(False)
        self._add_message(
            text=answer.text or "(no response)",
            is_user=False,
            citations=answer.citations,
            notes=answer.notes,
        )

    def _on_failure(self, msg: str):
        self._set_busy(False)
        self._add_message(text=f"Error: {msg}", is_user=False)

    def _set_busy(self, busy: bool):
        self._send_btn.setEnabled(not busy)
        self._input.setEnabled(not busy)
        self._status.setText("Thinking..." if busy else "")

    def _add_message(
        self,
        text: str,
        is_user: bool,
        citations: Optional[list[Citation]] = None,
        notes: str = "",
    ):
        bubble = _MessageBubble(
            text=text,
            is_user=is_user,
            citations=citations,
            notes=notes,
            parent=self._transcript,
        )
        bubble.citation_clicked.connect(self.citation_clicked.emit)
        # Insert before the trailing stretch so newest message is at the bottom.
        self._transcript_layout.insertWidget(
            self._transcript_layout.count() - 1, bubble,
        )
        # Defer scroll to allow layout to settle.
        bar = self._scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
            # Re-apply on the next event loop tick — Qt sometimes
            # measures the new bubble after this returns.
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
