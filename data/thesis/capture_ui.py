"""Playwright-basiertes Screenshot-Tool fuer Thesis-Abbildungen.

Erfasst hochaufloesende Screenshots der Wadi-Analytics Streamlit-UI
in publikationsreifer Qualitaet (300+ DPI bei A4-Druckgroesse).

Erfasst granulare Element-Captures: Willkommen, Chat-Input mit Frage,
jeden Analyse-Schritt (Erklaerung, stdout, Internals), Metriken-Bar,
Sidebar mit Kosten und die Kartenansicht.

Voraussetzungen:
    pip install -r data/thesis/requirements-thesis.txt
    playwright install chromium

Verwendung:
    # Alle Captures mit Standard-Frage (Streamlit muss laufen)
    python data/thesis/capture_ui.py

    # Nur Chat-Captures
    python data/thesis/capture_ui.py --pages chat

    # Eigene Frage
    python data/thesis/capture_ui.py --question "Meine Frage?"

    # Andere URL / Skalierung
    python data/thesis/capture_ui.py --url http://host:8501 --scale 2
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import async_playwright, Page, Locator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("capture_ui")

# Standard-Frage fuer die Thesis
DEFAULT_QUESTION = (
    "Gibt es eine signifikante räumliche Autokorrelation von "
    "Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) "
    "bzw. Mobilitätsindikatoren (shelter; stoneplace; camp site; fireplace; "
    "gravel platform)?"
)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
@dataclass
class CaptureConfig:
    """Konfiguration fuer UI-Captures."""
    base_url: str = "http://localhost:8501"
    output_dir: Path = Path("data/thesis/figures")
    viewport_width: int = 1920
    viewport_height: int = 1080
    device_scale_factor: int = 3
    wait_after_load_ms: int = 3000
    timeout_ms: int = 30000
    full_page: bool = True


# CSS zum Ausblenden von Streamlit-UI-Elementen die nicht in die Thesis gehoeren
CLEANUP_CSS = """
.stDeployButton { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stToolbar"] { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
.stStatusWidget { display: none !important; }
[data-testid="stToast"] { display: none !important; }
"""


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
async def _inject_cleanup_css(page: Page) -> None:
    """Injiziert CSS zum Ausblenden stoerrender Streamlit-UI-Elemente."""
    await page.add_style_tag(content=CLEANUP_CSS)
    log.debug("Cleanup-CSS injiziert")


async def _wait_for_streamlit(page: Page, config: CaptureConfig) -> None:
    """Wartet bis Streamlit vollstaendig geladen hat."""
    log.info("Warte auf Streamlit-Rendering...")
    await page.wait_for_load_state("networkidle")

    try:
        await page.wait_for_selector(
            "[data-testid='stApp']",
            state="attached",
            timeout=config.timeout_ms,
        )
    except Exception:
        log.warning("stApp-Selector nicht gefunden, fahre trotzdem fort")

    # Warte bis kein Spinner/Status-Widget mehr sichtbar ist
    try:
        await page.wait_for_function(
            """() => {
                const spinner = document.querySelector('[data-testid="stStatusWidget"]');
                return !spinner || spinner.offsetParent === null;
            }""",
            timeout=config.timeout_ms,
        )
    except Exception:
        log.debug("Spinner-Wait abgelaufen")

    await asyncio.sleep(config.wait_after_load_ms / 1000)
    log.info("Streamlit-Rendering abgeschlossen")


async def _save(page_or_loc: Page | Locator, path: Path, **kwargs) -> Path:
    """Speichert Screenshot und loggt Dateigroesse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    await page_or_loc.screenshot(path=str(path), **kwargs)
    size_kb = path.stat().st_size / 1024
    log.info("  -> %s (%.0f KB)", path.name, size_kb)
    return path


async def _navigate_and_prepare(page: Page, url: str, config: CaptureConfig) -> None:
    """Navigiert zu einer URL und bereitet die Seite vor."""
    log.info("Navigiere zu %s", url)
    await page.goto(url, wait_until="networkidle", timeout=config.timeout_ms)
    await _wait_for_streamlit(page, config)
    await _inject_cleanup_css(page)
    await asyncio.sleep(0.5)


async def _open_all_expanders(page: Page) -> int:
    """Oeffnet alle geschlossenen Expander auf der Seite. Gibt Anzahl zurueck."""
    closed = page.locator('[data-testid="stExpander"] summary[aria-expanded="false"]')
    count = await closed.count()
    for _ in range(count):
        try:
            await closed.nth(0).click()
            await asyncio.sleep(0.3)
        except Exception:
            break
    if count > 0:
        log.info("  %d Expander geoeffnet", count)
        await asyncio.sleep(0.5)
    return count


async def _click_expander_by_text(page: Page, text: str) -> bool:
    """Oeffnet einen Expander der den gegebenen Text enthaelt."""
    expanders = page.locator('[data-testid="stExpander"]')
    count = await expanders.count()
    for i in range(count):
        exp = expanders.nth(i)
        summary = exp.locator("summary")
        content = await summary.text_content()
        if content and text.lower() in content.lower():
            aria = await summary.get_attribute("aria-expanded")
            if aria == "false":
                await summary.click()
                await asyncio.sleep(0.5)
                log.info("  Expander '%s' geoeffnet", text)
            return True
    log.debug("  Expander '%s' nicht gefunden", text)
    return False


# ---------------------------------------------------------------------------
# Chat-Captures (granular)
# ---------------------------------------------------------------------------
async def capture_chat_welcome(page: Page, config: CaptureConfig) -> list[Path]:
    """Erfasst die Chat-Willkommensseite."""
    log.info("=== 01 Willkommensseite ===")
    await _navigate_and_prepare(page, config.base_url, config)

    paths = []
    paths.append(await _save(page, config.output_dir / "01_chat_welcome.png", full_page=True))
    return paths


async def capture_chat_input(page: Page, config: CaptureConfig, question: str) -> list[Path]:
    """Erfasst den Chat-Input MIT eingetippter Frage (vor dem Absenden)."""
    log.info("=== 02 Chat-Input mit Frage ===")
    await _navigate_and_prepare(page, config.base_url, config)

    paths = []
    chat_input = page.locator('[data-testid="stChatInput"] textarea')
    try:
        await chat_input.wait_for(state="visible", timeout=config.timeout_ms)
    except Exception:
        log.error("Chat-Input nicht gefunden")
        return paths

    # Frage eintippen (NICHT absenden)
    await chat_input.fill(question)
    await asyncio.sleep(0.5)

    # Chat-Input-Bereich isoliert
    chat_input_container = page.locator('[data-testid="stChatInput"]')
    paths.append(await _save(chat_input_container, config.output_dir / "02_chat_input_filled.png"))

    # Gesamtseite mit eingetippter Frage
    paths.append(await _save(page, config.output_dir / "02_chat_input_full.png", full_page=True))
    return paths


async def _wait_for_complete_response(page: Page) -> None:
    """Wartet bis die KI-Antwort vollstaendig gerendert ist (nach st.rerun)."""
    # Phase 1: Warte auf Status-Widget (zeigt dass Analyse laeuft)
    log.info("  Warte auf Analyse-Start...")
    try:
        await page.wait_for_selector(
            '[data-testid="stStatusWidget"]',
            state="visible",
            timeout=15000,
        )
        log.info("  Analyse laeuft...")
    except Exception:
        log.debug("  Status-Widget nicht erschienen (evtl. sehr schnelle Antwort)")

    # Phase 2: Warte bis Status-Widget verschwindet (Analyse abgeschlossen)
    log.info("  Warte auf Analyse-Abschluss (bis zu 10 Min)...")
    try:
        await page.wait_for_function(
            """() => {
                const s = document.querySelector('[data-testid="stStatusWidget"]');
                return !s || s.offsetParent === null;
            }""",
            timeout=600000,  # 10 Minuten fuer komplexe Multi-Step-Analysen
        )
        log.info("  Analyse abgeschlossen")
    except Exception:
        log.warning("  Timeout beim Warten auf Analyse-Abschluss")

    # Phase 3: Streamlit macht nach Abschluss ein st.rerun() -- die Seite
    # laedt komplett neu und rendert aus der Chat-History. Warte darauf.
    log.info("  Warte auf Rerun-Rendering (45s)...")
    await asyncio.sleep(45)

    # Phase 4: Warte bis Seite stabil ist (kein Spinner, kein Laden)
    try:
        await page.wait_for_load_state("networkidle")
        await page.wait_for_function(
            """() => {
                const s = document.querySelector('[data-testid="stStatusWidget"]');
                return !s || s.offsetParent === null;
            }""",
            timeout=30000,
        )
    except Exception:
        pass

    # Phase 5: Pruefe ob Chat-Nachrichten vorhanden sind
    try:
        await page.wait_for_function(
            """() => {
                const msgs = document.querySelectorAll('[data-testid="stChatMessage"]');
                return msgs.length >= 2;
            }""",
            timeout=15000,
        )
        msg_count = await page.locator('[data-testid="stChatMessage"]').count()
        log.info("  %d Chat-Nachrichten sichtbar", msg_count)
    except Exception:
        log.warning("  Weniger als 2 Chat-Nachrichten sichtbar")

    await asyncio.sleep(1)


async def capture_chat_response(page: Page, config: CaptureConfig, question: str) -> list[Path]:
    """Stellt die Frage, wartet auf vollstaendige Antwort, erfasst jeden UI-Schritt."""
    log.info("=== 03 Chat-Antwort (Frage stellen und warten) ===")
    await _navigate_and_prepare(page, config.base_url, config)

    paths = []

    # Frage absenden
    chat_input = page.locator('[data-testid="stChatInput"] textarea')
    try:
        await chat_input.wait_for(state="visible", timeout=config.timeout_ms)
    except Exception:
        log.error("Chat-Input nicht gefunden")
        return paths

    await chat_input.fill(question)
    await chat_input.press("Enter")
    log.info("Frage abgeschickt...")

    # Warte auf vollstaendige Antwort (inkl. st.rerun)
    await _wait_for_complete_response(page)
    await _inject_cleanup_css(page)
    await asyncio.sleep(1)

    # --- Gesamtansicht (alle Expander geschlossen) ---
    log.info("  Erfasse Gesamtansicht (Expander geschlossen)")
    paths.append(await _save(
        page, config.output_dir / "03_chat_response_collapsed.png", full_page=True,
    ))

    # --- User-Nachricht isoliert ---
    user_msgs = page.locator('[data-testid="stChatMessage"]').first
    try:
        paths.append(await _save(user_msgs, config.output_dir / "03_user_message.png"))
        log.info("  User-Nachricht erfasst")
    except Exception:
        log.warning("  User-Nachricht konnte nicht isoliert erfasst werden")

    # --- Assistant-Nachricht isoliert ---
    assistant_msgs = page.locator('[data-testid="stChatMessage"]')
    assistant_count = await assistant_msgs.count()
    if assistant_count >= 2:
        assistant = assistant_msgs.nth(1)
        try:
            paths.append(await _save(assistant, config.output_dir / "03_assistant_message.png"))
            log.info("  Assistant-Nachricht erfasst")
        except Exception:
            log.warning("  Assistant-Nachricht konnte nicht isoliert erfasst werden")

    # --- Einzelne Expander oeffnen und erfassen ---
    # Analyseplan
    if await _click_expander_by_text(page, "Analyseplan"):
        await asyncio.sleep(0.3)
        paths.append(await _save(
            page, config.output_dir / "04_analyseplan.png", full_page=True,
        ))

    # Alle Expander der Reihe nach einzeln oeffnen und jeweils capturen
    expanders = page.locator('[data-testid="stExpander"]')
    expander_count = await expanders.count()
    log.info("  %d Expander auf der Seite gefunden", expander_count)

    for idx in range(expander_count):
        exp = expanders.nth(idx)
        summary = exp.locator("summary")
        label = (await summary.text_content() or "").strip()
        safe_label = "".join(c if c.isalnum() or c in "_ -" else "_" for c in label)[:40]

        # Expander oeffnen falls geschlossen
        aria = await summary.get_attribute("aria-expanded")
        if aria == "false":
            try:
                await summary.click()
                await asyncio.sleep(0.5)
            except Exception:
                log.warning("  Expander %d konnte nicht geoeffnet werden", idx)
                continue

        # Expander-Inhalt erfassen
        fname = f"05_expander_{idx:02d}_{safe_label}.png"
        try:
            paths.append(await _save(exp, config.output_dir / fname))
            log.info("  Expander %d erfasst: '%s'", idx, label)
        except Exception:
            log.warning("  Expander %d konnte nicht erfasst werden: '%s'", idx, label)

    # --- Alle Expander offen: Gesamtansicht ---
    await _open_all_expanders(page)
    await asyncio.sleep(0.5)
    paths.append(await _save(
        page, config.output_dir / "06_chat_response_all_open.png", full_page=True,
    ))

    # --- Metriken-Bar (unterhalb der Antwort) ---
    metrics_bars = page.locator(".metrics-bar")
    if await metrics_bars.count() > 0:
        try:
            paths.append(await _save(
                metrics_bars.last, config.output_dir / "07_metrics_bar.png",
            ))
            log.info("  Metriken-Bar erfasst")
        except Exception:
            log.warning("  Metriken-Bar konnte nicht erfasst werden")

    return paths


async def capture_sidebar(page: Page, config: CaptureConfig) -> list[Path]:
    """Erfasst die Sidebar mit Modell-Auswahl und Session-Kosten."""
    log.info("=== 08 Sidebar ===")
    # Sidebar sollte bereits sichtbar sein nach dem Chat-Capture
    paths = []
    sidebar = page.locator('[data-testid="stSidebar"]')
    try:
        await sidebar.wait_for(state="visible", timeout=config.timeout_ms)
        paths.append(await _save(sidebar, config.output_dir / "08_sidebar.png"))
        log.info("  Sidebar erfasst")
    except Exception:
        log.warning("  Sidebar nicht gefunden")
    return paths


async def capture_map(page: Page, config: CaptureConfig) -> list[Path]:
    """Erfasst die Kartenansicht mit PyDeck-Visualisierung."""
    log.info("=== 09 Kartenansicht ===")
    await _navigate_and_prepare(page, f"{config.base_url}/show_map_view", config)
    paths = []

    # Warte auf PyDeck-Chart
    try:
        await page.wait_for_selector(
            '[data-testid="stDeckGlJsonChart"], [data-testid="stPydeckChart"]',
            timeout=config.timeout_ms,
        )
        log.info("  PyDeck-Chart gefunden, warte auf Tile-Rendering...")
        await asyncio.sleep(5)
    except Exception:
        log.warning("  Kein PyDeck-Chart gefunden (evtl. keine GeoJSON-Daten)")

    # Gesamtseite
    paths.append(await _save(page, config.output_dir / "09_map_full.png", full_page=True))

    # Nur der Karten-Bereich (ohne Sidebar)
    main_content = page.locator('[data-testid="stAppViewBlockContainer"]')
    try:
        paths.append(await _save(main_content, config.output_dir / "09_map_content.png"))
    except Exception:
        log.debug("  Karten-Content konnte nicht isoliert erfasst werden")

    return paths


async def capture_comparison(page: Page, config: CaptureConfig) -> list[Path]:
    """Erfasst das Modellvergleich-Dashboard."""
    log.info("=== 10 Modellvergleich-Dashboard ===")
    await _navigate_and_prepare(
        page, f"{config.base_url}/show_comparison_dashboard", config,
    )
    paths = []

    # Gesamtseite
    paths.append(await _save(
        page, config.output_dir / "10_comparison_dashboard.png", full_page=True,
    ))

    # Alle Expander oeffnen
    await _open_all_expanders(page)
    await asyncio.sleep(0.5)
    paths.append(await _save(
        page, config.output_dir / "10_comparison_expanded.png", full_page=True,
    ))

    return paths


# ---------------------------------------------------------------------------
# Browser-Setup und Hauptlogik
# ---------------------------------------------------------------------------
async def _check_app_reachable(page: Page, url: str, timeout: int) -> bool:
    """Prueft ob die Streamlit-App erreichbar ist."""
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if response and response.status < 400:
            return True
        log.error("App antwortet mit Status %s", response.status if response else "None")
        return False
    except Exception as e:
        log.error("App nicht erreichbar unter %s: %s", url, e)
        return False


async def main(config: CaptureConfig, pages: list[str], question: str) -> None:
    """Hauptfunktion: Erstellt Browser-Context und erfasst granulare UI-Screenshots."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "Starte Captures: viewport=%dx%d, scale=%d, output=%s",
        config.viewport_width, config.viewport_height,
        config.device_scale_factor, config.output_dir,
    )
    log.info("Frage: %s", question[:80])

    async with async_playwright() as p:
        log.info("Starte Chromium (headless)...")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={
                "width": config.viewport_width,
                "height": config.viewport_height,
            },
            device_scale_factor=config.device_scale_factor,
            color_scheme="dark",
        )
        page = await context.new_page()

        # Erreichbarkeit pruefen
        if not await _check_app_reachable(page, config.base_url, config.timeout_ms):
            log.error(
                "Streamlit-App nicht erreichbar. "
                "Bitte starten mit: streamlit run app/main.py"
            )
            await browser.close()
            sys.exit(1)

        content = await page.content()
        if "Datenimport" in content:
            log.warning(
                "Neo4j-Datenbank scheint leer zu sein (Import-Seite). "
                "Einige Captures koennten unvollstaendig sein."
            )

        all_paths: list[Path] = []

        for page_name in pages:
            try:
                if page_name == "chat":
                    # 1. Willkommen
                    all_paths.extend(await capture_chat_welcome(page, config))
                    # 2. Input mit Frage (vor Absenden)
                    all_paths.extend(await capture_chat_input(page, config, question))
                    # 3. Antwort mit allen Schritten
                    all_paths.extend(await capture_chat_response(page, config, question))
                    # 4. Sidebar (nach Chat, Session-Metriken sichtbar)
                    all_paths.extend(await capture_sidebar(page, config))
                elif page_name == "map":
                    all_paths.extend(await capture_map(page, config))
                elif page_name == "comparison":
                    all_paths.extend(await capture_comparison(page, config))
                else:
                    log.warning("Unbekannte Seite: '%s'", page_name)
            except Exception as e:
                log.error("Fehler bei Capture '%s': %s", page_name, e, exc_info=True)

        await browser.close()

    log.info("")
    log.info("=== Fertig: %d Screenshots erstellt ===", len(all_paths))
    for p in all_paths:
        log.info("  %s", p)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hochaufloesende UI-Screenshots fuer Thesis-Abbildungen",
    )
    parser.add_argument(
        "--url", default="http://localhost:8501",
        help="Streamlit-App URL (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir", default="data/thesis/figures",
        help="Ausgabeverzeichnis (default: %(default)s)",
    )
    parser.add_argument(
        "--scale", type=int, default=3,
        help="Device Scale Factor (default: %(default)s = ~300 DPI bei A4)",
    )
    parser.add_argument(
        "--width", type=int, default=1920,
        help="Viewport-Breite (default: %(default)s)",
    )
    parser.add_argument(
        "--height", type=int, default=1080,
        help="Viewport-Hoehe (default: %(default)s)",
    )
    parser.add_argument(
        "--pages", nargs="+", default=["chat", "map", "comparison"],
        choices=["chat", "map", "comparison"],
        help="Zu erfassende Seiten (default: alle)",
    )
    parser.add_argument(
        "--question", default=DEFAULT_QUESTION,
        help="Frage fuer den Chat (default: Autokorrelations-Frage)",
    )
    parser.add_argument(
        "--wait", type=int, default=3000,
        help="Extra-Wartezeit in ms nach Laden (default: %(default)s)",
    )
    parser.add_argument(
        "--no-full-page", action="store_true",
        help="Nur Viewport statt volle Seite",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    cfg = CaptureConfig(
        base_url=args.url,
        output_dir=Path(args.output_dir),
        viewport_width=args.width,
        viewport_height=args.height,
        device_scale_factor=args.scale,
        wait_after_load_ms=args.wait,
        full_page=not args.no_full_page,
    )
    asyncio.run(main(cfg, args.pages, args.question))
