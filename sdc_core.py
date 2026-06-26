#!/usr/bin/env python3
"""
SDC PDF -> CSV Konverter v2
Unterstuetzt zwei Formate:
  - RCM (MyRCM): "DEU Nachname Vorname" pro Zeile, Verein in Folgezeile
  - Race Control: "Nachname, Vorname" pro Zeile, Verein aus Nennliste

Verwendung:
    python3 sdc_pdf_to_csv.py /Pfad/zum/Ordner
    python3 sdc_pdf_to_csv.py /Pfad/zum/Ordner --nennliste /Pfad/zur/Nennliste.html
    python3 sdc_pdf_to_csv.py /Pfad/zum/Ordner Ausgabe.csv --nennliste /Pfad/zur/Nennliste.html
"""

import sys
import csv
import re
from pathlib import Path
from html.parser import HTMLParser
import pdfplumber

# ─── Vereins-Normalisierung ──────────────────────────────────────────────────

CLUB_ALIASES = {
    'la speedway':                   'LA Speedway Racing Club e.V.',
    'la speedway racing club':       'LA Speedway Racing Club e.V.',
    'la speedway e.v.':              'LA Speedway Racing Club e.V.',
    'la speedway racing club e.v.':  'LA Speedway Racing Club e.V.',
    'mc welden':                     'MC Welden e.V. - Fuchstalring',
    'mc welden e. v.':               'MC Welden e.V. - Fuchstalring',
    'mc welden e.v.':                'MC Welden e.V. - Fuchstalring',
    'mc welden e.v. - fuchstalring': 'MC Welden e.V. - Fuchstalring',
    'mrc münchen':                   'MRC München e.V.',
    'mrc münchen e.v.':              'MRC München e.V.',
    'mrc muenchen':                  'MRC München e.V.',
    'mrc muenchen e.v.':             'MRC München e.V.',
    'msc sand':                      'MSC Sand 1951 e.V.',
    'msc sand e.v.':                 'MSC Sand 1951 e.V.',
    'msc sand e.v':                  'MSC Sand 1951 e.V.',
    'msc sand/ main e.v':            'MSC Sand 1951 e.V.',
    'msc sand/ main e.v.':           'MSC Sand 1951 e.V.',
    'msc sand 1951':                 'MSC Sand 1951 e.V.',
    'msc sand 1951 e.v.':            'MSC Sand 1951 e.V.',
    'msc osterhofen':                'MSC Osterhofen e.V.',
    'msc osterhofen e.v.':           'MSC Osterhofen e.V.',
    'mcc laupheim':                  'MCC Laupheim e.V.',
    'mcc laupheim e.v.':             'MCC Laupheim e.V.',
    'mrc senden':                    'MRC Senden e.V.',
    'mrc senden e.v.':               'MRC Senden e.V.',
}

CLUB_FUZZY = [
    (re.compile(r'\bla[\s\-]*speedway\b',    re.I), 'LA Speedway Racing Club e.V.'),
    (re.compile(r'\bmc[\s\-]*welden\b',      re.I), 'MC Welden e.V. - Fuchstalring'),
    (re.compile(r'\bmrc[\s\-]*m[üu]nchen\b', re.I), 'MRC München e.V.'),
    (re.compile(r'\bmsc[\s\-]*sand\b',       re.I), 'MSC Sand 1951 e.V.'),
    (re.compile(r'\bmsc[\s\-]*osterhofen\b', re.I), 'MSC Osterhofen e.V.'),
    (re.compile(r'\bmcc[\s\-]*laupheim\b',   re.I), 'MCC Laupheim e.V.'),
    (re.compile(r'\bmrc[\s\-]*senden\b',     re.I), 'MRC Senden e.V.'),
    # Vereinheitlichung der ueber die Events verstreuten Schreibweisen
    (re.compile(r'\bmcc[\s\-]*nufringen\b',           re.I), 'MCC Nufringen e.V.'),
    (re.compile(r'\bmcc[\s\-]*fellbach\b',            re.I), 'MCC Fellbach e.V.'),
    (re.compile(r'\brgmc[\s\-]*teck\b',               re.I), 'RGMC Teck e.V.'),
    (re.compile(r'\brg[\s\-]*kirchen[\s\-]*hausen\b', re.I), 'RG Kirchen-Hausen'),
    (re.compile(r'\borf?[\s\-]*hassfurt\b',           re.I), 'ORF Hassfurt e.V.'),
    (re.compile(r'\bmrc[\s\-]*weiden\b',              re.I), 'MRC Weiden e.V.'),
    (re.compile(r'\bamc[\s\-]*tuttlingen\b',          re.I), 'AMC Tuttlingen e.V.'),
    (re.compile(r'\bamc[\s\-]*kirchentellinsfurt\b',  re.I), 'AMC Kirchentellinsfurt e.V.'),
    (re.compile(r'\bmcc[\s\-]*neuffen\b',             re.I), 'MC 2000 Neuffen e.V.'),
    (re.compile(r'\brc[\s\-]*cars[\s\-]*k[öo]ngen\b', re.I), 'RC Cars Köngen e.V.'),
    (re.compile(r'\bnitrof\w*',                       re.I), 'Nitrofighter Lambrechten'),
    (re.compile(r'\bteam[\s\-]*der[\s\-]*rc[\s\-]*keller\b', re.I), 'Team Der RC-Keller'),
    (re.compile(r'\b(?:mbv|modellbau)[\s\-]*dettingen', re.I), 'Modellbau Dettingen-Erms e.V.'),
    # ESV (Blau-Gold) Bischofsheim -> ein Verein (lt. Vorgabe)
    (re.compile(r'\besv\b.*\bbischofsheim\b',         re.I), 'ESV Bischofsheim e.V.'),
    # EFAC Hohenems (auch "EFAC/RCCR" -> erster Verein EFAC)
    (re.compile(r'\befac[\s\-]*hohenems\b',           re.I), 'EFAC Hohenems'),
    (re.compile(r'\befac\b',                          re.I), 'EFAC Hohenems'),
]

