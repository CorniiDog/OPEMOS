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
    "contracts.md",
    "security.md",
    "technical-reference.md",
}
NAVIGATION_PAGES = {
    "getting-started.md",
    "developer-guide.md",
    "image-builder.md",
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
    assert "baseurl: /open-gpu-kernel-modules-steamos-support" in config
    assert "title: OPEMOS" in config
    assert "Unofficial community-built" in config

    footer = (DOCS / "_includes/footer.html").read_text(encoding="utf-8")
    assert "not affiliated with, endorsed by" in footer
    assert "Valve Corporation" in footer and "NVIDIA Corporation" in footer

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
