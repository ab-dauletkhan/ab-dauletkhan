import datetime as dt
import os
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from fetch import parse_time, request

API = "https://api.github.com"
REPO = "repository { nameWithOwner name isPrivate owner { login } }"

YEAR_REPOS = f"""
query($from: DateTime!, $to: DateTime!) {{ viewer {{ contributionsCollection(from: $from, to: $to) {{
  commitContributionsByRepository(maxRepositories: 100) {{ {REPO} }}
}} }} }}"""

YEAR_PRS = f"""
query($from: DateTime!, $to: DateTime!, $after: String) {{ viewer {{ contributionsCollection(from: $from, to: $to) {{
  items: pullRequestContributions(first: 100, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ occurredAt pullRequest {{ {REPO} }} }}
  }}
}} }} }}"""

YEAR_REVIEWS = f"""
query($from: DateTime!, $to: DateTime!, $after: String) {{ viewer {{ contributionsCollection(from: $from, to: $to) {{
  items: pullRequestReviewContributions(first: 100, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ occurredAt pullRequestReview {{ {REPO} }} }}
  }}
}} }} }}"""

CALENDAR = """
query($from: DateTime!, $to: DateTime!) { viewer { contributionsCollection(from: $from, to: $to) {
  contributionCalendar { weeks { contributionDays { date contributionCount } } }
} } }"""

HISTORY = """
query($owner: String!, $name: String!, $author: ID!, $after: String) { repository(owner: $owner, name: $name) {
  defaultBranchRef { target { ... on Commit {
    history(first: 100, after: $after, author: { id: $author }) {
      pageInfo { hasNextPage endCursor }
      nodes { oid authoredDate parents { totalCount } }
    }
  } } }
} }"""


def headers():
    return {
        "Authorization": f"Bearer {os.environ['STATS_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def graphql(query, **variables):
    data, _ = request(f"{API}/graphql", headers(), {"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def years(since):
    now = dt.datetime.now(dt.timezone.utc)
    for year in range(since.year, now.year + 1):
        start = max(since, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc) - dt.timedelta(seconds=1))
        yield start.isoformat(), end.isoformat()


def contributions(query, since):
    for start, end in years(since):
        after = None
        while True:
            items = graphql(query, **{"from": start, "to": end, "after": after})["viewer"]["contributionsCollection"]["items"]
            yield from items["nodes"]
            if not items["pageInfo"]["hasNextPage"]:
                break
            after = items["pageInfo"]["endCursor"]


def history(repo, author):
    owner, name = repo.split("/", 1)
    after, out = None, []
    while True:
        ref = graphql(HISTORY, owner=owner, name=name, author=author, after=after)["repository"]["defaultBranchRef"]
        if not ref:
            return out
        page = ref["target"]["history"]
        out += [c for c in page["nodes"] if c["parents"]["totalCount"] == 1]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def file_changes(repo, sha):
    try:
        data, _ = request(f"{API}/repos/{repo}/commits/{sha}", headers())
    except urllib.error.HTTPError as err:
        if err.code in (404, 409, 422):
            return []
        raise
    return [[f["filename"], f.get("additions", 0), f.get("deletions", 0)] for f in data.get("files", [])]


def unseen(since, acts):
    calendar = Counter()
    for start, end in years(since):
        weeks = graphql(CALENDAR, **{"from": start, "to": end})["viewer"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        for day in (d for w in weeks for d in w["contributionDays"]):
            calendar[day["date"]] += day["contributionCount"]
    seen = Counter(t.astimezone(dt.timezone.utc).date().isoformat() for t, _, _ in acts)
    out = []
    for day, count in calendar.items():
        noon = dt.datetime.fromisoformat(day).replace(hour=12, tzinfo=dt.timezone.utc)
        out += [(noon, "private", None)] * max(0, count - seen[day])
    return out


def collect(cache):
    viewer = graphql("query { viewer { id login name createdAt } }")["viewer"]
    since = parse_time(viewer["createdAt"])
    login = viewer["login"]
    profile = f"{login}/{login}"

    repos = {}

    def remember(repo):
        key = repo["nameWithOwner"]
        repos[key] = {"name": repo["name"] if repo["owner"]["login"] == login else key, "public": not repo["isPrivate"]}
        return key

    for start, end in years(since):
        for item in graphql(YEAR_REPOS, **{"from": start, "to": end})["viewer"]["contributionsCollection"]["commitContributionsByRepository"]:
            remember(item["repository"])
    prs = [(parse_time(n["occurredAt"]), remember(n["pullRequest"]["repository"])) for n in contributions(YEAR_PRS, since)]
    reviews = [(parse_time(n["occurredAt"]), remember(n["pullRequestReview"]["repository"])) for n in contributions(YEAR_REVIEWS, since)]
    repos.pop(profile, None)

    with ThreadPoolExecutor(6) as pool:
        found = dict(zip(repos, pool.map(lambda key: history(key, viewer["id"]), repos)))
    commits = {}
    for key in sorted(found):
        for c in found[key]:
            commits.setdefault(c["oid"], {**c, "repo": key})
    commits = list(commits.values())

    missing = [c for c in commits if c["oid"] not in cache]
    with ThreadPoolExecutor(8) as pool:
        for c, files in zip(missing, pool.map(lambda c: file_changes(c["repo"], c["oid"]), missing)):
            cache[c["oid"]] = files

    acts = [(parse_time(c["authoredDate"]), "commit", c["repo"]) for c in commits]
    acts += [(t, "mr", key) for t, key in prs if key in repos]
    acts += [(t, "review", key) for t, key in reviews if key in repos]
    acts += unseen(since, acts)
    return {
        "name": viewer["name"] or login,
        "host": "github.com",
        "terms": ("pull requests", "PRs"),
        "repos": repos,
        "acts": acts,
        "commits": [{"id": c["oid"], "files": cache[c["oid"]]} for c in commits],
        "opened": len(prs),
        "events": len(prs) + len(reviews),
        "events_since": None,
    }