# ─── Manuelle Club-Zuordnung (Vorrang vor der Nennliste) ─────────────────────
# Fuer Fahrer, deren Verein nicht (korrekt) aus der Nennliste hervorgeht:
#   - Nennliste-Verein leer (Waechter)
#   - Nachnennung, gar nicht in Nennliste (Dejaco, Engmann)
#   - Jugendfinale ohne eigene Nennliste (Mueller, Johnson)
# Schluessel: (Nachname, Vorname) jeweils klein, ß als ss (wie name_key()).
MANUAL_CLUBS = {
    ('wächter',  'michael'):   'LA Speedway Racing Club e.V.',
    ('dejaco',   'alexander'): 'MC Welden e.V. - Fuchstalring',
    ('müller',   'ben'):       'MSC Sand 1951 e.V.',
    # Noch offen – bitte Verein ergaenzen, sobald bekannt:
    # ('engmann',  'phillip'):   '',
    # ('johnson',  'kolsen'):    '',
}

# ─── Bewusst NICHT gewertete Fahrer ──────────────────────────────────────────
# Gaststarter, nicht regelkonforme Nachnennungen, reine Zuschauer-Kinder usw.
# Diese werden weder in die CSV geschrieben noch als "fehlend" gemeldet.
# Schluessel: (Nachname, Vorname) klein, ß als ss (wie name_key()).
EXCLUDE_DRIVERS = {
    ('engmann', 'phillip'),   # Nachnennung, nicht regelkonform – nicht werten
}

def normalize_club(name: str) -> str:
    name = name.strip()
    # Doppel-Mitgliedschaft "Verein A / Verein B": nur der erste Verein zaehlt.
    # Nur bei ' / ' mit Leerzeichen trennen, damit Ortsnamen wie
    # "Dettingen/Erms" nicht zerschnitten werden.
    name = re.split(r'\s+/\s+', name)[0].strip()
    # Aus der Nennliste koennen zwei Vereine durch grosse Luecke verkettet sein
    # ("Mbv Dettingen/Erms   Team der RC keller") -> nur den ersten behalten.
    name = re.split(r'\s{3,}', name)[0].strip()

    key  = name.lower()
    if key in CLUB_ALIASES:
        return CLUB_ALIASES[key]
    for pattern, canonical in CLUB_FUZZY:
        if pattern.search(name):
            return canonical
    # Generische Endungs-Vereinheitlichung (e. V. / e.V -> e.V.)
    name = re.sub(r'\be\.?\s*v\.?\b\.?$', 'e.V.', name, flags=re.I).strip()
    return re.sub(r'\s+', ' ', name)

def name_key(s: str) -> str:
    """Normalisiert einen Namensbestandteil fuer den Abgleich.
    Faltet ß->ss, vereinheitlicht Gross/Klein und entfernt Mehrfach-Leerzeichen."""
    s = s.strip().casefold().replace('ß', 'ss')
    return re.sub(r'\s+', ' ', s)

