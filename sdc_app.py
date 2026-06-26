#!/usr/bin/env python3
"""
SDC-Konverter – Desktop-App (pywebview).
Oberfläche = HTML/CSS im Design der Plugin-Upload-Seite, gerendert vom
System-Webview (WebKit auf Mac, WebView2 auf Windows). Konverter-Kern
unverändert (sdc_core, poppler-frei via pdfplumber).
"""

import io
import os
import sys
import contextlib
import subprocess
from pathlib import Path

import webview

import sdc_core as C
try:
    import sdc_banner
    BANNER = "data:image/png;base64," + sdc_banner.BANNER_B64
except Exception:
    BANNER = ""

ROUNDS = [
    ("01", "Osterhofen"), ("02", "Sand"), ("03", "Landshut"),
    ("04", "Welden"), ("05", "Laupheim"), ("06", "Senden"),
]

# Standard-Klassen eines vollständigen Laufs (für Plausibilitätsprüfung)
EXPECTED_CLASSES = {"OR8 Expert", "OR8 Hobby", "ORE", "ORET", "ORT", "Jugendfinale"}


class Api:
    def __init__(self):
        self.window = None

    # ── Datei-Dialoge ────────────────────────────────────────────────────────
    def pick_folder(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return None
        folder = res[0] if isinstance(res, (list, tuple)) else res
        pdfs = sorted(Path(folder).glob("*.pdf")) + sorted(Path(folder).glob("*.PDF"))
        race_control = False
        try:
            for p in pdfs:
                if C.is_race_control(C.extract_plain(p)):
                    race_control = True
                    break
        except Exception:
            race_control = False
        return {
            "folder": folder,
            "name": os.path.basename(folder),
            "pdf_count": len(pdfs),
            "race_control": race_control,
        }

    def pick_nennliste(self):
        res = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("HTML-Dateien (*.html;*.htm)", "Alle Dateien (*.*)"))
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        return {"path": path, "name": os.path.basename(path)}

    def open_folder(self, folder):
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa
            elif sys.platform == "darwin":
                subprocess.run(["open", folder])
            else:
                subprocess.run(["xdg-open", folder])
        except Exception:
            pass
        return True

    # ── Konvertierung + Prüfroutinen ─────────────────────────────────────────
    def convert(self, num, ort, folder, nennliste, needs_nenn):
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
            file_stats = [
                {"file": fn, "klasse": kl, "found": found, "new": new, "fmt": fmt}
                for (fn, kl, found, new, fmt) in stats
            ]
            detected = sorted({kl for (_, kl, _, _, _) in stats})
            clubs = sorted({d["club"] for d in drivers if d["club"]})

            # ── Prüfroutinen / Warnungen ────────────────────────────────────
            warnings = []
            if total < 10:
                warnings.append(f"Nur {total} Fahrer erkannt – ungewöhnlich wenige. "
                                f"Ist das der richtige Ordner für „{ort}“?")
            if any(kl == "Unbekannt" for (_, kl, _, _, _) in stats):
                warnings.append("Mindestens eine Datei konnte keiner Klasse zugeordnet "
                                "werden – bitte Dateinamen prüfen.")
            missing = EXPECTED_CLASSES - set(detected)
            if missing:
                warnings.append("Nicht alle Standard-Klassen vorhanden (fehlt: "
                                + ", ".join(sorted(missing)) + "). "
                                "Falls dieser Lauf alle Klassen hatte: PDFs vollständig?")
            empty_club = sum(1 for d in drivers if not d["club"])
            if empty_club:
                warnings.append(f"{empty_club} Fahrer ohne Verein in der CSV – bitte prüfen.")

            return {
                "ok": True,
                "total": total,
                "csv_name": out_path.name,
                "csv_dir": str(out_path.parent),
                "stats": file_stats,
                "detected": detected,
                "club_count": len(clubs),
                "unmatched": sorted(set(unmatched)),
                "no_club": sorted(set(no_club)),
                "warnings": warnings,
            }
        except SystemExit:
            return {"ok": False, "error": "Keine PDF-Dateien gefunden."}
        except Exception as e:
            return {"ok": False, "error": str(e)}


HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  html,body { margin:0; padding:0; background:#eef0f2; color:#1c1c1c;
              font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .sdcw { max-width:720px; margin:0 auto; padding:16px 14px 30px; }
  .head { background:#f3f3f3; border:1px solid #e6e6e6; border-bottom:3px solid #ff6900;
          border-radius:8px 8px 0 0; overflow:hidden; }
  .head img { display:block; width:100%; height:auto; }
  .headtext { padding:12px 18px; }
  .kicker { font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:#ff6900; font-weight:700; }
  .title { font-size:21px; font-weight:700; margin:1px 0 0; }
  .sub { font-size:13px; color:#888; margin-top:3px; }
  .body { background:#fff; border:1px solid #e6e6e6; border-top:none;
          border-radius:0 0 8px 8px; padding:6px 16px 18px; }
  .step { font-size:13px; font-weight:700; margin:16px 0 6px; }
  select { width:100%; max-width:320px; padding:10px 12px; font-size:15px;
           border:1px solid #ddd; border-radius:8px; background:#fff; }
  .row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .btn { display:inline-flex; align-items:center; gap:7px; background:#ff6900; color:#fff;
         border:none; border-radius:8px; padding:11px 16px; font-size:15px; font-weight:700; cursor:pointer; }
  .btn:hover { background:#e85f00; }
  .btn:disabled { background:#f0bd99; cursor:default; }
  .btn.sec { background:#eee; color:#1c1c1c; font-weight:600; }
  .btn.sec:hover { background:#e2e2e2; }
  .muted { font-size:13px; color:#888; }
  .badge { display:inline-block; padding:5px 12px; border-radius:14px; font-size:13px; font-weight:700; margin-top:8px; }
  .badge.ok { background:#eafaef; color:#1e7d3a; border:1px solid #bfe3c4; }
  .badge.rc { background:#fdeeed; color:#a3302c; border:1px solid #f0c4c2; }
  .hidden { display:none; }
  .gobtn { margin-top:18px; font-size:16px; padding:13px 20px; }
  .res { margin-top:18px; }
  .res-card { border:1px solid #e6e6e6; border-radius:10px; padding:14px 16px; }
  .res-ok { border-color:#bfe3c4; background:#f4fbf5; }
  .res-err { border-color:#f0c4c2; background:#fdeeed; color:#a3302c; font-weight:600; }
  .res-h { font-size:16px; font-weight:700; color:#1e7d3a; display:flex; align-items:center; gap:8px; }
  .res-total { font-size:28px; font-weight:700; color:#ff7a1a; margin:6px 0 2px; }
  table.st { width:100%; border-collapse:collapse; font-size:13px; margin-top:10px; }
  table.st th { text-align:left; color:#999; font-weight:700; font-size:11px; text-transform:uppercase;
                border-bottom:2px solid #eee; padding:6px 6px; }
  table.st td { padding:6px 6px; border-bottom:1px solid #f2f2f2; }
  table.st td.n { text-align:right; font-weight:700; }
  .warn { background:#fff7ed; border:1px solid #fdba74; color:#9a3412; border-radius:8px;
          padding:10px 12px; font-size:13px; margin-top:10px; font-weight:600; }
  .list { margin:8px 0 0; padding-left:18px; font-size:13px; color:#555; }
  .chips { margin-top:8px; }
  .chip { display:inline-block; background:#f0f0f0; border-radius:12px; padding:3px 10px;
          font-size:12px; margin:2px 4px 2px 0; color:#444; }
</style></head>
<body>
<div class="sdcw">
  <div class="head">
    <img src="{{BANNER}}" alt="Süddeutschland-Cup">
    <div class="headtext">
      <div class="kicker">Süddeutschland-Cup</div>
      <div class="title">PDF → CSV Konverter</div>
      <div class="sub">Erzeugt aus den Ergebnislisten eines Laufs die CSV für die Vereinswertung.</div>
    </div>
  </div>
  <div class="body">
    <div class="step">1.  Lauf auswählen</div>
    <select id="round"></select>

    <div class="step">2.  Ordner mit den PDF-Ergebnislisten</div>
    <div class="row">
      <button class="btn" onclick="pickFolder()">Ordner wählen…</button>
      <span id="folderLbl" class="muted">– noch nichts gewählt –</span>
    </div>
    <div><span id="formatBadge"></span></div>

    <div id="nennBox" class="hidden">
      <div class="step">3.  Nennliste (HTML) – für diesen Lauf erforderlich</div>
      <div class="muted" style="margin-bottom:6px">Dieser Lauf nutzt „Race-Control“ (keine Vereinsspalte).
        Bitte die von myrcm.ch als HTML gespeicherte Nennliste wählen.</div>
      <div class="row">
        <button class="btn sec" onclick="pickNenn()">Nennliste wählen…</button>
        <span id="nennLbl" class="muted">– keine –</span>
      </div>
    </div>

    <button id="go" class="btn gobtn" onclick="convert()">Konvertieren&nbsp; →&nbsp; CSV erstellen</button>

    <div id="result" class="res"></div>
  </div>
</div>

<script>
  const ROUNDS = [["01","Osterhofen"],["02","Sand"],["03","Landshut"],["04","Welden"],["05","Laupheim"],["06","Senden"]];
  const state = { folder:null, nennliste:null, needsNenn:false };

  const sel = document.getElementById('round');
  ROUNDS.forEach((r,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=r[0]+" – "+r[1]; sel.appendChild(o); });
  sel.value = ROUNDS.length-1;

  function esc(s){ return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

  async function pickFolder(){
    const r = await window.pywebview.api.pick_folder();
    if(!r || !r.folder) return;
    state.folder = r.folder;
    document.getElementById('folderLbl').textContent = r.name + "  ("+r.pdf_count+" PDF-Dateien)";
    state.needsNenn = r.race_control;
    const b = document.getElementById('formatBadge');
    const nb = document.getElementById('nennBox');
    if(r.race_control){
      b.className='badge rc'; b.textContent='Format: Race-Control – Nennliste erforderlich';
      nb.classList.remove('hidden');
    } else {
      b.className='badge ok'; b.textContent='Format: RCM – keine Nennliste nötig ✓';
      nb.classList.add('hidden');
    }
  }

  async function pickNenn(){
    const r = await window.pywebview.api.pick_nennliste();
    if(!r || !r.path) return;
    state.nennliste = r.path;
    document.getElementById('nennLbl').textContent = r.name;
  }

  async function convert(){
    if(!state.folder){ alert('Bitte zuerst den Ordner mit den PDF-Dateien wählen.'); return; }
    if(state.needsNenn && !state.nennliste){ alert('Dieser Lauf (Race-Control) benötigt die Nennliste. Bitte unter Schritt 3 auswählen.'); return; }
    const i = parseInt(sel.value,10); const r = ROUNDS[i];
    const go = document.getElementById('go');
    go.disabled = true; go.textContent='Konvertiere…';
    const res = await window.pywebview.api.convert(r[0], r[1], state.folder, state.nennliste, state.needsNenn);
    go.disabled = false; go.textContent='Konvertieren  →  CSV erstellen';
    render(res);
  }

  function render(res){
    const el = document.getElementById('result');
    if(!res.ok){
      el.innerHTML = '<div class="res-card res-err">Fehler: '+esc(res.error||'unbekannt')+'</div>';
      return;
    }
    let h = '<div class="res-card res-ok">';
    h += '<div class="res-h">✓ CSV erstellt: '+esc(res.csv_name)+'</div>';
    h += '<div class="res-total">'+res.total+' Fahrer</div>';
    h += '<div class="muted">'+res.club_count+' Vereine · Klassen: </div>';
    h += '<div class="chips">'+res.detected.map(c=>'<span class="chip">'+esc(c)+'</span>').join('')+'</div>';

    h += '<table class="st"><thead><tr><th>Datei</th><th>Klasse</th><th>Format</th><th class="n">gef.</th><th class="n">neu</th></tr></thead><tbody>';
    res.stats.forEach(s=>{ h+='<tr><td>'+esc(s.file)+'</td><td>'+esc(s.klasse)+'</td><td>'+esc(s.fmt)+'</td><td class="n">'+s.found+'</td><td class="n">'+s.new+'</td></tr>'; });
    h += '</tbody></table>';

    (res.warnings||[]).forEach(w=>{ h += '<div class="warn">⚠ '+esc(w)+'</div>'; });

    if(res.unmatched && res.unmatched.length){
      h += '<div class="warn">⚠ '+res.unmatched.length+' Fahrer NICHT in der Nennliste gefunden:</div>';
      h += '<ul class="list">'+res.unmatched.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
    }
    if(res.no_club && res.no_club.length){
      h += '<div class="warn">⚠ '+res.no_club.length+' Fahrer in Nennliste, aber OHNE Verein:</div>';
      h += '<ul class="list">'+res.no_club.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
    }

    h += '<div style="margin-top:14px"><button class="btn sec" onclick="openDir(\''+res.csv_dir.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')">Ordner öffnen</button></div>';
    h += '</div>';
    el.innerHTML = h;
  }

  async function openDir(d){ await window.pywebview.api.open_folder(d); }
</script>
</body></html>"""


def main():
    api = Api()
    html = HTML.replace("{{BANNER}}", BANNER)
    window = webview.create_window("SDC-Konverter", html=html, js_api=api,
                                   width=760, height=860, min_size=(680, 600))
    api.window = window
    webview.start()


if __name__ == "__main__":
    main()
