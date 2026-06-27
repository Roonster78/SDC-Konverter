#!/usr/bin/env python3
"""
SDC-Konverter – Desktop-App auf Qt6 (PySide6).
Native Qt-Oberfläche + native Datei-Dialoge (Haupt-Thread → kein Freeze).
Konverter-Kern unverändert (sdc_core, poppler-frei via pdfplumber).
Komplett selbst-enthaltend: PyInstaller bündelt Qt mit, keine Laufzeit nötig.
"""

import os
import sys
import io
import base64
import contextlib
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTextEdit, QFileDialog, QFrame, QSizePolicy,
)

import sdc_core as C
try:
    import sdc_banner
    _BANNER_BYTES = base64.b64decode(sdc_banner.BANNER_B64)
except Exception:
    _BANNER_BYTES = None

ROUNDS = [
    ("01", "Osterhofen"), ("02", "Sand"), ("03", "Landshut"),
    ("04", "Welden"), ("05", "Laupheim"), ("06", "Senden"),
]
EXPECTED_CLASSES = {"OR8 Expert", "OR8 Hobby", "ORE", "ORET", "ORT", "Jugendfinale"}

ORANGE = "#ff6900"

STYLE = """
* { font-family: 'Segoe UI','Helvetica Neue',Helvetica,Arial,sans-serif; font-size: 13px; color: #1c1c1c; }
QWidget#root { background: #eef0f2; }
QWidget#head { background: #f3f3f3; border: 1px solid #e6e6e6; border-bottom: 3px solid #ff6900; }
QLabel#kicker { color: #ff6900; font-size: 11px; font-weight: 700; }
QLabel#title  { font-size: 21px; font-weight: 700; }
QLabel#sub    { color: #888; font-size: 12px; }
QWidget#body  { background: #ffffff; border: 1px solid #e6e6e6; border-top: none; }
QLabel.step   { font-weight: 700; }
QLabel.muted  { color: #888; }
QLabel#badgeOk { background: #eafaef; color: #1e7d3a; border: 1px solid #bfe3c4; border-radius: 12px; padding: 5px 12px; font-weight: 700; }
QLabel#badgeRc { background: #fdeeed; color: #a3302c; border: 1px solid #f0c4c2; border-radius: 12px; padding: 5px 12px; font-weight: 700; }
QComboBox { padding: 8px 10px; border: 1px solid #ddd; border-radius: 8px; background: #fff; min-height: 20px; }
QPushButton#accent { background: #ff6900; color: #fff; border: none; border-radius: 8px; padding: 10px 18px; font-weight: 700; }
QPushButton#accent:hover { background: #e85f00; }
QPushButton#accent:disabled { background: #f0bd99; }
QPushButton#sec { background: #ececec; color: #1c1c1c; border: none; border-radius: 8px; padding: 9px 14px; font-weight: 600; }
QPushButton#sec:hover { background: #e0e0e0; }
QTextEdit#result { border: 1px solid #e6e6e6; border-radius: 8px; background: #fff; }
"""


# ─── Kern-Aufrufe (Qt-frei, damit headless testbar) ──────────────────────────

def detect_format(folder):
    pdfs = sorted(Path(folder).glob("*.pdf")) + sorted(Path(folder).glob("*.PDF"))
    rc = False
    try:
        for p in pdfs:
            if C.is_race_control(C.extract_plain(p)):
                rc = True
                break
    except Exception:
        rc = False
    return {"folder": folder, "pdf_count": len(pdfs), "race_control": rc}


