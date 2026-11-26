"""Programmatic usage: call WebDoctor from Python and process findings."""
from webdoctor import run_diagnostics

report = run_diagnostics(
    "https://example.com",
    browser_mode=True,
    check_links=True,
    ai=True,              # provider default (sandbox: gpt-6-luna); rule-based fallback without credentials
    ai_model="gpt-6-luna",
)

print(f"Grade : {report.overall_grade} ({report.score}/100)")
print(f"Issues: {len(report.issues)}")
print("\nTop findings:")
for it in report.issues[:5]:
    print(f"- [{it.severity.value.upper()}] {it.title}")
    if it.how_it_happened:
        print(f"  how: {it.how_it_happened[:140]}...")
    if it.fix:
        print(f"  fix: {it.fix[:140]}...")

print("\nExecutive diagnosis:")
print(report.ai_summary)
