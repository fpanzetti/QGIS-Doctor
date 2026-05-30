"""
QGIS Doctor — Diagnostics Engine (v0.1 MVP).

Moduli implementati: CRS-01..05, LAY-01..08, PRJ-01..07, LOG-01..04.
Ogni check è isolato in try/except per non bloccare l'analisi in caso di errore.
"""

import os
import re
from dataclasses import dataclass, field

try:
    from qgis.core import (
        QgsProject, QgsSettings, QgsApplication, QgsMessageLog, Qgis,
        QgsMapLayer, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
        QgsWkbTypes,
    )
    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False


@dataclass
class DiagnosticIssue:
    id: str
    category: str
    issue_type: str          # "user_error" | "known_bug" | "config_warning" | "info"
    severity: str            # "ERROR" | "WARNING" | "INFO"
    title: str
    explanation: str
    suggestion: str
    technical_detail: str
    search_keywords: list = field(default_factory=list)
    known_issues: list = field(default_factory=list)
    layer_name: str = ""
    auto_fixable: bool = False


class DiagnosticsEngine:

    def __init__(self, iface, log_buffer: list):
        self.iface = iface
        self.log_buffer = log_buffer  # list of (tag, level, message)
        self.issues: list[DiagnosticIssue] = []

    # ── Public ───────────────────────────────────────────────────────────────

    def run_all(self, progress_callback=None) -> list[DiagnosticIssue]:
        """
        Esegue tutti i moduli diagnostici.
        progress_callback(module_name: str) — chiamato prima di ogni modulo.
        """
        self.issues = []
        if not _HAS_QGIS:
            return self.issues

        project = QgsProject.instance()
        if not project:
            return self.issues

        layers = list(project.mapLayers().values())

        modules = [
            ("CRS",         lambda: self._check_crs(project, layers)),
            ("Layers",      lambda: self._check_layers(layers)),
            ("Project",     lambda: self._check_project(project, layers)),
            ("Log",         lambda: self._check_log()),
            ("Joins",       lambda: self._check_joins(project, layers)),
            ("Plugins",     lambda: self._check_plugins(project)),
            ("Digitizing",  lambda: self._check_digitizing(project, layers)),
            ("Rendering",   lambda: self._check_rendering(project, layers)),
            ("Expressions", lambda: self._check_expressions(layers)),
            ("Layouts",     lambda: self._check_print_layouts(project)),
        ]

        for module_name, module_fn in modules:
            if progress_callback:
                try:
                    progress_callback(module_name)
                except Exception:
                    pass
            try:
                module_fn()
            except Exception:
                pass

        # Sort: ERROR first, then WARNING, then INFO
        order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
        self.issues.sort(key=lambda i: order.get(i.severity, 3))
        return self.issues

    def get_environment_summary(self) -> dict:
        """Raccoglie dati ambiente per il report e per i prompt LLM."""
        summary = {
            "qgis_version": "unknown",
            "os": "",
            "project_name": "",
            "project_crs": "",
            "n_layers": 0,
            "active_plugins": [],
            "snapping": "unknown",
            "otf_reprojection": "unknown",
        }
        if not _HAS_QGIS:
            return summary

        try:
            summary["qgis_version"] = Qgis.QGIS_VERSION
        except Exception:
            pass

        import platform
        summary["os"] = f"{platform.system()} {platform.release()}"

        project = QgsProject.instance()
        if project:
            summary["project_name"] = project.title() or project.baseName() or "Untitled"
            crs = project.crs()
            summary["project_crs"] = crs.authid() if crs.isValid() else "Invalid/None"
            summary["n_layers"] = len(project.mapLayers())

            try:
                snap_config = project.snappingConfig()
                summary["snapping"] = "on" if snap_config.enabled() else "off"
            except Exception:
                pass

        try:
            from qgis.utils import active_plugins
            summary["active_plugins"] = list(active_plugins)
        except Exception:
            pass

        return summary

    # ── MODULO 1 — CRS ───────────────────────────────────────────────────────

    def _check_crs(self, project, layers):
        project_crs = project.crs()

        for layer in layers:
            try:
                self._crs01_missing(layer)
            except Exception:
                pass
            try:
                self._crs02_mismatch(layer, project_crs, project)
            except Exception:
                pass
            try:
                self._crs05_out_of_bounds(layer)
            except Exception:
                pass
            try:
                self._crs06_displaced(layer)
            except Exception:
                pass

        try:
            self._crs03_many_crs(layers)
        except Exception:
            pass
        try:
            self._crs04_degrees_project(project_crs)
        except Exception:
            pass
        try:
            self._crs07_default_layer_crs(project_crs)
        except Exception:
            pass
        try:
            self._crs08_ntv2_not_available(layers, project_crs)
        except Exception:
            pass

    # ── CRS helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _guess_crs_from_extent(extent) -> tuple:
        """
        Analizza i valori numerici dell'extent e restituisce (authid, label, explanation).
        Copre i casi più comuni per l'Italia e per i dataset globali.
        """
        if extent.isNull() or extent.isEmpty():
            return None, None, None

        x = (extent.xMinimum() + extent.xMaximum()) / 2
        y = (extent.yMinimum() + extent.yMaximum()) / 2
        w = extent.width()
        h = extent.height()

        # ── Gradi decimali (sistema geografico) ──────────────────────────────
        # I valori sono piccoli numeri con decimali: x tra -180 e 180, y tra -90 e 90
        if -180 <= x <= 180 and -90 <= y <= 90 and w < 50 and h < 50:
            if 6 <= x <= 19 and 35 <= y <= 48:
                return (
                    "EPSG:4326",
                    "WGS 84 (gradi decimali — area Italia)",
                    "Le coordinate sono in gradi decimali (es. 14.73, 41.52). "
                    "Questo è il sistema geografico 'grezzo' — le stesse coordinate "
                    "che vedi su Google Maps. Non è un sistema proiettato in metri.",
                )
            return (
                "EPSG:4326",
                "WGS 84 (gradi decimali)",
                "Le coordinate sembrano essere in gradi decimali (longitudine/latitudine).",
            )

        # ── UTM zone 32N — WGS84 (EPSG:32632) ───────────────────────────────
        # Easting: 166000–834000 m, Northing: 3900000–9400000 m
        # Copre 6°E–12°E, include gran parte del nord/centro Italia
        if 100_000 <= x <= 900_000 and 3_800_000 <= y <= 9_500_000:
            if 400_000 <= x <= 780_000 and 4_400_000 <= y <= 5_200_000:
                return (
                    "EPSG:32632",
                    "WGS 84 / UTM zone 32N (coordinate in metri — Italia centro-nord)",
                    "Le coordinate sono in metri nel sistema UTM fuso 32N (6°E–12°E). "
                    "Questo è il sistema ufficiale italiano per la cartografia moderna.",
                )
            return (
                "EPSG:32632",
                "WGS 84 / UTM zone 32N (coordinate in metri)",
                "Le coordinate sembrano essere in metri nel sistema UTM zona 32N.",
            )

        # ── UTM zone 33N — WGS84 (EPSG:32633) ───────────────────────────────
        # Copre 12°E–18°E: Puglia, Calabria, Sicilia, parte dell'Adriatico
        if 100_000 <= x <= 900_000 and 3_800_000 <= y <= 9_500_000:
            return (
                "EPSG:32633",
                "WGS 84 / UTM zone 33N (coordinate in metri — Italia sud-est)",
                "Le coordinate sembrano essere in metri nel sistema UTM zona 33N "
                "(12°E–18°E), che copre il sud e l'adriatico.",
            )

        # ── Gauss-Boaga Fuso Ovest (EPSG:3003) ──────────────────────────────
        # Usato in Italia fino agli anni 2000. Easting ~1,200,000–1,800,000
        if 1_100_000 <= x <= 1_900_000 and 4_000_000 <= y <= 5_300_000:
            return (
                "EPSG:3003",
                "Monte Mario / Italy zone 1 — Gauss-Boaga Fuso Ovest",
                "Le coordinate sembrano Gauss-Boaga Fuso Ovest, il vecchio sistema "
                "cartografico italiano (usato su carte IGM fino agli anni 2000). "
                "L'easting è tipicamente intorno a 1,400,000–1,600,000.",
            )

        # ── Gauss-Boaga Fuso Est (EPSG:3004) ────────────────────────────────
        if 2_200_000 <= x <= 2_900_000 and 4_000_000 <= y <= 5_300_000:
            return (
                "EPSG:3004",
                "Monte Mario / Italy zone 2 — Gauss-Boaga Fuso Est",
                "Le coordinate sembrano Gauss-Boaga Fuso Est (Italia orientale e meridionale). "
                "L'easting è tipicamente intorno a 2,500,000–2,700,000.",
            )

        # ── Web Mercator (EPSG:3857) — usato da Google, OSM ─────────────────
        # Easting: -20000000–20000000, Northing: -20000000–20000000
        if -20_100_000 <= x <= 20_100_000 and -20_100_000 <= y <= 20_100_000:
            if abs(x) > 1_000_000 or abs(y) > 1_000_000:
                return (
                    "EPSG:3857",
                    "WGS 84 / Pseudo-Mercator (Web Mercator — usato da Google/OpenStreetMap)",
                    "Le coordinate sembrano essere in Web Mercator, il sistema usato da "
                    "Google Maps, OpenStreetMap e servizi web. I valori sono in metri "
                    "ma 'distorti' verso i poli.",
                )

        return None, None, None

    def _crs01_missing(self, layer):
        crs = layer.crs()
        if not crs.isValid() or crs.authid() == "":
            # Analizza le coordinate per suggerire il SR corretto
            extent = layer.extent()
            guessed_authid, guessed_label, guessed_expl = \
                self._guess_crs_from_extent(extent)

            coord_info = (
                f"  Extent del layer: xMin={extent.xMinimum():.4f}, "
                f"xMax={extent.xMaximum():.4f}, "
                f"yMin={extent.yMinimum():.4f}, "
                f"yMax={extent.yMaximum():.4f}"
                if not extent.isNull() else "  Extent non disponibile."
            )

            if guessed_authid:
                suggestion = (
                    f"STEP 1 — Assegna il SR corretto (NON riproiettare):\n"
                    f"  Tasto destro sul layer → Proprietà → Sorgente → icona SR.\n"
                    f"  Cerca e seleziona: {guessed_authid} ({guessed_label}).\n"
                    f"  Clicca OK. Il layer dovrebbe tornare al posto giusto.\n\n"
                    f"STEP 2 — Solo se necessario, riproietta al SR del progetto:\n"
                    f"  Processing → Toolbox → 'Riproietta layer' → SR destinazione:\n"
                    f"  {QgsProject.instance().crs().authid() if _HAS_QGIS else 'SR progetto'}.\n"
                    f"  Salva il risultato come nuovo file.\n\n"
                    f"⚠ La differenza è cruciale:\n"
                    f"  • 'Assegna SR' = dici a QGIS in quale sistema sono già i numeri\n"
                    f"    (i numeri non cambiano, solo l'interpretazione)\n"
                    f"  • 'Riproietta' = QGIS converte i numeri in un altro sistema\n"
                    f"    (i numeri cambiano, la geometria resta al posto suo)"
                )
                explanation = (
                    f"Il layer \"{layer.name()}\" non ha un sistema di riferimento (SR/CRS). "
                    f"Analizzando i valori delle coordinate, il SR più probabile è:\n"
                    f"  → {guessed_label}\n\n"
                    f"{guessed_expl}\n\n"
                    f"⚠ Attenzione all'errore più comune: se assegni un SR sbagliato\n"
                    f"(es. UTM a un layer in gradi), il layer sembrerà spostarsi di\n"
                    f"migliaia di km. In quel caso, riassegna il SR corretto sopra."
                )
            else:
                suggestion = (
                    "STEP 1 — Controlla i valori delle coordinate:\n"
                    "  Tasto destro → Proprietà → Informazioni → guarda 'Extent'.\n"
                    "  Se vedi numeri piccoli (es. 14.73, 41.52) → il SR è probabilmente\n"
                    "  EPSG:4326 (gradi decimali WGS84).\n"
                    "  Se vedi numeri grandi (es. 533000, 4600000) → il SR è probabilmente\n"
                    "  EPSG:32632 (UTM 32N, metri).\n\n"
                    "STEP 2 — Assegna il SR corretto (NON 'Riproietta'):\n"
                    "  Tasto destro → Proprietà → Sorgente → icona SR → cerca il codice.\n\n"
                    "⚠ 'Assegna SR' e 'Riproietta' sono operazioni diverse:\n"
                    "  • Assegna = dici a QGIS come leggere i numeri già presenti\n"
                    "  • Riproietta = QGIS converte i numeri in un altro sistema"
                )
                explanation = (
                    f"Il layer \"{layer.name()}\" non ha un sistema di riferimento (SR/CRS). "
                    "QGIS non sa dove posizionarlo sulla mappa.\n\n"
                    f"{coord_info}"
                )

            self.issues.append(DiagnosticIssue(
                id="CRS-01",
                category="CRS",
                issue_type="user_error",
                severity="ERROR",
                title="Layer senza sistema di riferimento (SR/CRS)",
                explanation=explanation,
                suggestion=suggestion,
                technical_detail=(
                    f"layer.crs().isValid()=False, authid='{layer.crs().authid()}'\n"
                    f"{coord_info}"
                    + (f"\nSR stimato: {guessed_authid}" if guessed_authid else "")
                ),
                layer_name=layer.name(),
            ))

    def _crs06_displaced(self, layer):
        """
        Rileva il caso: SR assegnato, ma le coordinate del layer non corrispondono
        all'area geografica valida per quel SR.

        Approccio in due fasi:

        FASE 1 — Validazione geografica (metodo affidabile):
          Riproietta il centroide del layer dal SR dichiarato a coordinate geografiche
          e verifica che cada dentro l'area valida di quel SR.
          Questo gestisce correttamente zone UTM adiacenti (32N vs 33N),
          Gauss-Boaga, Web Mercator, ecc.

        FASE 2 — Sniffing delle coordinate (fallback per casi eclatanti):
          Usato solo quando la fase 1 non è disponibile (no QGIS/PyQGIS).
          Rileva casi grossolani: gradi vs metri, datum completamente diversi.
          NON distingue zone UTM adiacenti — sarebbe un falso positivo garantito.
        """
        crs = layer.crs()
        if not crs.isValid():
            return  # gestito da CRS-01

        extent = layer.extent()
        if extent.isNull() or extent.isEmpty():
            return

        x_center = (extent.xMinimum() + extent.xMaximum()) / 2
        y_center = (extent.yMinimum() + extent.yMaximum()) / 2

        # ── FASE 1: validazione tramite riproiezione geografica ───────────────
        if _HAS_QGIS:
            displaced, geo_x, geo_y = self._check_displaced_via_reprojection(
                layer, crs, x_center, y_center
            )
            if displaced is False:
                return  # SR coerente con le coordinate → nessun problema
            if displaced is True:
                # Costruisci il messaggio con le info geografiche reali
                self._emit_crs06_geographic(layer, crs, geo_x, geo_y)
                return
            # displaced is None → riproiezione non riuscita, prova Fase 2

        # ── FASE 2: sniffing coordinate (solo distinzioni grossolane) ─────────
        guessed_authid, guessed_label, guessed_expl = \
            self._guess_crs_from_extent(extent)

        if guessed_authid is None:
            return
        if guessed_authid == crs.authid():
            return

        # Distingue solo casi chiari: gradi↔metri o datum completamente diversi
        # NON flaggare mai zone UTM adiacenti (32N vs 33N, 33N vs 34N ecc.)
        # perché hanno coordinate numericamente identiche
        is_degree_crs = crs.isGeographic()
        guessed_is_geographic = (guessed_authid == "EPSG:4326")
        is_webmercator_mismatch = (guessed_authid == "EPSG:3857" and not is_degree_crs)

        same_datum = (
            crs.geographicCrsAuthId() ==
            QgsCoordinateReferenceSystem(guessed_authid).geographicCrsAuthId()
        )

        # Salta se stesso datum + entrambi proiettati (probabile zona UTM adiacente)
        if same_datum and not is_degree_crs and not guessed_is_geographic:
            return

        if is_degree_crs and not guessed_is_geographic:
            scenario = (
                f"Il layer dichiara un SR in gradi ({crs.authid()}) "
                f"ma le sue coordinate sembrano essere in metri "
                f"(tipici di {guessed_label}). "
                "QGIS posizionerà il layer molto lontano dalla posizione reale."
            )
        elif not is_degree_crs and guessed_is_geographic:
            scenario = (
                f"Il layer dichiara un SR in metri ({crs.authid()}) "
                f"ma le sue coordinate sembrano essere in gradi decimali "
                f"(tipici di EPSG:4326 / WGS84). "
                "Questo è l'errore più comune: il layer risulterà spostato "
                "di migliaia di km rispetto alla sua posizione reale."
            )
        else:
            scenario = (
                f"Il layer dichiara {crs.authid()} ma le coordinate suggeriscono "
                f"{guessed_label} ({guessed_authid}). "
                "Il layer potrebbe non essere nella posizione corretta."
            )

        project_crs_id = (
            QgsProject.instance().crs().authid() if _HAS_QGIS else "SR del progetto"
        )
        fix_steps = (
            f"STEP 1 — Riassegna il SR corretto:\n"
            f"  Tasto destro → Proprietà → Sorgente → icona SR.\n"
            f"  Seleziona: {guessed_authid} ({guessed_label}).\n"
            f"  ⚠ Usa 'Assegna SR', NON 'Riproietta layer'.\n\n"
            f"  Differenza fondamentale:\n"
            f"  • Assegna SR = correggi l'etichetta sbagliata sui numeri\n"
            f"    (i numeri rimangono identici, QGIS li interpreta diversamente)\n"
            f"  • Riproietta = trasforma i numeri da un sistema all'altro\n"
            f"    (da usare DOPO aver assegnato il SR giusto)\n\n"
            f"STEP 2 — Verifica:\n"
            f"  Dopo l'assegnazione il layer dovrebbe tornare al posto corretto.\n"
            f"  QGIS riproietterà automaticamente 'al volo' verso {project_crs_id}."
        )

        self.issues.append(DiagnosticIssue(
            id="CRS-06",
            category="CRS",
            issue_type="user_error",
            severity="ERROR",
            title="SR assegnato non corrisponde alle coordinate — layer probabilmente spostato",
            explanation=scenario,
            suggestion=fix_steps,
            technical_detail=(
                f"SR dichiarato: {crs.authid()}, SR stimato: {guessed_authid}\n"
                f"Extent: x={extent.xMinimum():.2f}..{extent.xMaximum():.2f}, "
                f"y={extent.yMinimum():.2f}..{extent.yMaximum():.2f}"
            ),
            layer_name=layer.name(),
        ))

    def _check_displaced_via_reprojection(self, layer, crs, x_center, y_center):
        """
        Riproietta il centroide del layer dal SR dichiarato a coordinate geografiche
        e verifica che cadano dentro l'area valida del SR.

        Returns:
          (False, lon, lat)  → coordinate valide, SR coerente, nessun problema
          (True,  lon, lat)  → coordinate fuori dai bounds → layer probabilmente spostato
          (None,  None, None) → riproiezione fallita, usa fallback
        """
        try:
            from qgis.core import (
                QgsCoordinateTransform, QgsPointXY,
                QgsCoordinateReferenceSystem as QgsCRS,
            )
            geographic_crs = QgsCRS(crs.geographicCrsAuthId())
            if not geographic_crs.isValid():
                return None, None, None

            transform = QgsCoordinateTransform(
                crs, geographic_crs, QgsProject.instance()
            )
            geo_pt = transform.transform(QgsPointXY(x_center, y_center))
            lon, lat = geo_pt.x(), geo_pt.y()

            # Controlla se lon/lat è NaN o infinito (trasformazione fallita)
            import math
            if math.isnan(lon) or math.isnan(lat) or math.isinf(lon) or math.isinf(lat):
                return None, None, None

            # ── Controllo speciale per zone UTM WGS84 (EPSG:326xx) ─────────────
            # Le zone UTM adiacenti hanno coordinate numericamente identiche.
            # Verifichiamo che la longitudine riproiettata cada nella zona giusta
            # prima di affidarci ai bounds generici, che potrebbero essere troppo
            # stretti o restituire null per alcune zone UTM.
            authid = crs.authid()
            if authid.startswith("EPSG:326") and len(authid) == 10:
                try:
                    utm_epsg = int(authid.split(":")[1])
                    if 32601 <= utm_epsg <= 32660:
                        utm_zone = utm_epsg - 32600
                        # Longitudine centrale e range della zona UTM dichiarata
                        central_lon = (utm_zone - 1) * 6 - 177  # es. zona 32 → 9°E
                        zone_west = central_lon - 3
                        zone_east = central_lon + 3
                        # Margine extra: 2 gradi per casi limite (dati oltre il confine di zona)
                        tolerance = 2.0
                        if zone_west - tolerance <= lon <= zone_east + tolerance:
                            return False, lon, lat  # zona UTM coerente → nessun problema
                        # Lon fuori dalla zona: potrebbe essere zona sbagliata
                        # Ma solo se lo scarto è > 1 zona intera (6°) per sicurezza
                        declared_zone_dist = min(
                            abs(lon - zone_west), abs(lon - zone_east)
                        )
                        if declared_zone_dist < 8.0:
                            # Entro 8° dal bordo: zona adiacente, troppo ambiguo
                            return False, lon, lat
                        # Oltre 8°: lo spostamento è reale
                except (ValueError, IndexError):
                    pass

            # Confronta con i bounds geografici del SR dichiarato
            bounds = crs.bounds()  # in gradi geografici
            if bounds.isNull():
                return None, None, None

            # Margine di tolleranza: 5 gradi (evita falsi positivi per layer
            # vicini al bordo di una zona, es. Termoli vicino a 12°E)
            margin = 5.0
            in_bounds = (
                bounds.xMinimum() - margin <= lon <= bounds.xMaximum() + margin and
                bounds.yMinimum() - margin <= lat <= bounds.yMaximum() + margin
            )
            return (not in_bounds), lon, lat

        except Exception:
            return None, None, None

    def _emit_crs06_geographic(self, layer, crs, lon, lat):
        """Emette CRS-06 con informazioni geografiche reali (lon/lat del centroide)."""
        bounds = crs.bounds()
        bounds_str = (
            f"{bounds.xMinimum():.1f}°–{bounds.xMaximum():.1f}°E, "
            f"{bounds.yMinimum():.1f}°–{bounds.yMaximum():.1f}°N"
            if not bounds.isNull() else "bounds non disponibili"
        )
        project_crs_id = (
            QgsProject.instance().crs().authid() if _HAS_QGIS else "SR del progetto"
        )
        self.issues.append(DiagnosticIssue(
            id="CRS-06",
            category="CRS",
            issue_type="user_error",
            severity="ERROR",
            title="SR assegnato non corrisponde alla posizione geografica del layer",
            explanation=(
                f"Il layer \"{layer.name()}\" dichiara {crs.authid()}, "
                f"ma riproiettando le sue coordinate risulta posizionato a:\n"
                f"  Longitudine: {lon:.4f}°,  Latitudine: {lat:.4f}°\n\n"
                f"Questa posizione è fuori dall'area valida di {crs.authid()}:\n"
                f"  Area valida: {bounds_str}\n\n"
                "Il SR assegnato è quasi certamente sbagliato. "
                "Il layer risulterà spostato sulla mappa."
            ),
            suggestion=(
                "STEP 1 — Identifica il SR corretto:\n"
                f"  Le coordinate geografiche ({lon:.2f}°, {lat:.2f}°) suggeriscono\n"
                "  in quale area si trova il layer. Cerca il SR adatto a quell'area.\n"
                "  Suggerimento: controlla il nome del file o la sua provenienza.\n\n"
                "STEP 2 — Assegna il SR corretto (NON Riproietta):\n"
                "  Tasto destro → Proprietà → Sorgente → icona SR → cerca il codice.\n\n"
                "  Differenza fondamentale:\n"
                "  • Assegna SR = ridai i numeri all'interpretazione corretta\n"
                "  • Riproietta = trasforma i numeri (usa solo dopo aver assegnato)\n\n"
                f"STEP 3 — Verifica:\n"
                "  Dopo l'assegnazione il layer torna al posto corretto.\n"
                f"  QGIS riproietta automaticamente verso {project_crs_id}."
            ),
            technical_detail=(
                f"SR dichiarato: {crs.authid()}\n"
                f"Posizione geografica calcolata: lon={lon:.4f}°, lat={lat:.4f}°\n"
                f"Area valida {crs.authid()}: {bounds_str}"
            ),
            layer_name=layer.name(),
        ))

    # ── Datum helpers ─────────────────────────────────────────────────────────

    # Datum geografici Monte Mario (usato da Gauss-Boaga EPSG:3003/3004)
    _MONTE_MARIO_DATUM_IDS = {"EPSG:4265", "EPSG:4806"}
    # Datum WGS84 e suoi alias (ETRS89 è praticamente coincidente con WGS84)
    _WGS84_FAMILY = {"EPSG:4326", "EPSG:4258"}  # 4258 = ETRS89

    @classmethod
    def _is_monte_mario(cls, crs) -> bool:
        return crs.geographicCrsAuthId() in cls._MONTE_MARIO_DATUM_IDS

    @classmethod
    def _is_wgs84_family(cls, crs) -> bool:
        return crs.geographicCrsAuthId() in cls._WGS84_FAMILY

    # Nomi dei file griglia NTv2 per Monte Mario che PROJ 9+ conosce
    _ITALY_NTV2_GRIDS = {
        "35160622_47161840_R40_F00.gsb",  # Monte Mario → RDN2008, 0.1 m
        "35160622_47161840_R40_F89.gsb",  # Monte Mario → IGM95, 0.1 m
        "35160622_47161840_R40_E50.gsb",  # Monte Mario → ED50, 0.1 m
        "it_igmi_rer_etrs89.tif",         # Emilia-Romagna regionale
    }

    @classmethod
    def _ntv2_grid_available(cls) -> bool:
        """
        Controlla se almeno una griglia NTv2 per l'Italia è installata in PROJ.
        I file possono essere nel data dir di PROJ (dentro QGIS.app) o in ~./proj.
        """
        search_dirs = []
        try:
            # Data dir dentro QGIS.app
            search_dirs.append(
                "/Applications/QGIS.app/Contents/Resources/qgis/proj"
            )
        except Exception:
            pass
        try:
            import pyproj
            d = pyproj.datadir.get_data_dir()
            if d:
                search_dirs.append(d)
        except Exception:
            pass
        # User PROJ dir
        import os
        user_proj = os.path.expanduser("~/.local/share/proj")
        if os.path.isdir(user_proj):
            search_dirs.append(user_proj)

        for d in search_dirs:
            try:
                for fname in os.listdir(d):
                    if fname in cls._ITALY_NTV2_GRIDS:
                        return True
                    # Anche varianti it_igmi_*.tif o it_igm_*.gsb
                    if (fname.startswith("it_igm") and
                            (fname.endswith(".tif") or fname.endswith(".gsb"))):
                        return True
            except Exception:
                pass
        return False

    @staticmethod
    def _best_helmert_accuracy() -> int:
        """
        Restituisce l'accuratezza in metri della migliore trasformazione Helmert
        Monte Mario → WGS84 disponibile nel DB PROJ locale.
        Default 4 m (valore noto dal DB PROJ 9.x).
        """
        try:
            import sqlite3
            import os
            db_path = "/Applications/QGIS.app/Contents/Resources/qgis/proj/proj.db"
            if not os.path.exists(db_path):
                return 4
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT MIN(accuracy)
                FROM helmert_transformation_table
                WHERE (source_crs_auth_name||source_crs_code = 'EPSG4265'
                   OR target_crs_auth_name||target_crs_code = 'EPSG4265')
                AND accuracy IS NOT NULL
            """)
            row = cur.fetchone()
            conn.close()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:
            pass
        return 4

    def _crs02_mismatch(self, layer, project_crs, project):
        if not project_crs.isValid():
            return
        layer_crs = layer.crs()
        if not layer_crs.isValid():
            return
        if layer_crs.authid() == project_crs.authid():
            return

        # Avvisa solo se i datum sono diversi (problema reale di accuratezza)
        if layer_crs.geographicCrsAuthId() == project_crs.geographicCrsAuthId():
            return

        # Caso specifico: Monte Mario ↔ WGS84 (comune in Italia)
        is_mm_to_wgs = (
            self._is_monte_mario(layer_crs) and self._is_wgs84_family(project_crs)
        ) or (
            self._is_wgs84_family(layer_crs) and self._is_monte_mario(project_crs)
        )

        if is_mm_to_wgs:
            ntv2_ok = self._ntv2_grid_available()
            accuracy_note = (
                "✅ Griglia NTv2 italiana (IGM) installata → precisione ~5–20 cm."
                if ntv2_ok else
                "⚠ Griglia NTv2 italiana NON installata → QGIS usa Helmert 7-param "
                "(precisione ~1–3 metri). Per lavoro tecnico/ingegneristico questo "
                "errore è significativo."
            )
            ntv2_fix = (
                "" if ntv2_ok else
                "\n\nPer installare la griglia NTv2 italiana (precisione cm):\n"
                "  Impostazioni → Opzioni → SR e trasformazioni → Trasformazioni datum\n"
                "  → pulsante 'Scarica' → cerca 'Italy' o 'IGM' → installa.\n"
                "  Dopo l'installazione QGIS la usa automaticamente."
            )
            self.issues.append(DiagnosticIssue(
                id="CRS-02",
                category="CRS",
                issue_type="user_error",
                severity="WARNING",
                title=f"Datum diversi: Monte Mario ({layer_crs.authid()}) vs WGS84 ({project_crs.authid()})",
                explanation=(
                    f"Il layer \"{layer.name()}\" usa il datum Monte Mario "
                    f"({layer_crs.authid()}), il progetto usa WGS84 "
                    f"({project_crs.authid()}).\n\n"
                    "Monte Mario è il vecchio sistema geodetico italiano (IGM, carte storiche, "
                    "CTR regionali, Gauss-Boaga). WGS84 è il sistema GPS globale. "
                    "Sono fisicamente diversi: in Italia lo scarto è tipicamente "
                    "60–120 metri in direzione nord-est.\n\n"
                    f"{accuracy_note}\n\n"
                    "QGIS sovrappone i layer visivamente, ma l'errore esiste nei dati."
                ),
                suggestion=(
                    "Opzione A — Riproietta il layer (consigliata per uniformare il progetto):\n"
                    "  Processing → Riproietta layer\n"
                    f"  SR destinazione: {project_crs.authid()}\n"
                    "  Salva come nuovo file (non sovrascrivere l'originale).\n"
                    "  Con NTv2 installata la precisione è ~10–20 cm.\n"
                    "  Con solo Helmert la precisione è ~1–3 m.\n\n"
                    "Opzione B — Tieni il layer com'è e documenta lo scarto:\n"
                    "  Accettabile per sovrapposizioni di sfondo o dati storici.\n"
                    "  Non accettabile per analisi metriche precise."
                    + ntv2_fix
                ),
                technical_detail=(
                    f"layer datum: {layer_crs.geographicCrsAuthId()}, "
                    f"project datum: {project_crs.geographicCrsAuthId()}, "
                    f"NTv2 disponibile: {ntv2_ok}"
                ),
                layer_name=layer.name(),
            ))
        else:
            # Datum mismatch generico
            self.issues.append(DiagnosticIssue(
                id="CRS-02",
                category="CRS",
                issue_type="user_error",
                severity="WARNING",
                title="Layer e progetto usano datum diversi",
                explanation=(
                    f"Il layer \"{layer.name()}\" usa {layer_crs.authid()} mentre il progetto "
                    f"usa {project_crs.authid()}. Usano datum geografici diversi "
                    f"({layer_crs.geographicCrsAuthId()} vs {project_crs.geographicCrsAuthId()}), "
                    "il che può causare errori di posizionamento anche con la riproiezione al volo."
                ),
                suggestion=(
                    "Riproietta il layer nel SR del progetto:\n"
                    "  Processing → Riproietta layer\n"
                    f"  SR destinazione: {project_crs.authid()}\n"
                    "  Salva come nuovo file."
                ),
                technical_detail=(
                    f"layer CRS: {layer_crs.authid()}, project CRS: {project_crs.authid()}, "
                    f"layer datum: {layer_crs.geographicCrsAuthId()}, "
                    f"project datum: {project_crs.geographicCrsAuthId()}"
                ),
                layer_name=layer.name(),
            ))

    def _crs03_many_crs(self, layers):
        crs_set = {}  # authid → lista nomi layer
        for layer in layers:
            crs = layer.crs()
            if crs.isValid():
                crs_set.setdefault(crs.authid(), []).append(layer.name())

        if len(crs_set) <= 1:
            return

        # Controlla se ci sono mix di datum (più grave)
        datums = set()
        for authid in crs_set:
            crs = QgsCoordinateReferenceSystem(authid)
            datums.add(crs.geographicCrsAuthId())

        has_datum_mix = len(datums) > 1
        severity = "WARNING" if has_datum_mix else "INFO"
        issue_type = "user_error" if has_datum_mix else "config_warning"

        # Costruisci lista SR per la spiegazione
        crs_lines = "\n".join(
            f"  • {authid}: {', '.join(names[:3])}"
            + (" …" if len(names) > 3 else "")
            for authid, names in sorted(crs_set.items())
        )

        if has_datum_mix:
            datum_warning = (
                "\n⚠ Attenzione: alcuni di questi SR usano datum diversi "
                "(es. Monte Mario vs WGS84). La riproiezione al volo di QGIS "
                "introduce uno scarto sistematico di 60–120 m. "
                "Questo è un problema reale nei dati, non solo visivo."
            )
        else:
            datum_warning = (
                "\n✓ Tutti i SR usano lo stesso datum — nessun errore sistematico "
                "nella riproiezione al volo. Il problema è solo di ordine e prestazioni."
            )

        project_crs_id = QgsProject.instance().crs().authid() if _HAS_QGIS else "?"

        self.issues.append(DiagnosticIssue(
            id="CRS-03",
            category="CRS",
            issue_type=issue_type,
            severity=severity,
            title=f"SR multipli nel progetto — {'datum diversi (errore sistematico)' if has_datum_mix else 'stesso datum, SR diversi'}",
            explanation=(
                f"Il progetto usa {len(crs_set)} sistemi di riferimento diversi:\n"
                f"{crs_lines}\n"
                f"{datum_warning}\n\n"
                "Avere SR multipli non impedisce di lavorare, ma:\n"
                "  • Rallenta il rendering (ogni layer viene riproiettato al volo)\n"
                "  • Complica le analisi spaziali\n"
                "  • Aumenta il rischio di errori se un SR è sbagliato\n"
                "  • Rende il progetto più difficile da mantenere e condividere"
            ),
            suggestion=(
                f"Uniforma tutti i layer al SR del progetto ({project_crs_id}).\n\n"
                "Come farlo in modo sicuro:\n"
                "  1. Identifica i layer da riproiettare (quelli nell'elenco sopra\n"
                f"     che non sono già {project_crs_id})\n"
                "  2. Per ogni layer: Processing → Riproietta layer\n"
                f"     SR destinazione: {project_crs_id}\n"
                "     Salva come nuovo file (es. aggiungi '_32632' al nome)\n"
                "  3. Sostituisci il layer originale con quello riproiettato\n"
                "  4. Verifica visivamente che i layer si sovrappongano correttamente\n\n"
                "Eccezioni — layer che puoi lasciare nel loro SR originale:\n"
                "  • Layer WMS/WFS/XYZ remoti (non controlli il file)\n"
                "  • Dati storici che vuoi confrontare con lo scarto documentato\n\n"
                "Dopo l'uniformazione: tutti gli strumenti di analisi operano nello\n"
                "stesso spazio, i calcoli sono più veloci e non ci sono sorprese."
            ),
            technical_detail=f"SR distinti: {len(crs_set)}, datum distinti: {len(datums)}\n"
                              f"SR presenti: {', '.join(sorted(crs_set.keys()))}",
        ))

    def _crs07_default_layer_crs(self, project_crs):
        """
        CRS-07: il SR predefinito per i nuovi layer in QGIS non corrisponde al SR del progetto.
        Causa: ogni memory layer / scratch layer creato inizia con il SR sbagliato.
        """
        if not _HAS_QGIS or not project_crs.isValid():
            return

        s = QgsSettings()
        default_behavior = s.value("/projections/defaultBehaviour", "prompt")
        default_crs_authid = s.value("/projections/layerDefaultCrs", "EPSG:4326")

        # Se QGIS è impostato per chiedere ogni volta → nessun problema automatico
        if default_behavior == "prompt":
            return

        if default_crs_authid == project_crs.authid():
            return

        self.issues.append(DiagnosticIssue(
            id="CRS-07",
            category="CRS",
            issue_type="config_warning",
            severity="WARNING",
            title="SR predefinito per nuovi layer ≠ SR del progetto",
            explanation=(
                f"Quando crei un nuovo layer (memory layer, scratch layer, layer da "
                f"elaborazione), QGIS gli assegna automaticamente {default_crs_authid} "
                f"invece di {project_crs.authid()} (il SR del progetto corrente).\n\n"
                "Conseguenza pratica: ogni layer creato in QGIS senza specificare "
                "esplicitamente il SR parte con il SR sbagliato. Se non te ne accorgi "
                "e ci digitalizzi sopra, i dati saranno in un SR diverso dal resto del "
                "progetto — il classico errore silenzioso."
            ),
            suggestion=(
                "Per allineare il SR predefinito al progetto corrente:\n"
                "  Impostazioni → Opzioni → SR e trasformazioni\n"
                "  → sezione 'SR predefinito per i nuovi layer'\n"
                f"  → cambia da {default_crs_authid} a {project_crs.authid()}\n\n"
                "Oppure, per non dover cambiare questa impostazione ad ogni progetto:\n"
                "  Nella stessa sezione, imposta il comportamento su\n"
                "  'Usa il SR del progetto' — così segue automaticamente il progetto."
            ),
            technical_detail=(
                f"QgsSettings /projections/defaultBehaviour: '{default_behavior}'\n"
                f"QgsSettings /projections/layerDefaultCrs: '{default_crs_authid}'\n"
                f"Project CRS: '{project_crs.authid()}'"
            ),
        ))

    def _crs08_ntv2_not_available(self, layers, project_crs):
        """
        CRS-08: ci sono layer con datum Monte Mario ma la griglia NTv2 italiana
        non è installata → la riproiezione al volo usa Helmert (~1-3 m di errore).
        Emesso una volta sola per progetto, non per ogni layer.
        """
        if not _HAS_QGIS or not project_crs.isValid():
            return

        # Controlla se c'è almeno un layer Monte Mario nel progetto
        has_mm = any(
            self._is_monte_mario(layer.crs())
            for layer in layers
            if layer.crs().isValid()
        )
        if not has_mm:
            return

        # Controlla se il progetto usa WGS84 (quindi ci sarà una trasformazione MM→WGS84)
        if not self._is_wgs84_family(project_crs):
            return

        if self._ntv2_grid_available():
            return  # griglia presente, nessun problema

        # Determina la trasformazione Helmert migliore disponibile
        # e quante trasformazioni Helmert esistono (per dare contesto preciso)
        helmert_accuracy = self._best_helmert_accuracy()

        self.issues.append(DiagnosticIssue(
            id="CRS-08",
            category="CRS",
            issue_type="config_warning",
            severity="WARNING",
            title="Trasformazione Monte Mario → WGS84: solo Helmert disponibile (~4 m)",
            explanation=(
                "Il progetto contiene layer in Monte Mario (Gauss-Boaga, EPSG:3003/3004) "
                f"e il SR del progetto è WGS84 ({project_crs.authid()}).\n\n"
                "QGIS usa Helmert 7 parametri: è la migliore trasformazione disponibile "
                f"senza griglie NTv2 — accuratezza ~{helmert_accuracy} m.\n\n"
                "Impatto pratico dell'errore di ~4 m:\n"
                "  ✓ Ricognizione archeologica, cartografia, visualizzazione → ok\n"
                "  ✗ Rilievi topografici, tracciati ferroviari, progetto esecutivo → no\n\n"
                "Situazione griglie NTv2 per l'Italia:\n"
                "  • Griglie ufficiali IGM: a pagamento, solo via VERTO Online (API)\n"
                "  • Griglie Sferlazza/Agrigento: gratuite, accuratezza sub-metro, \n"
                "    scaricabili da provincia.agrigento.it\n"
                "  • CDN PROJ: non include griglie nazionali italiane\n"
                "  • Versioni KilletSoft: cifrate, inutilizzabili in QGIS"
            ),
            suggestion=(
                "Per uso non ingegneristico: nessuna azione necessaria.\n\n"
                "Per installare le griglie NTv2 gratuite (Sferlazza, ~sub-metro):\n\n"
                "  STEP 1 — Scarica il file Roma40→WGS84 (.gsb, 2.7 MB):\n"
                "  https://www.provincia.agrigento.it/flex/cm/pages/ServeBLOB.php/L/IT/IDPagina/309\n\n"
                "  STEP 2 — Copia i file in:\n"
                "  ~/Library/Application Support/proj/\n"
                "  con i nomi esatti che PROJ si aspetta:\n"
                "    35160622_47161840_R40_F00.gsb  (Roma40→WGS84)\n"
                "    35160622_47161840_R40_F89.gsb  (copia dello stesso)\n\n"
                "  STEP 3 — Riavvia QGIS\n\n"
                "  STEP 4 — Imposta la trasformazione preferita:\n"
                "  Impostazioni → Opzioni → SR e trasformazioni\n"
                "  → Trasformazioni di Coordinate → clicca +\n"
                f"  SR sorgente: EPSG:3004, SR destinazione: {project_crs.authid()}\n"
                "  → seleziona la voce con accuratezza 0.1 m\n\n"
                "Per uso ingegneristico (ITALFERR, topografia di precisione):\n"
                "  Le griglie ufficiali IGM si ottengono tramite la stazione\n"
                "  appaltante, un geodeta incaricato, o acquisto diretto da IGM.\n"
                "  VERTO Online (igmi.esercito.difesa.it) trasforma singoli dataset\n"
                "  server-side con le griglie ufficiali — gratuito e preciso."
            ),
            technical_detail=(
                f"Helmert migliore disponibile: accuratezza ~{helmert_accuracy} m\n"
                "Griglie Sferlazza (sub-metro, gratuite):\n"
                "  URL: provincia.agrigento.it/flex/cm/pages/ServeBLOB.php/L/IT/IDPagina/309\n"
                "  Nome da usare: 35160622_47161840_R40_F00.gsb → ~/Library/Application Support/proj/\n"
                "Griglie NTv2 note al DB PROJ (non presenti nel CDN):\n"
                "  35160622_47161840_R40_F00.gsb (Roma40→RDN2008, 0.1m)\n"
                "  35160622_47161840_R40_F89.gsb (Roma40→IGM95, 0.1m)"
            ),
        ))

    def _crs04_degrees_project(self, project_crs):
        if not project_crs.isValid():
            return
        if project_crs.mapUnits() == 6:  # QgsUnitTypes.DistanceDegrees == 6
            self.issues.append(DiagnosticIssue(
                id="CRS-04",
                category="CRS",
                issue_type="user_error",
                severity="WARNING",
                title="Progetto in gradi — misure di distanza non utilizzabili",
                explanation=(
                    f"Il SR del progetto ({project_crs.authid()}) usa gradi come unità. "
                    "Le misure di distanza e area in QGIS saranno in gradi, non in metri — "
                    "un risultato privo di significato per quasi tutti gli usi pratici."
                ),
                suggestion=(
                    "Cambia il SR del progetto in uno proiettato (in metri) adatto alla tua area:\n"
                    "  Progetto → Proprietà → SR\n"
                    "  Per l'Italia: EPSG:32632 (UTM 32N) o EPSG:32633 (UTM 33N)"
                ),
                technical_detail=f"project CRS: {project_crs.authid()}, "
                                  f"mapUnits: {project_crs.mapUnits()}",
            ))

    def _crs05_out_of_bounds(self, layer):
        crs = layer.crs()
        if not crs.isValid():
            return
        bounds = crs.bounds()
        if not bounds.isNull():
            extent = layer.extent()
            if extent.isNull() or extent.isEmpty():
                return
            if crs.isGeographic():
                if (extent.xMinimum() < bounds.xMinimum() - 1 or
                        extent.xMaximum() > bounds.xMaximum() + 1 or
                        extent.yMinimum() < bounds.yMinimum() - 1 or
                        extent.yMaximum() > bounds.yMaximum() + 1):
                    guessed_authid, guessed_label, _ = \
                        self._guess_crs_from_extent(extent)
                    extra = (
                        f"\n  SR stimato dalle coordinate: {guessed_authid} ({guessed_label})"
                        if guessed_authid else ""
                    )
                    self.issues.append(DiagnosticIssue(
                        id="CRS-05",
                        category="CRS",
                        issue_type="user_error",
                        severity="WARNING",
                        title="Coordinate fuori dai limiti validi per il SR dichiarato",
                        explanation=(
                            f"Il layer \"{layer.name()}\" ha coordinate fuori dai limiti "
                            f"validi per {crs.authid()}. Probabilmente il SR assegnato non "
                            f"corrisponde al sistema reale dei dati.{extra}"
                        ),
                        suggestion=(
                            "Controlla il SR corretto del layer in Layer → Proprietà → Informazioni.\n"
                            "Poi usa 'Assegna SR' (non Riproietta) per correggere l'etichetta.\n"
                            "  Tasto destro → Proprietà → Sorgente → icona SR."
                        ),
                        technical_detail=(
                            f"Extent layer: {extent.toString()}, "
                            f"Limiti CRS: {bounds.toString()}"
                        ),
                        layer_name=layer.name(),
                    ))

    # ── MODULO 2 — Layer (LAY-01..08) ────────────────────────────────────────

    def _check_layers(self, layers):
        for layer in layers:
            try:
                self._lay01_invalid(layer)
            except Exception:
                pass
            try:
                self._lay02_absolute_path(layer)
            except Exception:
                pass
            try:
                self._lay03_special_chars(layer)
            except Exception:
                pass
            try:
                self._lay04_temp_layer(layer)
            except Exception:
                pass
            try:
                self._lay05_duplicate(layers)
            except Exception:
                pass
            try:
                self._lay06_empty_vector(layer)
            except Exception:
                pass
            try:
                self._lay07_shp_missing_prj(layer)
            except Exception:
                pass
            try:
                self._lay08_shp_missing_shx(layer)
            except Exception:
                pass

        # LAY-11 and LAY-13 per-layer
        for layer in layers:
            try:
                self._lay11_unsaved_edits(layer)
            except Exception:
                pass
            try:
                self._lay13_raster_nodata(layer)
            except Exception:
                pass

    def _lay01_invalid(self, layer):
        if not layer.isValid():
            source = layer.source()
            # Determine issue_type: file missing → user_error, file exists but invalid → known_bug
            issue_type = "user_error"
            extra = ""
            if source and not source.startswith("memory"):
                # Extract file path for shapefiles etc.
                path = source.split("|")[0] if "|" in source else source
                if os.path.exists(path):
                    issue_type = "known_bug"
                    extra = " The file exists on disk — this may be a QGIS bug."
                else:
                    extra = " The file was not found at its recorded path."

            self.issues.append(DiagnosticIssue(
                id="LAY-01",
                category="Layers",
                issue_type=issue_type,
                severity="ERROR",
                title="Layer invalid: data source unreachable",
                explanation=(
                    f"Layer \"{layer.name()}\" could not be loaded.{extra} "
                    "It will appear broken in the map and cannot be used for analysis."
                ),
                suggestion=(
                    "Right-click the layer → Repair Data Source, or remove and re-add the layer."
                )
                if issue_type == "user_error" else
                (
                    "Try removing and re-adding the layer. If the problem persists, "
                    "check the QGIS message log for details."
                ),
                technical_detail=f"layer.source()='{source}', layer.isValid()=False",
                search_keywords=["layer invalid", "data source unreachable"],
                layer_name=layer.name(),
            ))

    def _lay02_absolute_path(self, layer):
        source = layer.source()
        if not source:
            return
        path = source.split("|")[0] if "|" in source else source
        if os.path.isabs(path) and os.path.exists(path):
            self.issues.append(DiagnosticIssue(
                id="LAY-02",
                category="Layers",
                issue_type="config_warning",
                severity="WARNING",
                title="Layer uses absolute path — may break if files are moved",
                explanation=(
                    f"Layer \"{layer.name()}\" is linked using an absolute path. "
                    "If you move the project folder or share it with others, "
                    "the link will break."
                ),
                suggestion=(
                    "Go to Project → Properties → General and set paths to Relative."
                ),
                technical_detail=f"source path: '{path}'",
                layer_name=layer.name(),
            ))

    def _lay03_special_chars(self, layer):
        import platform
        if platform.system() != "Windows":
            return
        source = layer.source()
        if not source:
            return
        special = set('#%&{}\\<>*?/$!\'":@+`|=')
        path = source.split("|")[0] if "|" in source else source
        found = [c for c in path if c in special]
        if found:
            self.issues.append(DiagnosticIssue(
                id="LAY-03",
                category="Layers",
                issue_type="user_error",
                severity="ERROR",
                title="Layer path contains special characters",
                explanation=(
                    f"Layer \"{layer.name()}\" has a path with characters "
                    f"({', '.join(set(found))}) that can cause loading failures on Windows."
                ),
                suggestion="Rename the file/folder to remove special characters, then repair the layer.",
                technical_detail=f"path: '{path}', problematic chars: {set(found)}",
                layer_name=layer.name(),
            ))

    def _lay04_temp_layer(self, layer):
        source = layer.source()
        if source and source.startswith("memory"):
            self.issues.append(DiagnosticIssue(
                id="LAY-04",
                category="Layers",
                issue_type="user_error",
                severity="WARNING",
                title="Temporary layer will be lost when project is closed",
                explanation=(
                    f"Layer \"{layer.name()}\" is a temporary (memory) layer. "
                    "Its contents exist only in RAM and will be permanently lost "
                    "when QGIS is closed or the project is reloaded."
                ),
                suggestion=(
                    "Right-click the layer → Make Permanent to save it to disk."
                ),
                technical_detail="layer.source() starts with 'memory:'",
                layer_name=layer.name(),
                auto_fixable=False,
            ))

    def _lay05_duplicate(self, layers):
        seen_sources = {}
        for layer in layers:
            src = layer.source()
            if not src or src.startswith("memory"):
                continue
            path = src.split("|")[0] if "|" in src else src
            if path in seen_sources:
                # Only emit once per duplicated source
                existing_name = seen_sources[path]
                # Check we haven't already reported this pair
                already = any(
                    i.id == "LAY-05" and i.technical_detail and path in i.technical_detail
                    for i in self.issues
                )
                if not already:
                    self.issues.append(DiagnosticIssue(
                        id="LAY-05",
                        category="Layers",
                        issue_type="info",
                        severity="INFO",
                        title="Duplicate layer detected",
                        explanation=(
                            f"Layers \"{existing_name}\" and \"{layer.name()}\" "
                            "point to the same data source. This is usually unintentional "
                            "and wastes memory."
                        ),
                        suggestion="Remove one of the duplicate layers if it is not needed.",
                        technical_detail=f"shared source: '{path}'",
                    ))
            else:
                seen_sources[path] = layer.name()

    def _lay06_empty_vector(self, layer):
        if layer.type() != QgsMapLayer.VectorLayer:
            return
        vec = layer
        if hasattr(vec, 'featureCount') and vec.featureCount() == 0:
            self.issues.append(DiagnosticIssue(
                id="LAY-06",
                category="Layers",
                issue_type="config_warning",
                severity="WARNING",
                title="Vector layer has no features",
                explanation=(
                    f"Layer \"{layer.name()}\" is a vector layer with no features. "
                    "It may be the result of an empty query, a failed import, "
                    "or a newly created layer not yet populated."
                ),
                suggestion=(
                    "Check the layer source and filter settings. "
                    "If this is intentional, you can ignore this warning."
                ),
                technical_detail=f"featureCount()=0",
                layer_name=layer.name(),
            ))

    def _lay07_shp_missing_prj(self, layer):
        source = layer.source()
        if not source:
            return
        path = source.split("|")[0] if "|" in source else source
        if not path.lower().endswith(".shp"):
            return
        prj_path = path[:-4] + ".prj"
        if not os.path.exists(prj_path):
            self.issues.append(DiagnosticIssue(
                id="LAY-07",
                category="Layers",
                issue_type="user_error",
                severity="ERROR",
                title="Shapefile missing projection file (.prj)",
                explanation=(
                    f"Layer \"{layer.name()}\" is a shapefile but its .prj file is missing. "
                    "QGIS cannot determine the correct CRS, and the layer may be positioned "
                    "incorrectly."
                ),
                suggestion=(
                    "Create a .prj file by exporting the layer with the correct CRS, "
                    "or manually assign the CRS in Layer Properties → Source."
                ),
                technical_detail=f"expected .prj at: '{prj_path}'",
                layer_name=layer.name(),
            ))

    def _lay08_shp_missing_shx(self, layer):
        source = layer.source()
        if not source:
            return
        path = source.split("|")[0] if "|" in source else source
        if not path.lower().endswith(".shp"):
            return
        shx_path = path[:-4] + ".shx"
        if not os.path.exists(shx_path):
            self.issues.append(DiagnosticIssue(
                id="LAY-08",
                category="Layers",
                issue_type="user_error",
                severity="WARNING",
                title="Shapefile missing index file (.shx)",
                explanation=(
                    f"Layer \"{layer.name()}\" is a shapefile but its .shx index file is missing. "
                    "QGIS may fail to read features, or read them incorrectly."
                ),
                suggestion=(
                    "Restore the .shx file from a backup, or recreate the shapefile "
                    "from the original data source."
                ),
                technical_detail=f"expected .shx at: '{shx_path}'",
                layer_name=layer.name(),
            ))

    # ── MODULO 5 — Project settings ──────────────────────────────────────────

    def _check_project(self, project, layers):
        for check in [
            lambda: self._prj01_unsaved(project),
            lambda: self._prj02_absolute_paths(project),
            lambda: self._prj03_no_title(project),
            lambda: self._prj04_degree_units(project),
            lambda: self._prj05_no_ellipsoid(project),
            lambda: self._prj06_many_layers(layers),
            lambda: self._prj07_custom_vars(project),
        ]:
            try:
                check()
            except Exception:
                pass

    def _prj01_unsaved(self, project):
        if project.isDirty():
            self.issues.append(DiagnosticIssue(
                id="PRJ-01",
                category="Project",
                issue_type="user_error",
                severity="WARNING",
                title="Project has unsaved changes",
                explanation=(
                    "Your project has unsaved changes. If QGIS crashes or is closed "
                    "unexpectedly, these changes will be lost."
                ),
                suggestion="Save the project now: Ctrl+S (or Cmd+S on Mac).",
                technical_detail="QgsProject.instance().isDirty() == True",
            ))

    def _prj02_absolute_paths(self, project):
        if project.filePathStorage() == 0:  # Qgis.FilePathType.Absolute == 0
            self.issues.append(DiagnosticIssue(
                id="PRJ-02",
                category="Project",
                issue_type="config_warning",
                severity="WARNING",
                title="Project uses absolute paths — reduced portability",
                explanation=(
                    "The project is configured to store file paths as absolute. "
                    "This means the project file will not work correctly if moved "
                    "to a different location or shared with others."
                ),
                suggestion=(
                    "Change to relative paths: Project → Properties → General → "
                    "set 'Save paths' to Relative."
                ),
                technical_detail="QgsProject.filePathStorage() == Absolute",
            ))

    def _prj03_no_title(self, project):
        if not project.title().strip():
            self.issues.append(DiagnosticIssue(
                id="PRJ-03",
                category="Project",
                issue_type="info",
                severity="INFO",
                title="Project has no title",
                explanation=(
                    "The project has no title set. A title helps identify the project "
                    "in layouts, metadata, and project listings."
                ),
                suggestion="Set a title in Project → Properties → General.",
                technical_detail="QgsProject.title() is empty",
            ))

    def _prj04_degree_units(self, project):
        try:
            from qgis.core import QgsUnitTypes
            units = project.distanceUnits()
            # DistanceDegrees
            if units == QgsUnitTypes.DistanceDegrees:
                self.issues.append(DiagnosticIssue(
                    id="PRJ-04",
                    category="Project",
                    issue_type="user_error",
                    severity="WARNING",
                    title="Project measurement units set to degrees",
                    explanation=(
                        "The project distance unit is set to degrees. "
                        "Length and area calculations will return values in degrees, "
                        "which are not meaningful for most GIS work."
                    ),
                    suggestion=(
                        "Change the unit in Project → Properties → General → "
                        "Measurements section."
                    ),
                    technical_detail=f"project.distanceUnits() == DistanceDegrees",
                ))
        except Exception:
            pass

    def _prj05_no_ellipsoid(self, project):
        ellipsoid = project.ellipsoid()
        if not ellipsoid or ellipsoid in ("", "NONE", "WGS84"):
            # WGS84 is fine; NONE is the problem
            if ellipsoid in ("", "NONE"):
                self.issues.append(DiagnosticIssue(
                    id="PRJ-05",
                    category="Project",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Ellipsoid not configured — geodetic calculations unavailable",
                    explanation=(
                        "No ellipsoid is set for this project. "
                        "Geodetic distance and area calculations (which account for Earth's "
                        "curvature) will not be available."
                    ),
                    suggestion=(
                        "Set an ellipsoid in Project → Properties → General → Measurements."
                    ),
                    technical_detail=f"project.ellipsoid() == '{ellipsoid}'",
                ))

    def _prj06_many_layers(self, layers):
        if len(layers) > 50:
            self.issues.append(DiagnosticIssue(
                id="PRJ-06",
                category="Project",
                issue_type="config_warning",
                severity="WARNING",
                title="Large project — performance may be affected",
                explanation=(
                    f"The project contains {len(layers)} layers. "
                    "Large layer counts can significantly slow down rendering, "
                    "saving, and loading times."
                ),
                suggestion=(
                    "Consider removing unused layers, grouping layers, "
                    "or splitting the project into smaller sub-projects."
                ),
                technical_detail=f"layer count: {len(layers)}",
            ))

    def _prj07_custom_vars(self, project):
        try:
            from qgis.core import QgsExpressionContextUtils
            vars_ = QgsExpressionContextUtils.projectScope(project).variableNames()
            # Filter out built-in QGIS variables
            builtin = {"project_title", "project_path", "project_filename",
                       "project_folder", "project_home", "project_crs",
                       "project_units", "project_ellipsoid", "project_distance_units",
                       "project_area_units", "project_basename", "project_last_saved",
                       "project_author", "project_abstract", "project_creation_date",
                       "project_keywords", "project_identifier", "project_version"}
            custom = [v for v in vars_ if v not in builtin]
            if custom:
                self.issues.append(DiagnosticIssue(
                    id="PRJ-07",
                    category="Project",
                    issue_type="info",
                    severity="INFO",
                    title="Custom project variables detected",
                    explanation=(
                        f"The project defines {len(custom)} custom variable(s): "
                        f"{', '.join(custom[:5])}{'...' if len(custom) > 5 else ''}. "
                        "These are used in expressions and may affect layer behaviour."
                    ),
                    suggestion=(
                        "Review in Project → Properties → Variables to verify they "
                        "are still needed and correctly defined."
                    ),
                    technical_detail=f"custom variables: {custom}",
                ))
        except Exception:
            pass

    # ── MODULO 8 — Log ───────────────────────────────────────────────────────

    def _check_log(self):
        for check in [
            self._log01_critical,
            self._log02_repeated_warnings,
            self._log03_plugin_messages,
            self._log04_db_errors,
        ]:
            try:
                check()
            except Exception:
                pass

    def _log01_critical(self):
        critical = [
            (tag, msg) for tag, level, msg in self.log_buffer
            if level >= 2  # Qgis.Critical == 2
        ]
        if critical:
            sample = critical[:3]
            self.issues.append(DiagnosticIssue(
                id="LOG-01",
                category="Log",
                issue_type="known_bug",
                severity="ERROR",
                title="Critical errors in QGIS message log",
                explanation=(
                    f"QGIS has logged {len(critical)} critical error message(s) since startup. "
                    "These may indicate serious problems with plugins, data sources, or QGIS itself."
                ),
                suggestion=(
                    "Open View → Panels → Log Messages to see the full log. "
                    "Note the source tag of the errors to identify which component is failing."
                ),
                technical_detail="Critical messages: " + " | ".join(
                    f"[{t}] {m[:100]}" for t, m in sample
                ),
                search_keywords=["critical error", "QGIS log"],
            ))

    def _log02_repeated_warnings(self):
        warning_msgs = [
            msg for tag, level, msg in self.log_buffer
            if level == 1  # Qgis.Warning == 1
        ]
        # Count repetitions
        from collections import Counter
        counts = Counter(warning_msgs)
        repeated = [(msg, cnt) for msg, cnt in counts.items() if cnt > 3]
        if repeated:
            top = repeated[:2]
            self.issues.append(DiagnosticIssue(
                id="LOG-02",
                category="Log",
                issue_type="known_bug",
                severity="WARNING",
                title="Repeated warnings in log",
                explanation=(
                    f"QGIS has logged {len(repeated)} warning message(s) more than 3 times. "
                    "Repeated warnings often indicate a systematic problem."
                ),
                suggestion=(
                    "Check View → Panels → Log Messages for the full warning text "
                    "and identify the component generating it."
                ),
                technical_detail=" | ".join(
                    f"'{m[:80]}' × {c}" for m, c in top
                ),
                search_keywords=["QGIS warning log repeated"],
            ))

    def _log03_plugin_messages(self):
        plugin_tags = [
            tag for tag, level, msg in self.log_buffer
            if tag not in ("QGIS", "Processing", "Python", "QgsProject", "")
            and level == 0  # Info
        ]
        unique_tags = list(dict.fromkeys(plugin_tags))
        if unique_tags:
            self.issues.append(DiagnosticIssue(
                id="LOG-03",
                category="Log",
                issue_type="info",
                severity="INFO",
                title="Plugin messages in log",
                explanation=(
                    f"The following plugins have written messages to the log: "
                    f"{', '.join(unique_tags[:5])}."
                ),
                suggestion=(
                    "These are informational. Check the log if a plugin is misbehaving."
                ),
                technical_detail=f"plugin log tags: {unique_tags}",
            ))

    def _log04_db_errors(self):
        db_errors = [
            msg for tag, level, msg in self.log_buffer
            if any(kw in msg.lower() for kw in
                   ["connection refused", "unable to connect", "authentication failed",
                    "postgis", "postgres", "spatialite", "oracle", "mssql",
                    "could not connect to server"])
        ]
        if db_errors:
            self.issues.append(DiagnosticIssue(
                id="LOG-04",
                category="Log",
                issue_type="user_error",
                severity="ERROR",
                title="Database connection error",
                explanation=(
                    f"QGIS logged {len(db_errors)} database connection error(s). "
                    "Layers connected to a database may not load correctly."
                ),
                suggestion=(
                    "Check your database connection settings in the Browser panel "
                    "(right-click the connection → Edit). Verify the server is running "
                    "and your credentials are correct."
                ),
                technical_detail=db_errors[0][:200] if db_errors else "",
            ))

    # ── MODULO Phase 2 — LAY-11, LAY-13 ─────────────────────────────────────

    def _lay11_unsaved_edits(self, layer):
        """LAY-11: layer in modalità editing con modifiche non salvate."""
        try:
            if layer.isEditable() and layer.isModified():
                self.issues.append(DiagnosticIssue(
                    id="LAY-11",
                    category="Layers",
                    issue_type="user_error",
                    severity="WARNING",
                    title="Layer has unsaved edits",
                    explanation=(
                        f"Layer \"{layer.name()}\" is in edit mode with unsaved changes. "
                        "These changes will be lost if QGIS crashes or closes unexpectedly."
                    ),
                    suggestion=(
                        "Save your edits: Layer → Save Layer Edits (pencil icon in toolbar).\n"
                        "Or stop editing: Layer → Toggle Editing → click 'Save'."
                    ),
                    technical_detail=(
                        f"layer.isEditable()=True, layer.isModified()=True"
                    ),
                    layer_name=layer.name(),
                    auto_fixable=True,
                ))
        except Exception:
            pass

    def _lay13_raster_nodata(self, layer):
        """LAY-13: raster senza valore NoData impostato."""
        try:
            if layer.type() != QgsMapLayer.RasterLayer:
                return
            provider = layer.dataProvider()
            if provider is None:
                return
            bands_no_nodata = []
            for band in range(1, layer.bandCount() + 1):
                try:
                    src_nodata = provider.sourceNoDataValue(band)
                    has_nodata = provider.sourceHasNoDataValue(band)
                    if not has_nodata:
                        bands_no_nodata.append(band)
                except Exception:
                    pass
            if bands_no_nodata:
                self.issues.append(DiagnosticIssue(
                    id="LAY-13",
                    category="Layers",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Raster layer has no NoData value set",
                    explanation=(
                        f"Raster layer \"{layer.name()}\" has no NoData value configured "
                        f"for band(s): {bands_no_nodata}. "
                        "Pixels that should be transparent may appear as black or white borders."
                    ),
                    suggestion=(
                        "Set a NoData value in Layer Properties → Transparency → "
                        "Additional No Data Value.\n"
                        "Common values: 0 (for black background), 255 (for white), "
                        "-9999 or -32768 (for DEMs)."
                    ),
                    technical_detail=(
                        f"Bands without NoData: {bands_no_nodata}, "
                        f"total bands: {layer.bandCount()}"
                    ),
                    layer_name=layer.name(),
                ))
        except Exception:
            pass

    # ── MODULO Phase 2 — JOINS ───────────────────────────────────────────────

    def _check_joins(self, project, layers):
        """JOI-01..04: problemi con join e relazioni."""
        layer_ids = set(project.mapLayers().keys())

        for layer in layers:
            try:
                if layer.type() != QgsMapLayer.VectorLayer:
                    continue
                joins = layer.vectorJoins()
                for join_info in joins:
                    try:
                        self._joi01_missing_source(layer, join_info, layer_ids)
                    except Exception:
                        pass
                    try:
                        self._joi02_type_mismatch(layer, join_info, project)
                    except Exception:
                        pass
                    try:
                        self._joi03_no_matches(layer, join_info, project)
                    except Exception:
                        pass
            except Exception:
                pass

        # JOI-04: relazioni con layer assente
        try:
            self._joi04_missing_relation_layer(project, layer_ids)
        except Exception:
            pass

    def _joi01_missing_source(self, layer, join_info, layer_ids):
        join_layer_id = join_info.joinLayerId()
        if join_layer_id not in layer_ids:
            self.issues.append(DiagnosticIssue(
                id="JOI-01",
                category="Joins",
                issue_type="user_error",
                severity="ERROR",
                title="Join references a missing layer",
                explanation=(
                    f"Layer \"{layer.name()}\" has a join referencing layer ID "
                    f"\"{join_layer_id}\" which is not loaded in the project. "
                    "The join will not work."
                ),
                suggestion=(
                    "Remove the broken join: Layer Properties → Joins → select the join → "
                    "click the minus (−) button.\n"
                    "Then re-add the join after loading the source layer."
                ),
                technical_detail=(
                    f"join.joinLayerId()='{join_layer_id}' not in project.mapLayers()"
                ),
                layer_name=layer.name(),
            ))

    def _joi02_type_mismatch(self, layer, join_info, project):
        join_layer_id = join_info.joinLayerId()
        join_layer = project.mapLayer(join_layer_id)
        if join_layer is None:
            return  # handled by JOI-01

        join_field_name = join_info.joinFieldName()
        target_field_name = join_info.targetFieldName()

        join_field = join_layer.fields().field(join_field_name) if join_field_name else None
        target_field = layer.fields().field(target_field_name) if target_field_name else None

        if join_field and target_field:
            if join_field.type() != target_field.type():
                self.issues.append(DiagnosticIssue(
                    id="JOI-02",
                    category="Joins",
                    issue_type="user_error",
                    severity="WARNING",
                    title="Join fields have incompatible types",
                    explanation=(
                        f"Layer \"{layer.name()}\" joins on field \"{target_field_name}\" "
                        f"(type {target_field.typeName()}) to field \"{join_field_name}\" "
                        f"(type {join_field.typeName()}) in \"{join_layer.name()}\". "
                        "Type mismatch can cause all rows to fail to match."
                    ),
                    suggestion=(
                        "Ensure both join fields have the same data type (e.g., both Integer "
                        "or both String). Use Processing → Field Calculator to convert if needed."
                    ),
                    technical_detail=(
                        f"target field type: {target_field.typeName()}, "
                        f"join field type: {join_field.typeName()}"
                    ),
                    layer_name=layer.name(),
                ))

    def _joi03_no_matches(self, layer, join_info, project):
        join_layer_id = join_info.joinLayerId()
        join_layer = project.mapLayer(join_layer_id)
        if join_layer is None:
            return

        join_field_name = join_info.joinFieldName()
        target_field_name = join_info.targetFieldName()
        if not join_field_name or not target_field_name:
            return

        try:
            # Campiona fino a 200 feature per verificare se ci sono corrispondenze
            sample_values = set()
            for feat in layer.getFeatures():
                val = feat[target_field_name]
                if val is not None:
                    sample_values.add(val)
                if len(sample_values) >= 200:
                    break

            if not sample_values:
                return

            join_values = set()
            for feat in join_layer.getFeatures():
                val = feat[join_field_name]
                if val is not None:
                    join_values.add(val)
                if len(join_values) >= 500:
                    break

            if join_values and sample_values.isdisjoint(join_values):
                self.issues.append(DiagnosticIssue(
                    id="JOI-03",
                    category="Joins",
                    issue_type="user_error",
                    severity="WARNING",
                    title="Join produces zero matches",
                    explanation=(
                        f"Layer \"{layer.name()}\" join on \"{target_field_name}\" → "
                        f"\"{join_layer.name()}\".\"{join_field_name}\" produced no matches "
                        "in the sampled features. Check that the key values are consistent."
                    ),
                    suggestion=(
                        "Open the attribute tables of both layers and compare the key field "
                        "values. Common issues: leading/trailing spaces, different casing, "
                        "numeric vs string format (e.g. 1 vs '1')."
                    ),
                    technical_detail=(
                        f"Sample target values (up to 5): {list(sample_values)[:5]}, "
                        f"sample join values (up to 5): {list(join_values)[:5]}"
                    ),
                    layer_name=layer.name(),
                ))
        except Exception:
            pass

    def _joi04_missing_relation_layer(self, project, layer_ids):
        try:
            rel_manager = project.relationManager()
            for relation in rel_manager.relations().values():
                ref_layer_id = relation.referencedLayerId()
                ref_ing_layer_id = relation.referencingLayerId()
                missing = []
                if ref_layer_id not in layer_ids:
                    missing.append(f"referenced layer: {ref_layer_id}")
                if ref_ing_layer_id not in layer_ids:
                    missing.append(f"referencing layer: {ref_ing_layer_id}")
                if missing:
                    self.issues.append(DiagnosticIssue(
                        id="JOI-04",
                        category="Joins",
                        issue_type="user_error",
                        severity="WARNING",
                        title=f"Relation \"{relation.name()}\" references missing layer(s)",
                        explanation=(
                            f"Relation \"{relation.name()}\" has missing layer(s): "
                            f"{', '.join(missing)}. Forms and related tools will not work."
                        ),
                        suggestion=(
                            "Go to Project → Properties → Relations to review and fix "
                            "or delete the broken relation."
                        ),
                        technical_detail=(
                            f"relation id: {relation.id()}, missing: {missing}"
                        ),
                    ))
        except Exception:
            pass

    # ── MODULO Phase 2 — PLUGINS ─────────────────────────────────────────────

    def _check_plugins(self, project):
        """PLG-01, PLG-03..05: problemi con plugin."""
        try:
            self._plg01_load_error()
        except Exception:
            pass
        try:
            self._plg03_incompatible()
        except Exception:
            pass
        try:
            self._plg04_too_many()
        except Exception:
            pass
        try:
            self._plg05_experimental()
        except Exception:
            pass

    def _plg01_load_error(self):
        error_entries = [
            (tag, msg) for tag, level, msg in self.log_buffer
            if level >= 1 and any(kw in msg.lower() for kw in
                                  ["traceback", "importerror", "modulenotfounderror",
                                   "syntaxerror", "failed to load plugin", "plugin error",
                                   "error loading plugin"])
        ]
        if error_entries:
            sample = error_entries[:2]
            self.issues.append(DiagnosticIssue(
                id="PLG-01",
                category="Plugins",
                issue_type="known_bug",
                severity="ERROR",
                title="Plugin load error detected in log",
                explanation=(
                    f"QGIS logged {len(error_entries)} error message(s) that suggest "
                    "a plugin failed to load. This can cause features to be unavailable "
                    "or errors during normal operation."
                ),
                suggestion=(
                    "1. Go to View → Panels → Log Messages and filter by 'Plugins'.\n"
                    "2. Identify the failing plugin from the log.\n"
                    "3. Try: Plugins → Manage and Install Plugins → find the plugin → "
                    "   Reinstall or Uninstall it.\n"
                    "4. If it's a third-party plugin, check if it's compatible with your "
                    "   QGIS version."
                ),
                technical_detail=" | ".join(
                    f"[{t}] {m[:120]}" for t, m in sample
                ),
                search_keywords=["QGIS plugin load error", "ImportError plugin"],
            ))

    def _plg03_incompatible(self):
        try:
            from qgis.core import Qgis
            import re as _re
            current_ver = Qgis.QGIS_VERSION
            # Parse to (major, minor)
            m = _re.match(r"(\d+)\.(\d+)", current_ver)
            if not m:
                return
            current_major = int(m.group(1))
            current_minor = int(m.group(2))

            from qgis.utils import plugins
            import os

            plugins_dir = os.path.join(
                os.path.expanduser("~"),
                "Library", "Application Support", "QGIS", "QGIS3",
                "profiles", "default", "python", "plugins"
            )
            if not os.path.isdir(plugins_dir):
                return

            for plugin_name in os.listdir(plugins_dir):
                meta_path = os.path.join(plugins_dir, plugin_name, "metadata.txt")
                if not os.path.exists(meta_path):
                    continue
                try:
                    min_ver = None
                    max_ver = None
                    with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("qgisMinimumVersion="):
                                min_ver = line.split("=", 1)[1].strip()
                            elif line.startswith("qgisMaximumVersion="):
                                max_ver = line.split("=", 1)[1].strip()

                    if min_ver:
                        mm = _re.match(r"(\d+)\.(\d+)", min_ver)
                        if mm:
                            req_major = int(mm.group(1))
                            req_minor = int(mm.group(2))
                            if (current_major, current_minor) < (req_major, req_minor):
                                self.issues.append(DiagnosticIssue(
                                    id="PLG-03",
                                    category="Plugins",
                                    issue_type="known_bug",
                                    severity="WARNING",
                                    title=f"Plugin '{plugin_name}' requires newer QGIS",
                                    explanation=(
                                        f"Plugin '{plugin_name}' requires QGIS >= {min_ver} "
                                        f"but you are running {current_ver}. "
                                        "This plugin may not work correctly."
                                    ),
                                    suggestion=(
                                        f"Update QGIS to version {min_ver} or later, or "
                                        f"use an older version of '{plugin_name}'."
                                    ),
                                    technical_detail=(
                                        f"qgisMinimumVersion: {min_ver}, "
                                        f"current QGIS: {current_ver}"
                                    ),
                                ))
                except Exception:
                    pass
        except Exception:
            pass

    def _plg04_too_many(self):
        try:
            from qgis.utils import active_plugins
            count = len(active_plugins)
            if count > 20:
                self.issues.append(DiagnosticIssue(
                    id="PLG-04",
                    category="Plugins",
                    issue_type="config_warning",
                    severity="INFO",
                    title=f"Many active plugins ({count}) — may slow QGIS startup",
                    explanation=(
                        f"You have {count} plugins active. Each plugin is loaded at startup "
                        "and can increase QGIS loading time and memory usage. "
                        "Some plugins may also conflict with each other."
                    ),
                    suggestion=(
                        "Disable plugins you don't actively use:\n"
                        "  Plugins → Manage and Install Plugins → Installed tab\n"
                        "  Uncheck plugins you don't need."
                    ),
                    technical_detail=f"active_plugins count: {count}",
                ))
        except Exception:
            pass

    def _plg05_experimental(self):
        try:
            import os
            plugins_dir = os.path.join(
                os.path.expanduser("~"),
                "Library", "Application Support", "QGIS", "QGIS3",
                "profiles", "default", "python", "plugins"
            )
            if not os.path.isdir(plugins_dir):
                return

            from qgis.utils import active_plugins
            active_set = set(active_plugins)

            experimental = []
            for plugin_name in os.listdir(plugins_dir):
                if plugin_name not in active_set:
                    continue
                meta_path = os.path.join(plugins_dir, plugin_name, "metadata.txt")
                if not os.path.exists(meta_path):
                    continue
                try:
                    with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if line.strip().lower() == "experimental=true":
                                experimental.append(plugin_name)
                                break
                except Exception:
                    pass

            if experimental:
                self.issues.append(DiagnosticIssue(
                    id="PLG-05",
                    category="Plugins",
                    issue_type="config_warning",
                    severity="INFO",
                    title=f"Experimental plugin(s) active: {', '.join(experimental[:3])}",
                    explanation=(
                        f"The following plugin(s) are marked as experimental: "
                        f"{', '.join(experimental)}. "
                        "Experimental plugins may be unstable, incomplete, or cause "
                        "unexpected behaviour."
                    ),
                    suggestion=(
                        "Use experimental plugins with caution. If you experience instability, "
                        "try disabling them: Plugins → Manage and Install Plugins."
                    ),
                    technical_detail=f"experimental plugins: {experimental}",
                ))
        except Exception:
            pass

    # ── MODULO Phase 2 — DIGITIZING ──────────────────────────────────────────

    def _check_digitizing(self, project, layers):
        """DIG-01..05: configurazione digitizing e snapping."""
        for check in [
            lambda: self._dig01_snap_off_editing(project, layers),
            lambda: self._dig02_snap_tolerance_zero(project),
            lambda: self._dig03_snap_invisible(project, layers),
            lambda: self._dig04_no_topo_editing(project, layers),
            lambda: self._dig05_avoid_intersections(project),
        ]:
            try:
                check()
            except Exception:
                pass

    def _dig01_snap_off_editing(self, project, layers):
        editing_layers = [l for l in layers if l.isEditable()]
        if not editing_layers:
            return
        try:
            snap_config = project.snappingConfig()
            if not snap_config.enabled():
                names = ", ".join(l.name() for l in editing_layers[:3])
                self.issues.append(DiagnosticIssue(
                    id="DIG-01",
                    category="Digitizing",
                    issue_type="config_warning",
                    severity="WARNING",
                    title="Snapping disabled while editing",
                    explanation=(
                        f"Layer(s) are in edit mode ({names}) but snapping is turned off. "
                        "Without snapping, digitized vertices may not align precisely with "
                        "existing geometry, creating gaps and overlaps."
                    ),
                    suggestion=(
                        "Enable snapping: View → Toolbars → Snapping Toolbar → click the "
                        "magnet icon, or press S.\n"
                        "Recommended: enable snapping to vertices and segments."
                    ),
                    technical_detail="snappingConfig.enabled()=False, layers in editing mode",
                ))
        except Exception:
            pass

    def _dig02_snap_tolerance_zero(self, project):
        try:
            snap_config = project.snappingConfig()
            if snap_config.enabled() and snap_config.tolerance() == 0:
                self.issues.append(DiagnosticIssue(
                    id="DIG-02",
                    category="Digitizing",
                    issue_type="config_warning",
                    severity="WARNING",
                    title="Snapping tolerance is 0",
                    explanation=(
                        "Snapping is enabled but the tolerance is set to 0. "
                        "With zero tolerance, snapping will only work when the cursor "
                        "is positioned exactly on a vertex — practically unusable."
                    ),
                    suggestion=(
                        "Set a reasonable snapping tolerance:\n"
                        "  View → Snapping Toolbar → set tolerance to 10–20 pixels\n"
                        "  or 0.5–2 map units depending on your scale."
                    ),
                    technical_detail="snappingConfig.tolerance()=0",
                ))
        except Exception:
            pass

    def _dig03_snap_invisible(self, project, layers):
        try:
            snap_config = project.snappingConfig()
            if not snap_config.enabled():
                return
            from qgis.core import QgsSnappingConfig
            # Mode 2 = advanced (per-layer)
            if snap_config.mode() == QgsSnappingConfig.AdvancedConfiguration:
                snap_layers = snap_config.individualLayerSettings()
                for layer_id, settings in snap_layers.items():
                    if not settings.enabled():
                        continue
                    layer = project.mapLayer(layer_id)
                    if layer and not layer.isVisible():
                        self.issues.append(DiagnosticIssue(
                            id="DIG-03",
                            category="Digitizing",
                            issue_type="config_warning",
                            severity="INFO",
                            title=f"Snapping enabled on invisible layer '{layer.name()}'",
                            explanation=(
                                f"Snapping is configured for layer \"{layer.name()}\" "
                                "but the layer is not visible. QGIS may still snap to it, "
                                "which can cause confusing behaviour."
                            ),
                            suggestion=(
                                "Either make the layer visible, or disable snapping for it "
                                "in View → Snapping Toolbar → Edit Snapping Configuration."
                            ),
                            technical_detail=f"layer.isVisible()=False, snapping enabled",
                            layer_name=layer.name(),
                        ))
        except Exception:
            pass

    def _dig04_no_topo_editing(self, project, layers):
        editing_layers = [l for l in layers if l.isEditable()]
        if not editing_layers:
            return
        try:
            snap_config = project.snappingConfig()
            if not snap_config.topologicalEditing():
                self.issues.append(DiagnosticIssue(
                    id="DIG-04",
                    category="Digitizing",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Topological editing disabled while editing",
                    explanation=(
                        "Topological editing is off. When you move a shared vertex, "
                        "it will only move in the layer you're editing — adjacent features "
                        "in other layers will not update, creating sliver gaps."
                    ),
                    suggestion=(
                        "Enable topological editing:\n"
                        "  View → Snapping Toolbar → click the topological editing button\n"
                        "  (looks like two overlapping polygons)."
                    ),
                    technical_detail="snappingConfig.topologicalEditing()=False",
                ))
        except Exception:
            pass

    def _dig05_avoid_intersections(self, project):
        try:
            snap_config = project.snappingConfig()
            from qgis.core import QgsSnappingConfig
            mode = snap_config.intersectionSnapping()
            if mode:
                self.issues.append(DiagnosticIssue(
                    id="DIG-05",
                    category="Digitizing",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Intersection snapping (avoid overlaps) is active",
                    explanation=(
                        "Intersection snapping / avoid overlaps is enabled. "
                        "This can cause automatic geometry modifications when digitizing "
                        "polygons, which may be unexpected."
                    ),
                    suggestion=(
                        "If you are seeing unexpected geometry changes while digitizing, "
                        "check: View → Snapping Toolbar → Avoid Overlap on Active Layer."
                    ),
                    technical_detail="snappingConfig.intersectionSnapping()=True",
                ))
        except Exception:
            pass

    # ── MODULO Phase 2 — RENDERING ───────────────────────────────────────────

    def _check_rendering(self, project, layers):
        """RND-01..07: problemi di rendering e performance."""
        for check in [
            lambda: self._rnd01_parallel_rendering(),
            lambda: self._rnd02_map_cache_disabled(),
            lambda: self._rnd05_missing_label_font(layers),
            lambda: self._rnd06_missing_svg(layers),
        ]:
            try:
                check()
            except Exception:
                pass

    def _rnd01_parallel_rendering(self):
        try:
            s = QgsSettings()
            parallel = s.value("/qgis/parallel_rendering", True)
            if parallel in (False, "false", "False", "0", 0):
                self.issues.append(DiagnosticIssue(
                    id="RND-01",
                    category="Rendering",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Parallel rendering disabled",
                    explanation=(
                        "Parallel (multi-threaded) rendering is disabled. "
                        "Rendering will use only one CPU core, which is significantly "
                        "slower on projects with many layers."
                    ),
                    suggestion=(
                        "Enable parallel rendering:\n"
                        "  Settings → Options → Rendering → check 'Use parallel rendering'."
                    ),
                    technical_detail="QgsSettings /qgis/parallel_rendering = False",
                ))
        except Exception:
            pass

    def _rnd02_map_cache_disabled(self):
        try:
            s = QgsSettings()
            cache = s.value("/qgis/enable_render_caching", True)
            if cache in (False, "false", "False", "0", 0):
                self.issues.append(DiagnosticIssue(
                    id="RND-02",
                    category="Rendering",
                    issue_type="config_warning",
                    severity="INFO",
                    title="Map cache disabled — rendering may be slower",
                    explanation=(
                        "The map rendering cache is disabled. QGIS will re-render every "
                        "layer on every map move, even if the data hasn't changed. "
                        "This significantly slows down panning and zooming."
                    ),
                    suggestion=(
                        "Enable render caching:\n"
                        "  Settings → Options → Rendering → check 'Use render caching'."
                    ),
                    technical_detail="QgsSettings /qgis/enable_render_caching = False",
                ))
        except Exception:
            pass

    def _rnd05_missing_label_font(self, layers):
        try:
            from qgis.core import QgsFontUtils, QgsPalLayerSettings
            for layer in layers:
                if layer.type() != QgsMapLayer.VectorLayer:
                    continue
                try:
                    labeling = layer.labeling()
                    if labeling is None:
                        continue
                    settings = labeling.settings()
                    fmt = settings.format()
                    family = fmt.font().family()
                    if family and not QgsFontUtils.isFontFamilyOnSystem(family):
                        self.issues.append(DiagnosticIssue(
                            id="RND-05",
                            category="Rendering",
                            issue_type="config_warning",
                            severity="WARNING",
                            title=f"Label font not found: '{family}'",
                            explanation=(
                                f"Layer \"{layer.name()}\" uses font \"{family}\" for labels, "
                                "but this font is not installed on your system. "
                                "Labels will be rendered with a fallback font."
                            ),
                            suggestion=(
                                f"Install the font '{family}' on your system, or change the "
                                "label font in Layer Properties → Labels → Text."
                            ),
                            technical_detail=f"font family: '{family}', isFontFamilyOnSystem=False",
                            layer_name=layer.name(),
                        ))
                except Exception:
                    pass
        except Exception:
            pass

    def _rnd06_missing_svg(self, layers):
        try:
            from qgis.core import QgsSvgMarkerSymbolLayer, QgsSymbol
            for layer in layers:
                if layer.type() != QgsMapLayer.VectorLayer:
                    continue
                try:
                    renderer = layer.renderer()
                    if renderer is None:
                        continue
                    symbols = renderer.symbols(None) if hasattr(renderer, 'symbols') else []
                    for symbol in symbols:
                        for i in range(symbol.symbolLayerCount()):
                            sl = symbol.symbolLayer(i)
                            if isinstance(sl, QgsSvgMarkerSymbolLayer):
                                path = sl.path()
                                if path and not os.path.exists(path):
                                    self.issues.append(DiagnosticIssue(
                                        id="RND-06",
                                        category="Rendering",
                                        issue_type="user_error",
                                        severity="WARNING",
                                        title="Missing SVG symbol file",
                                        explanation=(
                                            f"Layer \"{layer.name()}\" uses an SVG marker "
                                            f"at \"{path}\" which cannot be found. "
                                            "The layer will render with a default marker."
                                        ),
                                        suggestion=(
                                            "Fix the SVG path in Layer Properties → Symbology, "
                                            "or copy the SVG file to the expected location."
                                        ),
                                        technical_detail=f"SVG path: '{path}' not found",
                                        layer_name=layer.name(),
                                    ))
                except Exception:
                    pass
        except Exception:
            pass

    # ── MODULO Phase 2 — EXPRESSIONS ─────────────────────────────────────────

    def _check_expressions(self, layers):
        """EXP-01..03: espressioni non valide."""
        for layer in layers:
            try:
                self._exp01_virtual_fields(layer)
            except Exception:
                pass
            try:
                self._exp02_layer_filter(layer)
            except Exception:
                pass
            try:
                self._exp03_label_expression(layer)
            except Exception:
                pass

    def _exp01_virtual_fields(self, layer):
        if layer.type() != QgsMapLayer.VectorLayer:
            return
        try:
            from qgis.core import QgsExpression
            fields = layer.fields()
            for i in range(fields.count()):
                field = fields.field(i)
                if fields.fieldOrigin(i) == fields.OriginExpression:
                    expr_idx = fields.fieldOriginIndex(i)
                    expr_str = layer.expressionField(expr_idx)
                    if expr_str:
                        expr = QgsExpression(expr_str)
                        if expr.hasParserError():
                            self.issues.append(DiagnosticIssue(
                                id="EXP-01",
                                category="Expressions",
                                issue_type="user_error",
                                severity="WARNING",
                                title=f"Virtual field '{field.name()}' has invalid expression",
                                explanation=(
                                    f"Layer \"{layer.name()}\" has a virtual field "
                                    f"\"{field.name()}\" with an invalid expression. "
                                    f"Error: {expr.parserErrorString()}"
                                ),
                                suggestion=(
                                    "Edit the virtual field expression:\n"
                                    "  Layer Properties → Fields → click on the virtual field "
                                    "  → edit the expression."
                                ),
                                technical_detail=(
                                    f"field: '{field.name()}', expression: '{expr_str}', "
                                    f"error: {expr.parserErrorString()}"
                                ),
                                layer_name=layer.name(),
                            ))
        except Exception:
            pass

    def _exp02_layer_filter(self, layer):
        try:
            from qgis.core import QgsExpression
            subset = layer.subsetString()
            if subset:
                expr = QgsExpression(subset)
                if expr.hasParserError():
                    self.issues.append(DiagnosticIssue(
                        id="EXP-02",
                        category="Expressions",
                        issue_type="user_error",
                        severity="ERROR",
                        title="Layer filter expression is invalid",
                        explanation=(
                            f"Layer \"{layer.name()}\" has a filter (subset string) that "
                            f"is not valid. The layer may show no features or the wrong features.\n"
                            f"Expression: {subset[:100]}\n"
                            f"Error: {expr.parserErrorString()}"
                        ),
                        suggestion=(
                            "Fix the filter expression:\n"
                            "  Right-click layer → Filter (or Layer Properties → Source → "
                            "  Query Builder) → fix or remove the expression."
                        ),
                        technical_detail=(
                            f"subsetString: '{subset}', "
                            f"error: {expr.parserErrorString()}"
                        ),
                        layer_name=layer.name(),
                    ))
        except Exception:
            pass

    def _exp03_label_expression(self, layer):
        if layer.type() != QgsMapLayer.VectorLayer:
            return
        try:
            from qgis.core import QgsExpression, QgsPropertyDefinition
            labeling = layer.labeling()
            if labeling is None:
                return
            settings = labeling.settings()
            # Check the label field expression
            field_name = settings.fieldName
            if field_name and settings.isExpression:
                expr = QgsExpression(field_name)
                if expr.hasParserError():
                    self.issues.append(DiagnosticIssue(
                        id="EXP-03",
                        category="Expressions",
                        issue_type="user_error",
                        severity="WARNING",
                        title="Label expression is invalid",
                        explanation=(
                            f"Layer \"{layer.name()}\" uses an expression for labels "
                            f"that has a parser error: {expr.parserErrorString()}\n"
                            f"Expression: {field_name[:100]}"
                        ),
                        suggestion=(
                            "Fix the label expression:\n"
                            "  Layer Properties → Labels → click the expression icon (ε) "
                            "  next to the label field → fix the expression."
                        ),
                        technical_detail=(
                            f"label expression: '{field_name}', "
                            f"error: {expr.parserErrorString()}"
                        ),
                        layer_name=layer.name(),
                    ))
        except Exception:
            pass

    # ── MODULO Phase 2 — PRINT LAYOUTS ───────────────────────────────────────

    def _check_print_layouts(self, project):
        """LAY-PRT-01..03: problemi con i layout di stampa."""
        try:
            layout_manager = project.layoutManager()
            layer_ids = set(project.mapLayers().keys())
            project_crs = project.crs()

            for layout in layout_manager.layouts():
                try:
                    self._lay_prt01_missing_layers(layout, layer_ids)
                except Exception:
                    pass
                try:
                    self._lay_prt02_crs_mismatch(layout, project_crs)
                except Exception:
                    pass
                try:
                    self._lay_prt03_atlas_missing(layout, layer_ids)
                except Exception:
                    pass
        except Exception:
            pass

    def _lay_prt01_missing_layers(self, layout, layer_ids):
        try:
            from qgis.core import QgsLayoutItemMap
            for item in layout.items():
                if not isinstance(item, QgsLayoutItemMap):
                    continue
                try:
                    # Check layer set
                    map_layers = item.layerSet()
                    missing = [lid for lid in map_layers if lid not in layer_ids]
                    if missing:
                        self.issues.append(DiagnosticIssue(
                            id="LAY-PRT-01",
                            category="Layouts",
                            issue_type="user_error",
                            severity="WARNING",
                            title=f"Print layout '{layout.name()}' references missing layers",
                            explanation=(
                                f"Layout \"{layout.name()}\" has a map item referencing "
                                f"{len(missing)} layer(s) that are no longer in the project. "
                                "The layout may print incorrectly."
                            ),
                            suggestion=(
                                "Open the layout: Project → Layout Manager → open the layout.\n"
                                "Check the map item's layers: Item Properties → Layers.\n"
                                "Remove missing layers or re-add them to the project."
                            ),
                            technical_detail=(
                                f"layout: '{layout.name()}', "
                                f"missing layer IDs: {missing[:3]}"
                            ),
                        ))
                except Exception:
                    pass
        except Exception:
            pass

    def _lay_prt02_crs_mismatch(self, layout, project_crs):
        try:
            from qgis.core import QgsLayoutItemMap
            for item in layout.items():
                if not isinstance(item, QgsLayoutItemMap):
                    continue
                try:
                    map_crs = item.crs()
                    if (map_crs.isValid() and project_crs.isValid() and
                            map_crs.authid() != project_crs.authid()):
                        self.issues.append(DiagnosticIssue(
                            id="LAY-PRT-02",
                            category="Layouts",
                            issue_type="info",
                            severity="INFO",
                            title=f"Layout '{layout.name()}' uses different CRS from project",
                            explanation=(
                                f"Layout \"{layout.name()}\" map item uses {map_crs.authid()} "
                                f"while the project CRS is {project_crs.authid()}. "
                                "This is intentional if you need a specific projection for print, "
                                "but may cause confusion."
                            ),
                            suggestion=(
                                "If unintentional, change the map CRS in the layout:\n"
                                "  Select the map item → Item Properties → CRS."
                            ),
                            technical_detail=(
                                f"layout map CRS: {map_crs.authid()}, "
                                f"project CRS: {project_crs.authid()}"
                            ),
                        ))
                        break  # Una sola segnalazione per layout
                except Exception:
                    pass
        except Exception:
            pass

    def _lay_prt03_atlas_missing(self, layout, layer_ids):
        try:
            from qgis.core import QgsPrintLayout
            if not isinstance(layout, QgsPrintLayout):
                return
            atlas = layout.atlas()
            if atlas and atlas.enabled():
                coverage_layer = atlas.coverageLayer()
                if coverage_layer is None:
                    self.issues.append(DiagnosticIssue(
                        id="LAY-PRT-03",
                        category="Layouts",
                        issue_type="user_error",
                        severity="ERROR",
                        title=f"Atlas in layout '{layout.name()}' has no coverage layer",
                        explanation=(
                            f"Layout \"{layout.name()}\" has Atlas enabled but the "
                            "coverage layer is missing or not set. "
                            "Atlas generation will fail."
                        ),
                        suggestion=(
                            "Open the layout → Atlas menu → Atlas Settings.\n"
                            "Set a valid coverage layer, or disable Atlas if not needed."
                        ),
                        technical_detail=(
                            f"layout: '{layout.name()}', atlas.enabled()=True, "
                            "coverageLayer()=None"
                        ),
                    ))
        except Exception:
            pass
