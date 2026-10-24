"""Build the portable review artifact from the same prototype sources."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "prototype"


def build():
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="theme.js"></script>',
                        '<script>' + (SOURCE / 'theme.js').read_text(encoding='utf-8') + '</script>')
    html = html.replace('<link rel="stylesheet" href="palette.css">',
                        '<style>' + (SOURCE / 'palette.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<link rel="stylesheet" href="style.css">',
                        '<style>' + (SOURCE / 'style.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<script src="app.js"></script>',
                        '<script>' + (SOURCE / 'app.js').read_text(encoding='utf-8') + '</script>')
    (SOURCE / "preview.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build()
