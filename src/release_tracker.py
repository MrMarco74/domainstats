import os
import re
import json
import subprocess

# Paths
# Sibling checkout of the yads repo used to derive release history from its
# git log. No hardcoded personal path — set YADS_REPO_PATH to enable this,
# e.g. "YADS_REPO_PATH=/path/to/yads".
YADS_REPO = os.getenv("YADS_REPO_PATH", "")
RELEASES_JSON = os.path.join(os.path.dirname(YADS_REPO), "yads", "releases", "version.json")
OUTPUT_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "releases.json")


def _parse_release_commits() -> list:
    """
    Real release history lives in git history, not a static file — every version
    bump touches releases/version.json, so its commit log IS the changelog.
    (There used to be a version_de.json with a hand-maintained history, but that
    file no longer exists; git log is now the single source of truth.)
    """
    log = subprocess.run(
        ["git", "log", "--follow", "--format=%ad|%s", "--date=short", "--", "releases/version.json"],
        cwd=YADS_REPO, capture_output=True, text=True, check=True,
    ).stdout

    seen = set()
    entries = []
    for line in log.splitlines():
        if "|" not in line:
            continue
        date, msg = line.split("|", 1)

        m = re.match(r"Release v([\d.]+):\s*(.*)", msg)
        if m:
            version, title = m.group(1), m.group(2).strip()
        else:
            m2 = re.search(r"\(v([\d.]+)\)", msg)
            m3 = re.search(r"v([\d.]+)", msg)
            if m2:
                version = m2.group(1)
                title = re.sub(r"\s*\(v[\d.]+\)", "", msg).strip()
            elif m3:
                version = m3.group(1)
                title = msg.strip()
            else:
                continue

        if version in seen:
            continue
        seen.add(version)
        entries.append({"date": date, "version": f"v{version}", "title": title})

    entries.sort(key=lambda e: tuple(int(p) for p in e["version"].lstrip("v").split(".")), reverse=True)
    return entries


def track_releases():
    if not YADS_REPO or not os.path.isdir(os.path.join(YADS_REPO, ".git")):
        print(f"Error: YADS_REPO_PATH is not set to a valid git repo (got: {YADS_REPO!r}).")
        return

    releases_history = _parse_release_commits()

    # yads-security.de and .com are the same product (DE/EN of the same site) —
    # the app's "combined" domain view assumes both lists are identical.
    output = {
        "yads-security.de": releases_history,
        "yads-security.com": releases_history,
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(output, f, indent=4, ensure_ascii=False)

    print(f"Successfully updated {OUTPUT_JSON} with {len(releases_history)} releases.")


if __name__ == "__main__":
    track_releases()