# ─── Klassen-Erkennung aus Dateiname ────────────────────────────────────────

CLASS_PATTERNS = [
    (re.compile(r'OR8.?Expert', re.I), 'OR8 Expert'),
    (re.compile(r'OR8.?Hobby',  re.I), 'OR8 Hobby'),
    (re.compile(r'\bORET\b',    re.I), 'ORET'),
    (re.compile(r'\bORE8?\b',   re.I), 'ORE'),
    (re.compile(r'\bORT\b',     re.I), 'ORT'),
    (re.compile(r'Jugend',      re.I), 'Jugendfinale'),
]

def detect_class(stem: str) -> str:
    for pattern, class_name in CLASS_PATTERNS:
        if pattern.search(stem):
            return class_name
    return 'Unbekannt'

# ─── PDF-Textextraktion ──────────────────────────────────────────────────────

def extract_plain(pdf_path) -> str:
    """Reiner Textauszug (fuer Format-Erkennung + Race-Control-Parser)."""
    out = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or '')
    return '\n'.join(out)

def extract_rcm_field_rows(pdf_path, col_gap: float = 9.0):
    """Rekonstruiert die Tabellenspalten ueber Wort-Koordinaten.
    Gibt je sichtbarer Zeile eine Liste von Spaltenfeldern zurueck –
    entspricht dem, was 'pdftotext -layout' + Split an 2+ Leerzeichen lieferte."""
    rows = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            lines = {}
            for w in words:
                key = round(w['top'] / 3)          # ~3px Zeilen-Toleranz
                lines.setdefault(key, []).append(w)
            for key in sorted(lines):
                ws = sorted(lines[key], key=lambda x: x['x0'])
                fields = []
                cur = [ws[0]['text']]
                lastx = ws[0]['x1']
                for w in ws[1:]:
                    if w['x0'] - lastx > col_gap:  # grosse Luecke = neue Spalte
                        fields.append(' '.join(cur))
                        cur = [w['text']]
                    else:
                        cur.append(w['text'])
                    lastx = w['x1']
                fields.append(' '.join(cur))
                rows.append(fields)
    return rows

# ─── Format-Erkennung ────────────────────────────────────────────────────────

def is_race_control(text: str) -> bool:
    return bool(re.search(r'Race.Control|RACE.CONTROL', text))

# ─── Nennliste aus HTML laden (myrcm.ch gespeicherte Seite) ──────────────

class _NennlisteParser(HTMLParser):
    """Extrahiert Tabellenzeilen aus der myrcm.ch Nennliste-HTML."""
    def __init__(self):
        super().__init__()
        self._in_row   = False
        self._in_cell  = False
        self._col      = 0
        self._cur_row  = []
        self._cur_text = []
        self.rows      = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == 'tr':
            row_id = attr_dict.get('id', '')
            if row_id.lstrip('-').isdigit() and int(row_id) > 0:
                self._in_row  = True
                self._col     = 0
                self._cur_row = []
        elif tag == 'td' and self._in_row:
            self._in_cell  = True
            self._cur_text = []

    def handle_endtag(self, tag):
        if tag == 'td' and self._in_cell:
            self._cur_row.append(''.join(self._cur_text).strip())
            self._in_cell = False
            self._col += 1
        elif tag == 'tr' and self._in_row:
            self.rows.append(self._cur_row)
            self._in_row = False

    def handle_data(self, data):
        if self._in_cell:
            self._cur_text.append(data)

    def handle_entityref(self, name):
        if self._in_cell and name == 'nbsp':
            self._cur_text.append('')


def load_nennliste_html(html_path: str) -> dict:
    """
    Liest eine als HTML gespeicherte myrcm.ch-Nennlistenseite.
    Tabellenstruktur: # | Key | Nachname | Vorname | Verein | Land | ...
    Gibt zurueck: (nachname_lower, vorname_lower) -> club
    """
    path = Path(html_path)
    if not path.exists():
        print(f'  Fehler: Nennliste nicht gefunden: {path}', file=sys.stderr)
        return None

    print(f'  Lese Nennliste: {path.name}')
    html = path.read_text(encoding='utf-8', errors='replace')

    parser = _NennlisteParser()
    parser.feed(html)

    lookup = {}
    for row in parser.rows:
        if len(row) < 5:
            continue
        # Spalten: 0=Nr, 1=Key, 2=Nachname, 3=Vorname, 4=Verein
        nachname = row[2].strip()
        vorname  = row[3].strip()
        verein   = row[4].strip()
        if nachname and vorname:
            club = normalize_club(verein) if verein else ''
            key  = (name_key(nachname), name_key(vorname))
            if key not in lookup:
                lookup[key] = club

    print(f'  {len(lookup)} Fahrer in Nennliste gefunden.')
    return lookup
