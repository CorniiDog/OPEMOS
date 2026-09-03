#!/usr/bin/env python3
"""Validate the repository README and GitHub Pages documentation contract."""

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
README = ROOT / "README.md"
REQUIRED_PAGES = {
    "index.md",
    "getting-started.md",
    "developer-guide.md",
    "image-builder.md",
    "interstitial.md",
    "contracts.md",
    "security.md",
    "technical-reference.md",
}
NAVIGATION_PAGES = {
    "getting-started.md",
    "developer-guide.md",
    "image-builder.md",
    "interstitial.md",
    "contracts.md",
    "security.md",
}
LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
COMMAND_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:bootstrap|lib|tests)/[A-Za-z0-9_.-]+"
    r"(?:/[A-Za-z0-9_.-]+)*\.(?:py|sh))"
)


def relative_luminance(value: str) -> float:
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045
              else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    brighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (brighter + 0.05) / (darker + 0.05)


def css_colors(stylesheet: str, variable: str) -> list[str]:
    return re.findall(rf"--{re.escape(variable)}:\s*(#[0-9a-fA-F]{{6}});", stylesheet)


def front_matter(text: str, path: Path) -> dict[str, str]:
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"missing front matter: {path}"
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError(f"unterminated front matter: {path}") from error
    values = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        assert separator and key.strip(), f"malformed front matter: {path}"
        assert key.strip() not in values, f"duplicate front matter key: {path}"
        values[key.strip()] = value.strip()
    for key in ("layout", "title", "description"):
        assert values.get(key), f"missing {key} front matter: {path}"
    return values


def slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[`*_~]", "", value).strip().lower()
    value = re.sub(r"[^a-z0-9 _-]", "", value)
    return re.sub(r"[ _]+", "-", value)


def anchors(text: str) -> set[str]:
    result = set()
    occurrences: dict[str, int] = {}
    for heading in HEADING.findall(text):
        base = slug(heading)
        count = occurrences.get(base, 0)
        occurrences[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def validate_link(source: Path, destination: str) -> None:
    destination = unquote(destination.strip())
    if (not destination or destination.startswith(("http://", "https://", "mailto:"))
            or "{{" in destination):
        return
    path_text, _, fragment = destination.partition("#")
    target = source if not path_text else (source.parent / path_text).resolve()
    assert target == ROOT or ROOT in target.parents, (
        f"documentation link escapes repository: {source}: {destination}"
    )
    assert target.is_file(), f"broken documentation link: {source}: {destination}"
    if fragment and target.suffix.lower() == ".md":
        target_anchors = anchors(target.read_text(encoding="utf-8"))
        assert fragment in target_anchors, (
            f"broken documentation anchor: {source}: {destination}"
        )


def main() -> None:
    pages = {path.name for path in DOCS.glob("*.md")}
    assert REQUIRED_PAGES <= pages, "required documentation pages are missing"
    assert len(README.read_text(encoding="utf-8").splitlines()) <= 150, (
        "root README must remain a concise project entry point"
    )
    readme = README.read_text(encoding="utf-8")
    assert "[OPEMOS documentation](https://corniidog.github.io/OPEMOS/)" in readme
    assert "**https://corniidog.github.io/" not in readme
    assert ".git opemos" in readme and "cd opemos" in readme
    assert 'src="docs/assets/images/opemos-pill.svg"' in readme
    assert '<h1 align="center">OPEMOS</h1>' in readme
    assert "https://github.com/CorniiDog/OPEMOS.EXE" in readme
    assert "https://corniidog.github.io/OPEMOS.EXE/" in readme
    assert "actions/workflows/shell.yml" in readme
    pill = DOCS / "assets/images/opemos-pill.svg"
    pill_text = pill.read_text(encoding="utf-8")
    assert pill.is_file() and pill.stat().st_size <= 16 * 1024
    assert "<script" not in pill_text.lower() and "href=" not in pill_text.lower()
    assert "linearGradient" in pill_text and "#76b900" in pill_text

    identity_files = [
        README,
        DOCS / "_config.yml",
        *sorted(DOCS.glob("*.md")),
        ROOT / "bootstrap/compile_online.sh",
        ROOT / "bootstrap/online_commit.sh",
        ROOT / "bootstrap/online_dev.sh",
        ROOT / "bootstrap/online_install.sh",
        ROOT / "bootstrap/online_setup_nvidia.sh",
        ROOT / "bootstrap/publish_artifacts.sh",
        ROOT / "bootstrap/setup_nvidia.sh",
        ROOT / "lib/common.sh",
        ROOT / "lib/resolve_target.py",
        ROOT / "lib/validate_publish_inputs.py",
    ]
    for path in identity_files:
        text = path.read_text(encoding="utf-8")
        assert "CorniiDog/open-gpu-kernel-modules-steamos-support" not in text, (
            f"stale pre-rename GitHub repository identity: {path}"
        )
        assert "corniidog.github.io/open-gpu-kernel-modules-steamos-support" not in text, (
            f"stale pre-rename GitHub Pages identity: {path}"
        )

    sources = [README, *sorted(DOCS.glob("*.md"))]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "\x00" not in text and not text.startswith("\ufeff"), (
            f"unsafe documentation encoding: {source}"
        )
        if source != README:
            front_matter(text, source)
        for destination in LINK.findall(text):
            validate_link(source, destination)

    task_pages = [DOCS / name for name in REQUIRED_PAGES if name != "technical-reference.md"]
    for page in [README, *task_pages]:
        text = page.read_text(encoding="utf-8")
        for relative in COMMAND_PATH.findall(text):
            assert (ROOT / relative).exists(), (
                f"documented command path does not exist: {page}: {relative}"
            )

    config = (DOCS / "_config.yml").read_text(encoding="utf-8")
    for page in sorted(NAVIGATION_PAGES):
        assert re.search(rf"^\s+- {re.escape(page)}\s*$", config, re.MULTILINE), (
            f"documentation navigation omits {page}"
        )
    assert "baseurl: /OPEMOS" in config
    assert "repository: CorniiDog/OPEMOS" in config
    assert "title: OPEMOS" in config
    assert "Unofficial community-built" in config

    stylesheet = (DOCS / "assets/main.scss").read_text(encoding="utf-8")
    assert "--brand-gradient:" in stylesheet
    assert ".site-header::before," in stylesheet
    assert ".site-footer::before" in stylesheet
    assert ".opemos-hero" in stylesheet and ".opemos-wordmark" in stylesheet
    assert ".highlighter-rouge .highlight {" in stylesheet
    assert "table th {" in stylesheet
    assert "background-color: var(--code-background) !important;" in stylesheet
    assert "background-color: var(--surface-strong) !important;" in stylesheet
    table_rule = re.search(r"^table \{(?P<body>.*?)^\}", stylesheet, re.MULTILINE | re.DOTALL)
    assert table_rule is not None
    assert "display: table;" in table_rule.group("body")
    assert "display: block;" not in table_rule.group("body")
    assert "border-collapse: separate;" in table_rule.group("body")
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    assert 'class="opemos-hero"' in index
    assert 'class="opemos-wordmark"' in index
    palettes = {
        "page-background": css_colors(stylesheet, "page-background"),
        "text-primary": css_colors(stylesheet, "text-primary"),
        "text-muted": css_colors(stylesheet, "text-muted"),
        "surface-strong": css_colors(stylesheet, "surface-strong"),
        "steam-blue": css_colors(stylesheet, "steam-blue"),
        "nvidia-green-accessible": css_colors(
            stylesheet, "nvidia-green-accessible"
        ),
        "code-background": css_colors(stylesheet, "code-background"),
        "code-text": css_colors(stylesheet, "code-text"),
        "syntax-comment": css_colors(stylesheet, "syntax-comment"),
        "syntax-keyword": css_colors(stylesheet, "syntax-keyword"),
        "syntax-string": css_colors(stylesheet, "syntax-string"),
        "syntax-name": css_colors(stylesheet, "syntax-name"),
        "syntax-error": css_colors(stylesheet, "syntax-error"),
    }
    assert all(len(colors) == 2 for colors in palettes.values()), (
        "light and dark theme colors must use the shared palette"
    )
    for index in range(2):
        background = palettes["page-background"][index]
        for role in (
            "text-primary", "text-muted", "steam-blue", "nvidia-green-accessible"
        ):
            assert contrast_ratio(palettes[role][index], background) >= 4.5, (
                f"{role} lacks WCAG AA contrast in palette {index}"
            )
        code_background = palettes["code-background"][index]
        for role in (
            "code-text", "syntax-comment", "syntax-keyword", "syntax-string",
            "syntax-name", "syntax-error",
        ):
            assert contrast_ratio(palettes[role][index], code_background) >= 4.5, (
                f"{role} lacks WCAG AA code contrast in palette {index}"
            )
        assert contrast_ratio(
            palettes["text-primary"][index], palettes["surface-strong"][index]
        ) >= 4.5, f"table heading lacks WCAG AA contrast in palette {index}"

    footer = (DOCS / "_includes/footer.html").read_text(encoding="utf-8")
    assert "not affiliated with, endorsed by" in footer
    assert "Valve Corporation" in footer and "NVIDIA Corporation" in footer
    assert "https://github.com/CorniiDog/OPEMOS.EXE" in footer

    workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    for action in (
        "actions/configure-pages@v5",
        "actions/jekyll-build-pages@v1",
        "actions/upload-pages-artifact@v3",
        "actions/deploy-pages@v4",
    ):
        assert action in workflow, f"Pages workflow omits {action}"
    assert "pages: write" in workflow and "id-token: write" in workflow


if __name__ == "__main__":
    main()
