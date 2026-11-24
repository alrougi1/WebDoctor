"""SEO & accessibility fundamentals checks from HTML: title, meta, lang, headings, alt text, and form/survey inputs."""
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from typing import List
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity


def run_seo_a11y_check(url: str, html: str) -> CheckResult:
    issues: List[Issue] = []
    soup = BeautifulSoup(html, "html.parser")

    # 1. Title
    title_el = soup.find("title")
    title_text = title_el.get_text().strip() if title_el else ""
    if not title_text:
        issues.append(Issue(
            id="SEO_NO_TITLE",
            category="seo_a11y",
            severity=Severity.ERROR,
            title="Missing or Empty <title> Tag",
            summary="The page has no <title> element in the HTML head.",
            detail="<title> is absent or contains only whitespace.",
            how_it_happened="Search engines use the title tag as the primary headline in search result snippets, and browsers display it in tabs. Without it, search engines may pick a suboptimal headline and click-through rates will drop.",
            reproduction=f"curl -s '{url}' | grep -o '<title>[^<]*</title>'",
            fix="Add a unique, descriptive `<title>` tag (50–60 characters) inside the `<head>` of this page.",
        ))
    elif len(title_text) < 10:
        issues.append(Issue(
            id="SEO_SHORT_TITLE",
            category="seo_a11y",
            severity=Severity.WARNING,
            title=f"Very Short <title> Tag ({len(title_text)} characters)",
            summary=f"Title is '{title_text[:60]}', which is unusually short.",
            detail=f"Length: {len(title_text)} characters (recommended: 50–60).",
            how_it_happened="Short titles waste valuable keyword real-estate in search results and may not adequately identify the page.",
            reproduction=f"curl -s '{url}' | grep -o '<title>[^<]*</title>'",
            fix="Expand the title to clearly name the page topic plus the brand name, around 50–60 characters.",
        ))
    elif len(title_text) > 70:
        issues.append(Issue(
            id="SEO_LONG_TITLE",
            category="seo_a11y",
            severity=Severity.INFO,
            title=f"<title> Tag May Be Truncated in SERPs ({len(title_text)} characters)",
            summary=f"Title exceeds 70 characters and will likely be cut off by search engines.",
            detail=f"Length: {len(title_text)} chars: '{title_text[:80]}...'",
            how_it_happened="Google truncates titles around 600 px (roughly 50–60 characters), so the end of your title may not appear in search results.",
            reproduction=f"curl -s '{url}' | grep -o '<title>[^<]*</title>'",
            fix="Shorten the title to 50–60 characters, keeping the most important keywords near the beginning.",
        ))

    # 2. Meta description
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    desc_text = (meta_desc.get("content") or "").strip() if meta_desc else ""
    if not desc_text:
        issues.append(Issue(
            id="SEO_NO_META_DESCRIPTION",
            category="seo_a11y",
            severity=Severity.WARNING,
            title="Missing Meta Description Tag",
            summary="There is no <meta name='description'> tag for search snippets.",
            detail="Search engines will auto-generate snippets from body text, which is often less relevant.",
            how_it_happened="Without a crafted meta description, the snippet under your title in search results is chosen automatically, usually reducing click-through rates.",
            reproduction=f"curl -s '{url}' | grep -iE 'meta[^>]*description'",
            fix="Add `<meta name='description' content='...'>` (120–160 characters) summarizing what a visitor gets from this page.",
        ))
    elif len(desc_text) < 50 or len(desc_text) > 165:
        issues.append(Issue(
            id="SEO_SUBOPTIMAL_DESCRIPTION",
            category="seo_a11y",
            severity=Severity.INFO,
            title=f"Sub-optimal Meta Description Length ({len(desc_text)} characters)",
            summary="Meta descriptions perform best between 120 and 160 characters.",
            detail=f"Current length: {len(desc_text)} chars.",
            how_it_happened="Too-short descriptions miss the chance to persuade searchers; overly long descriptions get truncated with an ellipsis in SERPs.",
            reproduction=f"curl -s '{url}' | grep -iE 'meta[^>]*description'",
            fix="Adjust the meta description length to 120–160 characters including a call to action.",
        ))

    # 3. Viewport meta tag (mobile-friendly)
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if not viewport:
        issues.append(Issue(
            id="A11Y_NO_VIEWPORT",
            category="seo_a11y",
            severity=Severity.ERROR,
            title="Missing Viewport Meta Tag (Not Mobile Optimized)",
            summary="Mobile browsers will render the page at desktop width and zoom out.",
            detail="`<meta name='viewport' content='width=device-width, initial-scale=1'>` is missing.",
            how_it_happened="Without the viewport tag, mobile devices emulate a ~980px desktop viewport, causing tiny text and horizontal scrolling. This also fails Google's mobile-friendly test, which directly hurts search ranking.",
            reproduction=f"curl -s '{url}' | grep -i 'name=.viewport'",
            fix="Add `<meta name='viewport' content='width=device-width, initial-scale=1'>` inside `<head>`.",
        ))

    # 4. html lang attribute
    html_el = soup.find("html")
    lang = (html_el.get("lang") or "").strip() if html_el else ""
    if not lang:
        issues.append(Issue(
            id="A11Y_NO_LANG",
            category="seo_a11y",
            severity=Severity.WARNING,
            title="Missing `lang` Attribute on <html> (WCAG 3.1.1)",
            summary="Screen readers cannot determine which language voice to use.",
            detail="The <html> element has no `lang` attribute.",
            how_it_happened="Assistive technology relies on the lang attribute to pick the correct pronunciation rules. Without it, accented text, date formats, and words in other languages are mispronounced or skipped entirely.",
            reproduction=f"curl -s '{url}' | grep -oE '<html[^>]*>'",
            fix="Add the primary language to the `<html>` tag, e.g. `<html lang='en'>`.",
        ))

    # 5. Missing alt attributes on images
    imgs = soup.find_all("img")
    missing_alt = [img for img in imgs if img.get("alt") is None]
    if missing_alt:
        sev = Severity.ERROR if len(missing_alt) >= 5 else Severity.WARNING
        samples = [img.get("src", "inline")[:60] for img in missing_alt[:3]]
        issues.append(Issue(
            id="A11Y_MISSING_IMG_ALT",
            category="seo_a11y",
            severity=sev,
            title=f"{len(missing_alt)} Image(s) Missing `alt` Attribute (WCAG 1.1.1)",
            summary=f"Images lack text alternatives; screen readers will announce their filenames instead.",
            detail=f"{len(missing_alt)} of {len(imgs)} images have no alt attribute. Samples: {', '.join(samples)}",
            how_it_happened="When an `img` tag omits `alt`, screen readers read the image URL, which is meaningless to visually impaired users, and search engines cannot index image content properly.",
            reproduction=f"Check image tags on {url} in DevTools Elements panel.",
            fix="Add descriptive `alt='...'` text to every informative image. For purely decorative images use `alt=''` so assistive tech can safely skip them.",
        ))

    # 6. Heading structure (h1 presence & multiple h1)
    h1s = soup.find_all("h1")
    if not h1s:
        issues.append(Issue(
            id="SEO_NO_H1",
            category="seo_a11y",
            severity=Severity.WARNING,
            title="No <h1> Heading Found",
            summary="The page is missing a primary <h1> heading for the main topic.",
            detail="No <h1> element exists in the HTML body.",
            how_it_happened="Headings create the document outline that both users and crawlers use to grasp page structure. Missing an <h1> confuses screen-reader navigation and weakens topical SEO signals.",
            reproduction=f"curl -s '{url}' | grep -oE '<h1[^>]*>'",
            fix="Add a single <h1> heading that describes the primary topic of this page.",
        ))
    elif len(h1s) > 1:
        issues.append(Issue(
            id="SEO_MULTIPLE_H1",
            category="seo_a11y",
            severity=Severity.INFO,
            title=f"Multiple <h1> Headings ({len(h1s)})",
            summary="A page should ideally have one primary <h1>, with sections organized under h2-h6.",
            detail=f"Found {len(h1s)} <h1> elements: " + "; ".join(h.get_text()[:40].strip() for h in h1s[:4]),
            how_it_happened="Modern search engines tolerate multiple <h1>s, but assistive technology users benefit from a clear single-topic document outline.",
            reproduction=f"Check heading structure on {url}.",
            fix="Convert secondary <h1> headings to <h2> and nest deeper subsections under h3–h6.",
        ))

    # 7. Favicon
    favicon = soup.find("link", attrs={"rel": re.compile(r"icon", re.I)})
    if not favicon:
        issues.append(Issue(
            id="SEO_NO_FAVICON",
            category="seo_a11y",
            severity=Severity.INFO,
            title="Missing Favicon `<link rel='icon'>` Tag",
            summary="Browser tabs and bookmarks will not show a brand icon for this site.",
            detail="No <link rel='icon'> found (a default /favicon.ico may still be served).",
            how_it_happened="Without an explicit favicon link, browsers either make an extra guessing request to `/favicon.ico` or show a generic placeholder, hurting perceived professionalism.",
            reproduction=f"curl -s '{url}' | grep -iE 'rel=.icon'",
            fix="Add `<link rel='icon' type='image/x-icon' href='/favicon.ico'>` inside `<head>`.",
        ))

    # 8. Forms & survey controls (WCAG 1.3.1 / 3.3.2)
    forms = soup.find_all("form")
    labeled_ids = {l.get("for") for l in soup.find_all("label", attrs={"for": True})}
    controls = soup.find_all(["input", "textarea", "select"])
    skip_types = {"hidden", "submit", "reset", "button", "image"}
    meaningful = [c for c in controls if (c.get("type", "text") if c.name == "input" else "") not in skip_types]
    missing_label = []
    for c in meaningful:
        has_for_label = bool(c.get("id") and c.get("id") in labeled_ids)
        has_wrap_label = bool(c.find_parent("label"))
        has_aria = bool(c.get("aria-label") or c.get("aria-labelledby") or c.get("title"))
        if not (has_for_label or has_wrap_label or has_aria):
            missing_label.append(c)
    if missing_label:
        sev = Severity.ERROR if len(missing_label) >= 4 else Severity.WARNING
        samples = [(c.get("name") or c.get("id") or c.name)[:40] for c in missing_label[:3]]
        issues.append(Issue(
            id="A11Y_FORM_MISSING_LABELS",
            category="seo_a11y",
            severity=sev,
            title=f"{len(missing_label)} Form/Survey Input(s) Missing Accessible Labels",
            summary="Input fields in forms or surveys lack associated <label> or aria-label attributes.",
            detail=f"{len(missing_label)} of {len(meaningful)} controls have no accessible name. Samples: {', '.join(samples)}",
            how_it_happened="Screen-reader users cannot tell what a form field is asking for without a programmatically associated label (visible placeholder text disappears when typing and is not announced reliably). This fails WCAG 1.3.1 Info & Relationships and 3.3.2 Labels or Instructions.",
            reproduction=f"Inspect form inputs on {url} in DevTools; verify each has a matching <label for='...'>.",
            fix="Add a `<label for='id'>Field name</label>` paired with each input's `id`, or wrap the input inside `<label>`, or add `aria-label='...'` for icon-only inputs.",
        ))

    metrics = {
        "title_length": len(title_text),
        "has_meta_description": bool(desc_text),
        "description_length": len(desc_text),
        "has_viewport": bool(viewport),
        "html_lang": lang or None,
        "h1_count": len(h1s),
        "image_count": len(imgs),
        "images_missing_alt": len(missing_alt),
        "has_favicon": bool(favicon),
        "form_count": len(forms),
        "form_controls_total": len(meaningful),
        "controls_missing_labels": len(missing_label),
    }

    has_crit = any(i.severity == Severity.CRITICAL for i in issues)
    has_err = any(i.severity == Severity.ERROR for i in issues)
    has_warn = any(i.severity == Severity.WARNING for i in issues)
    status = CheckStatus.FAILED if (has_crit or has_err) else (CheckStatus.WARN if has_warn else CheckStatus.PASSED)

    return CheckResult(
        name="SEO & Accessibility Fundamentals",
        category="seo_a11y",
        status=status,
        issues=issues,
        metrics=metrics,
    )