# ─── RCM-Parser (Finalrangliste, pdftotext -layout) ──────────────────────────
# Mit -layout steht jeder Fahrer auf EINER Zeile, Spalten durch 2+ Leerzeichen
# getrennt:
#   Rang  [DMC#]  [NAT] Nachname Vorname    Verein    Qualy(..)  Zeiten ...
# Die Jugendfinale-Variante hat teils keinen NAT-Praefix und keine DMC#.

# Eine Qualy-/Zeit-/Ergebnis-Spalte (markiert das Ende des Vereins-Feldes)
RESULT_COL_RE = re.compile(r'^(?:\d+\s*\(\d*\)|DNS|DNF|DSQ|DQ|-\s*\(-\)|-)\b', re.I)

# Bekannte Laendercodes (IOC/3-Buchstaben). Nur diese werden als NAT-Praefix
# entfernt – sonst wuerden Vereinskuerzel wie "MCC" oder "RG" zerstoert.
NAT_CODES = {
    'DEU', 'GER', 'AUT', 'SUI', 'CHE', 'ITA', 'NED', 'NLD', 'CZE', 'CZ',
    'DEN', 'DNK', 'FRA', 'BEL', 'ESP', 'POL', 'GBR', 'USA', 'SWE', 'HUN',
    'SVK', 'SVN', 'CRO', 'LUX', 'LIE', 'NOR', 'FIN', 'POR', 'POT', 'IRL',
}

def _strip_nat(s: str) -> str:
    """Entfernt einen fuehrenden Laendercode-Token, falls vorhanden."""
    parts = s.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in NAT_CODES:
        return parts[1].strip()
    return s

def _split_name(name_field: str):
    """Zerlegt 'NAT Nachname Vorname...' bzw. 'Nachname Vorname...' in
    (Nachname, Vorname). Nachname = erstes Wort (ggf. mit Bindestrich),
    Vorname = Rest. Gibt (None, None) zurueck, wenn kein gueltiger Name."""
    s = _strip_nat(name_field.strip())
    parts = s.split()
    if len(parts) < 2:
        return None, None
    # Plausibilitaet: muss wie ein Name aussehen (Grossbuchstabe am Anfang)
    if not re.match(r'^[A-ZÄÖÜ]', parts[0]):
        return None, None
    return parts[0], ' '.join(parts[1:])

def parse_rcm_drivers(field_rows, klasse: str) -> list:
    results = []
    seen    = set()
    for fields in field_rows:
        if len(fields) < 3:
            continue
        # Feld 0 muss die Rang-Nummer sein
        if not fields[0].isdigit():
            continue
        # optionale DMC#-Nummer ueberspringen
        idx = 1
        while idx < len(fields) and fields[idx].isdigit():
            idx += 1
        if idx + 1 >= len(fields):
            continue
        name_field = fields[idx]
        club_field = _strip_nat(fields[idx + 1])   # NAT kann im Club-Feld leaken

        nachname, vorname = _split_name(name_field)
        if not nachname:
            continue
        # Vereinsfeld darf keine Ergebnis-/Zeitspalte sein
        if RESULT_COL_RE.match(club_field) or not re.search(r'[A-Za-zÄÖÜäöü]', club_field):
            continue

        nn, vn = name_key(nachname), name_key(vorname)
        if (nn, vn) in seen:
            continue
        if (nn, vn) in EXCLUDE_DRIVERS or (vn, nn) in EXCLUDE_DRIVERS:
            continue
        seen.add((nn, vn))

        # Manuelle Zuordnung hat Vorrang, sonst Verein aus dem PDF
        club = MANUAL_CLUBS.get((nn, vn)) or MANUAL_CLUBS.get((vn, nn)) \
               or normalize_club(club_field)
        results.append({'klasse': klasse, 'nachname': nachname,
                         'vorname': vorname, 'club': club})
    return results

# ─── Race-Control-Parser ──────────────────────────────────────────────────────

# Alterskategorien die nach dem Vornamen auftauchen koennen
AGE_CAT = r'(?:\s+(?:Jun|Jug|Senior|\d{2,3}\+))?'