def run_conversion(num, ort, folder, nennliste, needs_nenn):
    try:
        pdfs = sorted(Path(folder).glob("*.pdf")) + sorted(Path(folder).glob("*.PDF"))
        if not pdfs:
            return {"ok": False, "error": "Keine PDF-Dateien im gewählten Ordner."}
        if needs_nenn and not nennliste:
            return {"ok": False, "error": "Dieser Lauf (Race-Control) benötigt die Nennliste."}

        buf = io.StringIO()
        out_path = Path(folder) / f"{num} {ort}_Vereinswertung.csv"
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            lookup = C.load_nennliste_html(nennliste) if needs_nenn else None
            drivers, stats, no_club, unmatched = C.process_folder(Path(folder), lookup)
            C.write_csv(drivers, out_path)

        total = len(drivers)
        detected = sorted({kl for (_, kl, _, _, _) in stats})
        clubs = sorted({d["club"] for d in drivers if d["club"]})

        warnings = []
        if total < 10:
            warnings.append(f"Nur {total} Fahrer erkannt – ungewöhnlich wenige. Ist das der richtige Ordner für „{ort}“?")
        if any(kl == "Unbekannt" for (_, kl, _, _, _) in stats):
            warnings.append("Mindestens eine Datei konnte keiner Klasse zugeordnet werden – Dateinamen prüfen.")
        missing = EXPECTED_CLASSES - set(detected)
        if missing:
            warnings.append("Nicht alle Standard-Klassen vorhanden (fehlt: " + ", ".join(sorted(missing)) +
                            "). Falls dieser Lauf alle Klassen hatte: PDFs vollständig?")
        empty_club = sum(1 for d in drivers if not d["club"])
        if empty_club:
            warnings.append(f"{empty_club} Fahrer ohne Verein in der CSV – bitte prüfen.")

        return {
            "ok": True, "total": total, "csv_name": out_path.name, "csv_dir": str(out_path.parent),
            "stats": [{"file": fn, "klasse": kl, "found": f, "new": n, "fmt": fm} for (fn, kl, f, n, fm) in stats],
            "detected": detected, "club_count": len(clubs),
            "unmatched": sorted(set(unmatched)), "no_club": sorted(set(no_club)),
            "warnings": warnings,
        }
    except SystemExit:
        return {"ok": False, "error": "Keine PDF-Dateien gefunden."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def result_html(res):
    if not res.get("ok"):
        return f'<p style="color:#a3302c;font-weight:bold">Fehler: {_esc(res.get("error","unbekannt"))}</p>'
    h = [f'<h3 style="color:#1e7d3a;margin:0">&#10003; CSV erstellt: {_esc(res["csv_name"])}</h3>']
    h.append(f'<p style="margin:6px 0 2px"><span style="font-size:24px;color:#ff7a1a"><b>{res["total"]} Fahrer</b></span>'
             f'&nbsp;&middot;&nbsp;{res["club_count"]} Vereine</p>')
    h.append(f'<p style="color:#666;margin:2px 0">Klassen: {_esc(", ".join(res["detected"]))}</p>')
    h.append('<table border="0" cellspacing="0" cellpadding="5" width="100%">')
    h.append('<tr bgcolor="#f1f1f1"><td><b>Datei</b></td><td><b>Klasse</b></td><td><b>Format</b></td>'
             '<td align="right"><b>gef.</b></td><td align="right"><b>neu</b></td></tr>')
    for s in res["stats"]:
        h.append(f'<tr><td>{_esc(s["file"])}</td><td>{_esc(s["klasse"])}</td><td>{_esc(s["fmt"])}</td>'
                 f'<td align="right">{s["found"]}</td><td align="right">{s["new"]}</td></tr>')
    h.append('</table>')
    for w in res.get("warnings", []):
        h.append(f'<p style="color:#9a3412;background:#fff7ed;padding:8px;margin:8px 0"><b>&#9888; {_esc(w)}</b></p>')
    if res.get("unmatched"):
        h.append(f'<p style="color:#9a3412;margin:8px 0 2px"><b>&#9888; {len(res["unmatched"])} Fahrer NICHT in der Nennliste:</b></p><ul>')
        h += [f'<li>{_esc(n)}</li>' for n in res["unmatched"]]
        h.append('</ul>')
    if res.get("no_club"):
        h.append(f'<p style="color:#9a3412;margin:8px 0 2px"><b>&#9888; {len(res["no_club"])} Fahrer in Nennliste, aber OHNE Verein:</b></p><ul>')
        h += [f'<li>{_esc(n)}</li>' for n in res["no_club"]]
        h.append('</ul>')
    return "".join(h)


# ─── Worker-Thread ───────────────────────────────────────────────────────────

class Worker(QThread):
    done = Signal(object)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        try:
            res = self._fn(*self._args)
        except Exception as e:
            res = {"_error": str(e)}
        self.done.emit(res)


# ─── Hauptfenster ────────────────────────────────────────────────────────────

class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.folder = None
        self.nennliste = None
        self.needs_nenn = False
        self.csv_dir = None
        self._workers = []

        self.setObjectName("root")
        self.setWindowTitle("SDC-Konverter")
        self.resize(780, 880)
        self.setMinimumSize(660, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        # Kopf
        head = QWidget(); head.setObjectName("head")
        hv = QVBoxLayout(head); hv.setContentsMargins(0, 0, 0, 12); hv.setSpacing(2)
        if _BANNER_BYTES:
            pix = QPixmap(); pix.loadFromData(_BANNER_BYTES)
            blbl = QLabel(); blbl.setPixmap(pix); blbl.setAlignment(Qt.AlignCenter)
            blbl.setStyleSheet("padding:12px 0 4px")
            hv.addWidget(blbl)
        ht = QVBoxLayout(); ht.setContentsMargins(18, 0, 18, 0); ht.setSpacing(1)
        k = QLabel("SÜDDEUTSCHLAND-CUP"); k.setObjectName("kicker"); ht.addWidget(k)
        t = QLabel("PDF → CSV Konverter"); t.setObjectName("title"); ht.addWidget(t)
        s = QLabel("Erzeugt aus den Ergebnislisten eines Laufs die CSV für die Vereinswertung.")
        s.setObjectName("sub"); ht.addWidget(s)
        hv.addLayout(ht)
        outer.addWidget(head)

        # Körper
        body = QWidget(); body.setObjectName("body")
        bv = QVBoxLayout(body); bv.setContentsMargins(18, 14, 18, 18); bv.setSpacing(8)

        bv.addWidget(self._step("1.  Lauf auswählen"))
        self.round_box = QComboBox()
        self.round_box.addItems([f"{n} – {ort}" for n, ort in ROUNDS])
        self.round_box.setCurrentIndex(len(ROUNDS) - 1)
        self.round_box.setMaximumWidth(320)
        bv.addWidget(self.round_box)

        bv.addWidget(self._step("2.  Ordner mit den PDF-Ergebnislisten"))
        r2 = QHBoxLayout()
        b1 = QPushButton("Ordner wählen…"); b1.setObjectName("accent"); b1.clicked.connect(self.pick_folder)
        r2.addWidget(b1)
        self.folder_lbl = QLabel("– noch nichts gewählt –"); self.folder_lbl.setProperty("class", "muted")
        self.folder_lbl.setStyleSheet("color:#888"); r2.addWidget(self.folder_lbl); r2.addStretch()
        bv.addLayout(r2)
        self.badge = QLabel(""); self.badge.setVisible(False); bv.addWidget(self.badge, 0, Qt.AlignLeft)

        # Nennliste (Schritt 3) – nur bei Race-Control
        self.nenn_widget = QWidget()
        nv = QVBoxLayout(self.nenn_widget); nv.setContentsMargins(0, 6, 0, 0); nv.setSpacing(4)
        nv.addWidget(self._step("3.  Nennliste (HTML) – für diesen Lauf erforderlich"))
        hint = QLabel("Dieser Lauf nutzt „Race-Control“ (keine Vereinsspalte). Bitte die von myrcm.ch als HTML gespeicherte Nennliste wählen.")
        hint.setWordWrap(True); hint.setStyleSheet("color:#888"); nv.addWidget(hint)
        r3 = QHBoxLayout()
        b2 = QPushButton("Nennliste wählen…"); b2.setObjectName("sec"); b2.clicked.connect(self.pick_nennliste)
        r3.addWidget(b2)
        self.nenn_lbl = QLabel("– keine –"); self.nenn_lbl.setStyleSheet("color:#888")
        r3.addWidget(self.nenn_lbl); r3.addStretch()
        nv.addLayout(r3)
        self.nenn_widget.setVisible(False)
        bv.addWidget(self.nenn_widget)

        self.go = QPushButton("Konvertieren  →  CSV erstellen"); self.go.setObjectName("accent")
        self.go.setStyleSheet("font-size:15px;padding:12px 20px"); self.go.clicked.connect(self.convert)
        bv.addWidget(self.go, 0, Qt.AlignLeft)

        self.result = QTextEdit(); self.result.setObjectName("result"); self.result.setReadOnly(True)
        self.result.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        bv.addWidget(self.result, 1)

        self.open_btn = QPushButton("Ordner öffnen"); self.open_btn.setObjectName("sec")
        self.open_btn.clicked.connect(self.open_dir); self.open_btn.setVisible(False)
        bv.addWidget(self.open_btn, 0, Qt.AlignLeft)

        outer.addWidget(body, 1)

    def _step(self, text):
        lbl = QLabel(text); lbl.setProperty("class", "step")
        lbl.setStyleSheet("font-weight:700;margin-top:8px")
        return lbl

    # ── Aktionen ─────────────────────────────────────────────────────────────
    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner mit den PDF-Ergebnislisten wählen")
        if not d:
            return
        self.folder = d
        self.folder_lbl.setText(os.path.basename(d) + "  (prüfe Format …)")
        self.badge.setVisible(False)
        self.nenn_widget.setVisible(False)
        w = Worker(detect_format, d)
        w.done.connect(self._on_detect)
        self._workers.append(w); w.start()

    def _on_detect(self, res):
        self.folder_lbl.setText(f"{os.path.basename(res['folder'])}  ({res['pdf_count']} PDF-Dateien)")
        self.needs_nenn = res["race_control"]
        if res["race_control"]:
            self.badge.setObjectName("badgeRc")
            self.badge.setText("Format: Race-Control – Nennliste erforderlich")
            self.nenn_widget.setVisible(True)
        else:
            self.badge.setObjectName("badgeOk")
            self.badge.setText("Format: RCM – keine Nennliste nötig  ✓")
            self.nenn_widget.setVisible(False)
        self.badge.setStyleSheet("")  # ObjectName-Style neu anwenden
        self.style().unpolish(self.badge); self.style().polish(self.badge)
        self.badge.setVisible(True)

    def pick_nennliste(self):
        f, _ = QFileDialog.getOpenFileName(self, "Nennliste (HTML) wählen", "",
                                           "HTML-Dateien (*.html *.htm);;Alle Dateien (*.*)")
        if f:
            self.nennliste = f
            self.nenn_lbl.setText(os.path.basename(f))

    def convert(self):
        if not self.folder:
            self._msg("Bitte zuerst den Ordner mit den PDF-Dateien wählen.")
            return
        if self.needs_nenn and not self.nennliste:
            self._msg("Dieser Lauf (Race-Control) benötigt die Nennliste. Bitte unter Schritt 3 auswählen.")
            return
        num, ort = ROUNDS[self.round_box.currentIndex()]
        self.go.setEnabled(False); self.go.setText("Konvertiere …")
        w = Worker(run_conversion, num, ort, self.folder, self.nennliste, self.needs_nenn)
        w.done.connect(self._on_convert)
        self._workers.append(w); w.start()

    def _on_convert(self, res):
        self.go.setEnabled(True); self.go.setText("Konvertieren  →  CSV erstellen")
        self.result.setHtml(result_html(res))
        if res.get("ok"):
            self.csv_dir = res.get("csv_dir")
            self.open_btn.setVisible(True)
        else:
            self.open_btn.setVisible(False)

    def open_dir(self):
        if not self.csv_dir:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.csv_dir)  # noqa
            elif sys.platform == "darwin":
                subprocess.run(["open", self.csv_dir])
            else:
                subprocess.run(["xdg-open", self.csv_dir])
        except Exception:
            pass

    def _msg(self, text):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Hinweis", text)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = Main()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
