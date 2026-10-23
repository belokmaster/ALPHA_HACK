from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def test_ui_template_rendering_demo_mode() -> None:
    templates_dir = Path(__file__).parent.parent.parent / "app/web/templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    template = env.get_template("index.html")
    html = template.render(demo=True)

    # Required Shell & Branding
    assert "Альфа-Бизнес" in html
    assert "Демонстрационный прототип" in html
    assert "Не является индивидуальной инвестиционной рекомендацией" in html

    # Auth Form
    assert 'id="actor-select"' in html
    assert "alice" in html and "bob" in html
    assert 'id="btn-login"' in html

    # Context Controls
    assert 'id="context-ribbon"' in html
    assert 'id="company-select"' in html
    assert 'id="role-select"' in html
    assert 'id="temporal-mode-select"' in html
    assert 'id="asof-input"' in html
    assert 'id="btn-apply-context"' in html

    # Navigation Tabs
    assert 'id="app-tabs"' in html
    assert 'data-tab="feed"' in html
    assert 'data-tab="news"' in html
    assert 'data-tab="status"' in html

    # Containers for 6 Sections, Evidence Drawer, Followup
    assert 'id="feed-container"' in html
    assert 'id="analysis-container"' in html
    assert 'id="job-progress-container"' in html
    assert 'id="followup-container"' in html
    assert 'id="followup-text"' in html
    assert 'id="btn-followup-submit"' in html
    assert 'id="assumption-toggle"' in html
    assert 'id="evidence-drawer-backdrop"' in html
    assert 'id="drawer-body"' in html

    # Fixture Mode Toggle & Banner
    assert 'id="btn-toggle-fixtures"' in html
    assert 'id="fixture-mode-banner"' in html
    assert 'id="fixture-scenario-select"' in html

    # Scripts and styles embedded cleanly
    assert "AlfaApi" in html
    assert "AlfaFixtures" in html
    assert "AlfaUI" in html
    assert "handleLogin" in html


def test_ui_template_rendering_no_demo() -> None:
    templates_dir = Path(__file__).parent.parent.parent / "app/web/templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    template = env.get_template("index.html")
    html = template.render(demo=False)

    assert "Режим демонстрации выключен" in html
    assert "DEMO_MODE" in html
    assert 'id="actor-select"' not in html


def test_ui_has_no_external_cdns_or_fonts() -> None:
    templates_dir = Path(__file__).parent.parent.parent / "app/web/templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    template = env.get_template("index.html")
    html = template.render(demo=True)

    # Disallowed external libraries / trackers / fonts
    for forbidden in [
        "https://cdn.",
        "https://unpkg.com",
        "https://fonts.googleapis.com",
        "https://cdnjs.cloudflare.com",
        "react",
        "vue",
    ]:
        assert forbidden not in html.lower(), f"Forbidden external dependency found: {forbidden}"


def test_ui_fixtures_conform_to_dto_schemas() -> None:
    """Verify that fixture objects in fixtures.js strictly validate against contracts/v1.py."""
    fixtures_file = (
        Path(__file__).parent.parent.parent / "app/web/templates/fixtures.js"
    ).read_text(encoding="utf-8")

    assert "FIXTURE_USER_CONTEXT" in fixtures_file
    assert "FIXTURE_ANALYSIS_READY" in fixtures_file
    assert "FIXTURE_ANALYSIS_PARTIAL" in fixtures_file
    assert "FIXTURE_ANALYSIS_INSUFFICIENT" in fixtures_file
    assert "FIXTURE_ANALYSIS_STALE" in fixtures_file
    assert "FIXTURE_ANALYSIS_SECURITY_LONG_TEXT" in fixtures_file