# Linie mit Pl + (optional Reg#) + "Nachname, Vorname [Alterskategorie]"
# Reg# optional, da Gaststarter/Lokalfahrer teils ohne Startnummer gelistet sind.
RC_FULL_RE = re.compile(
    r'^\s*\d+\s+(?:\d+\s+)?'
    r'([A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)'
    r',\s+'
    r'([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)' + AGE_CAT
)
# Linie nur mit "Nachname, Vorname" (ohne Reg#, z.B. Eintrag ohne Lizenz)
RC_NAME_ONLY_RE = re.compile(
    r'^([A-ZÄÖÜ][a-zäöüß\-]+)'
    r',\s+'
    r'([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)' + AGE_CAT + r'\s*$'
)

def _lookup_club(nn: str, vn: str, lookup: dict):
    """Sucht einen Fahrer in der Nennliste. Probiert beide Namensreihenfolgen
    (manche Nenner haben Vor-/Nachname vertauscht eingetragen) sowie
    Teiltreffer beim Vornamen. Gibt den Verein zurueck (kann '' sein, wenn
    der Fahrer genannt ist, aber keinen Verein angegeben hat) oder None,
    wenn der Fahrer gar nicht in der Nennliste steht."""
    # 0. Manuelle Zuordnung hat immer Vorrang (auch beide Reihenfolgen)
    manual = MANUAL_CLUBS.get((nn, vn)) or MANUAL_CLUBS.get((vn, nn))
    if manual:
        return manual
    # 1. Exakter Treffer in normaler Reihenfolge
    if (nn, vn) in lookup:
        return lookup[(nn, vn)]
    # 2. Vertauschte Reihenfolge (Nenner-Tippfehler)
    if (vn, nn) in lookup:
        return lookup[(vn, nn)]
    # 3. Teiltreffer: Nachname exakt, Vorname Praefix (beide Reihenfolgen)
    for (n, v), c in lookup.items():
        if n == nn and (v.startswith(vn) or vn.startswith(v)):
            return c
        if v == nn and (n.startswith(vn) or vn.startswith(n)):  # vertauscht
            return c
    return None

def parse_race_control_drivers(text: str, klasse: str, lookup: dict) -> tuple:
    """Gibt (results, no_club, unmatched) zurueck.
      results   : Fahrer mit Verein aus der Nennliste
      no_club   : Fahrer in der Nennliste, aber ohne Vereinsangabe
      unmatched : Fahrer, die gar nicht in der Nennliste stehen
    """
    lines     = [l.strip() for l in text.splitlines()]
    found     = {}   # dedup innerhalb dieser Klasse
    no_club   = []
    unmatched = []

    for line in lines:
        # Zeilen mit Pl + Reg# + Name (Hauptfall)
        m = RC_FULL_RE.match(line) or RC_NAME_ONLY_RE.match(line)
        if not m:
            continue
        nachname = m.group(1).strip()
        vorname  = m.group(2).strip()
        # Alterskategorie(n) am Ende des Vornamens entfernen (Jun/Jug/Senior/40+ ...)
        vorname  = re.sub(r'(?:\s+(?:Jun|Jug|Senior|Veteran|\d{2,3}\+))+$', '', vorname, flags=re.I).strip()
        nn, vn   = name_key(nachname), name_key(vorname)
        key      = (nn, vn)

        if key in found:
            continue

        # Bewusst nicht gewertete Fahrer komplett ueberspringen
        if (nn, vn) in EXCLUDE_DRIVERS or (vn, nn) in EXCLUDE_DRIVERS:
            continue

        club = _lookup_club(nn, vn, lookup)

        if club:
            found[key] = {'klasse': klasse, 'nachname': nachname,
                           'vorname': vorname, 'club': club}
        elif club == '':
            # In Nennliste vorhanden, aber kein Verein hinterlegt
            found[key] = {'klasse': klasse, 'nachname': nachname,
                           'vorname': vorname, 'club': ''}
            no_club.append(f'{nachname}, {vorname}')
        else:
            unmatched.append(f'{nachname}, {vorname}')

    return list(found.values()), no_club, unmatched

# ─── Ordner verarbeiten ──────────────────────────────────────────────────────

