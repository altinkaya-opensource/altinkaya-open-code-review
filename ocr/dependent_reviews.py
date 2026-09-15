"""Refresh explicitly linked reviews through GitHub workflow_dispatch."""

import json

from linked_prs import ORGANIZATION, can_share, references

PR_FIELDS = "number body author{login}"
REPO_FIELDS = "nameWithOwner databaseId isPrivate visibility isArchived defaultBranchRef{name}"


def query(request, text, variables):
    result = request("/graphql", {"query": text, "variables": variables})
    if result.get("errors") or not result.get("data"):
        raise RuntimeError("Dependent PR discovery failed")
    return result["data"]


def open_prs(request):
    """Read the organization's open PR declarations without search-index delay."""
    result, cursor = [], None
    while True:
        text = ("query($cursor:String){organization(login:" + json.dumps(ORGANIZATION) + ")"
                "{repositories(first:100,after:$cursor){nodes{" + REPO_FIELDS +
                " pullRequests(first:50,states:OPEN){nodes{" + PR_FIELDS +
                "}pageInfo{hasNextPage endCursor}}}pageInfo{hasNextPage endCursor}}}}")
        connection = query(request, text, {"cursor": cursor})["organization"]["repositories"]
        for repo in connection["nodes"]:
            if repo["isArchived"] or not repo["defaultBranchRef"]:
                continue
            pulls = repo["pullRequests"]
            while True:
                result.extend((repo, pr) for pr in pulls["nodes"])
                if not pulls["pageInfo"]["hasNextPage"]:
                    break
                text = ("query($name:String!,$cursor:String){repository(owner:" + json.dumps(ORGANIZATION) +
                        ",name:$name){pullRequests(first:100,states:OPEN,after:$cursor){nodes{" + PR_FIELDS +
                        "}pageInfo{hasNextPage endCursor}}}}")
                pulls = query(request, text, {"name": repo["nameWithOwner"].split("/", 1)[1],
                                             "cursor": pulls["pageInfo"]["endCursor"]})["repository"]["pullRequests"]
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


def dispatch(repository, number, branch, request):
    """Queue a fresh event so current PR metadata and the latest action are used."""
    path = f"/repos/{repository}/actions/workflows/automatic-ocr-review.yml"
    try:
        workflow = request(path)
    except RuntimeError as error:
        if getattr(error, "status", None) == 404:
            return False
        raise
    if workflow.get("state") != "active":
        return False
    request(path + "/dispatches",
            {"ref": branch, "inputs": {"pull_request_number": str(number)}})
    return True


def refresh(repository, number, source_pr, request):
    """Called only for actual PR events, never for dispatched runs, to prevent cycles."""
    source = source_pr["base"]["repo"]
    count = 0
    for repo, pr in open_prs(request):
        name = repo["nameWithOwner"]
        if (name.casefold(), pr["number"]) == (repository.casefold(), number):
            continue
        if (repository.casefold(), number) not in references(pr.get("body"), name):
            continue
        target = {"id": repo["databaseId"], "full_name": name, "private": repo["isPrivate"],
                  "visibility": repo.get("visibility", "").lower()}
        if not can_share(source, target, (pr.get("author") or {}).get("login"), request):
            continue
        try:
            queued = dispatch(name, pr["number"], repo["defaultBranchRef"]["name"], request)
        except RuntimeError:
            # A public source run must not reveal the identity of a private dependent.
            raise RuntimeError("A dependent OCR review could not be queued") from None
        count += int(queued)
    return count
