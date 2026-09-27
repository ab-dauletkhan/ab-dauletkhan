import os
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from fetch import parse_time, request

API = os.environ.get("CI_API_V4_URL", "https://gitlab.astanahub.com/api/v4")


def call(path, params):
    return request(f"{API}{path}?{urllib.parse.urlencode(params)}", {"PRIVATE-TOKEN": os.environ["STATS_TOKEN"]})


def get(path, **params):
    return call(path, params)[0]


def pages(path, **params):
    page = "1"
    while page:
        data, headers = call(path, {"per_page": 100, **params, "page": page})
        yield from data
        page = headers.get("X-Next-Page")


def event_kind(event):
    action, target = event["action_name"], event.get("target_type")
    if target == "MergeRequest" and action in ("opened", "accepted"):
        return "mr"
    if action in ("approved", "commented on"):
        return "review"
    if action == "pushed new" and (event.get("push_data") or {}).get("ref_type") == "tag":
        return "release"
    return None


def project_commits(pid, emails):
    found = {}
    try:
        for email in emails:
            for c in pages(f"/projects/{pid}/repository/commits", all="true", author=email):
                if len(c["parent_ids"]) == 1 and c["author_email"].lower() in emails:
                    found[c["id"]] = c
    except urllib.error.HTTPError as err:
        if err.code not in (403, 404):
            raise
    return list(found.values())


def file_changes(pid, sha):
    files = []
    for d in pages(f"/projects/{pid}/repository/commits/{sha}/diff"):
        lines = d["diff"].splitlines()
        files.append([d["new_path"], sum(l.startswith("+") for l in lines), sum(l.startswith("-") for l in lines)])
    return files


def collect(cache):
    me = get("/user")
    emails = {e.lower() for e in (me.get("email"), me.get("commit_email"), me.get("public_email")) if e}
    emails |= {e["email"].lower() for e in pages("/user/emails") if e.get("confirmed_at")}
    projects = {p["id"]: p for p in pages("/projects", membership="true")}
    events = list(pages(f"/users/{me['id']}/events"))
    for pid in {e["project_id"] for e in events if e.get("project_id")} - projects.keys():
        try:
            projects[pid] = get(f"/projects/{pid}")
        except urllib.error.HTTPError:
            pass
    profile = f"{me['username']}/{me['username']}"
    projects = {pid: p for pid, p in projects.items() if p["path_with_namespace"] != profile}
    events = [e for e in events if e.get("project_id") in projects]

    with ThreadPoolExecutor(8) as pool:
        found = dict(zip(projects, pool.map(lambda pid: project_commits(pid, emails), projects)))
    commits = {}
    for pid in sorted(found):
        for c in found[pid]:
            commits.setdefault((parse_time(c["authored_date"]), c["title"]), {**c, "project_id": pid})
    commits = list(commits.values())

    missing = [c for c in commits if c["id"] not in cache]
    with ThreadPoolExecutor(8) as pool:
        for c, files in zip(missing, pool.map(lambda c: file_changes(c["project_id"], c["id"]), missing)):
            cache[c["id"]] = files

    acts = [(parse_time(c["authored_date"]), "commit", c["project_id"]) for c in commits]
    acts += [(parse_time(e["created_at"]), kind, e["project_id"]) for e in events if (kind := event_kind(e))]
    return {
        "name": me["name"],
        "host": urllib.parse.urlparse(API).hostname,
        "terms": ("merge requests", "MRs"),
        "repos": {pid: {"name": p["path"], "public": p.get("visibility") == "public"} for pid, p in projects.items()},
        "acts": acts,
        "commits": [{"id": c["id"], "files": cache[c["id"]]} for c in commits],
        "opened": sum(1 for e in events if e.get("target_type") == "MergeRequest" and e["action_name"] == "opened"),
        "events": len(events),
        "events_since": min((parse_time(e["created_at"]) for e in events), default=None),
    }