def process_folder(folder: Path, lookup: dict):
    pdfs  = sorted(folder.glob('*.pdf')) + sorted(folder.glob('*.PDF'))
    if not pdfs:
        print(f'Fehler: Keine PDF-Dateien in {folder} gefunden.')
        sys.exit(1)

    seen       = {}   # (name_key nn, name_key vn) -> dict  (globale Dedup)
    stats      = []
    all_unmatched = []
    all_no_club   = []

    for pdf in pdfs:
        klasse = detect_class(pdf.stem)
        text   = extract_plain(pdf)   # fuer die Format-Erkennung + Race-Control

        if is_race_control(text):
            if lookup is None:
                print(f'  ! Race-Control-Format erkannt, aber keine Nennliste angegeben.')
                print(f'    Bitte --nennliste /Pfad/zur/Nennliste.html uebergeben.')
                stats.append((pdf.name, klasse, 0, 0, 'kein Lookup'))
                continue
            drivers, no_club, unmatched = parse_race_control_drivers(text, klasse, lookup)
            all_unmatched.extend(unmatched)
            all_no_club.extend(no_club)
            fmt = 'Race-Control'
        else:
            # RCM-Finalrangliste: Spalten aus Wort-Koordinaten rekonstruieren
            field_rows = extract_rcm_field_rows(pdf)
            drivers    = parse_rcm_drivers(field_rows, klasse)
            fmt        = 'RCM'

        new_count = 0
        for d in drivers:
            key = (name_key(d['nachname']), name_key(d['vorname']))
            if key not in seen:
                seen[key]  = d
                new_count += 1

        stats.append((pdf.name, klasse, len(drivers), new_count, fmt))

    return list(seen.values()), stats, all_no_club, all_unmatched

# ─── CSV schreiben ───────────────────────────────────────────────────────────

def write_csv(drivers: list, output_path: Path):
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['Klasse', 'Nachname', 'Vorname', 'Club'])
        for d in sorted(drivers, key=lambda x: (x['club'], x['nachname'], x['vorname'])):
            writer.writerow([d['klasse'], d['nachname'], d['vorname'], d['club']])

# ─── Argumente parsen ────────────────────────────────────────────────────────

def parse_args():
    args       = sys.argv[1:]
    folder     = None
    output     = None
    nennliste  = None

    i = 0
    while i < len(args):
        if args[i] == '--nennliste' and i + 1 < len(args):
            nennliste = args[i + 1]
            i += 2
        elif folder is None:
            folder = Path(args[i]).resolve()
            i += 1
        elif output is None:
            output = Path(args[i]).resolve()
            i += 1
        else:
            i += 1

    if folder is None:
        print(__doc__)
        sys.exit(1)

    if output is None:
        output = folder / f'{folder.name}_Vereinswertung.csv'

    return folder, output, nennliste

# ─── Einstiegspunkt ──────────────────────────────────────────────────────────

def main():
    folder, output_path, nennliste_source = parse_args()

    if not folder.is_dir():
        print(f'Fehler: "{folder}" ist kein gueltiger Ordner.')
        sys.exit(1)

    sep = '-' * 80
    print(f'\nSDC PDF -> CSV Konverter v2')
    print(sep)
    print(f'Ordner : {folder}')
    print(f'Ausgabe: {output_path}\n')

    # Nennliste laden (falls angegeben)
    lookup = None
    if nennliste_source:
        lookup = load_nennliste_html(nennliste_source)
        print()

    drivers, stats, no_club, unmatched = process_folder(folder, lookup)

    print()
    print(sep)
    print(f'{"Datei":<44} {"Klasse":<14} {"Format":<14} {"Gefunden":>9} {"Neu":>6}')
    print(sep)
    for filename, klasse, found, new, fmt in stats:
        print(f'{filename[:43]:<44} {klasse:<14} {fmt:<14} {found:>9} {new:>6}')
    print(sep)
    print(f'{"Eindeutige Fahrer gesamt":<44} {"":<14} {"":<14} {"":>9} {len(drivers):>6}')

    if no_club:
        print(f'\nIn Nennliste, aber OHNE Vereinsangabe ({len(set(no_club))} Fahrer):')
        print(f'  (in CSV mit leerem Club -> bitte Verein ergaenzen)')
        for name in sorted(set(no_club)):
            print(f'  - {name}')

    if unmatched:
        print(f'\nNICHT in Nennliste gefunden ({len(set(unmatched))} Fahrer):')
        print(f'  (Nennliste ist Master -> beim Veranstalter nachhaken,')
        print(f'   oder separate Liste noetig, z.B. Jugendfinale)')
        for name in sorted(set(unmatched)):
            print(f'  - {name}')

    if not drivers:
        print('\nKeine Fahrerdaten erkannt.')
        sys.exit(1)

    write_csv(drivers, output_path)
    print(f'\nCSV gespeichert: {output_path}\n')

if __name__ == '__main__':
    main()
