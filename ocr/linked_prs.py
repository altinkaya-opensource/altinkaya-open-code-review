"""Load bounded, authorized linked-PR evidence without executing related code."""

import hashlib
import json
import re
from urllib.parse import quote

ORGANIZATION = "altinkaya-opensource"
MAX_LINKS = 5
URL_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})/pull/([1-9]\d{0,9})(?=$|[\s/?#<>),\]])")
DECLARATION_RE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?(?:depends[- ]on|related[- ]prs?)[ \t]*:[ \t]*(.+)$", re.I | re.M)
SHORT_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:(?:([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})|([A-Za-z0-9_.-]{1,100})))?#([1-9]\d{0,9})(?!\d)")


def references(body, repository):
    """Accept PR URLs and explicit Depends-On/Related-PR shorthand declarations."""
    result = {(match[1].casefold(), int(match[2])) for match in URL_RE.finditer(body or "")
              if match[1].split("/")[0].casefold() == ORGANIZATION}
    for declaration in DECLARATION_RE.finditer(body or ""):
        result.update((match[1].casefold(), int(match[2])) for match in URL_RE.finditer(declaration[1]))
        shorthand = re.sub(r"https?://\S+", "", declaration[1])
        for match in SHORT_RE.finditer(shorthand):
            repo = match[1] or (ORGANIZATION + "/" + match[2] if match[2] else repository)
            result.add((repo.casefold(), int(match[3])))
    return sorted(result)


def signature(pr):
    """Freeze code and descriptive context; edits invalidate an in-flight review."""
    value = {
        "repository_id": pr["base"]["repo"]["id"], "base": pr["base"]["sha"], "head": pr["head"]["sha"],
        "repository": pr["base"]["repo"].get("full_name", "").casefold(),
        "private": pr["base"]["repo"].get("private"),
        "visibility": pr["base"]["repo"].get("visibility"),
        "state": pr["state"], "merged": bool(pr.get("merged")),
        "metadata_sha": hashlib.sha256((str(pr.get("title", "")) + "\n" + str(pr.get("body") or "")).encode()).hexdigest(),
    }
    if not all(re.fullmatch(r"[a-f0-9]{40}", value[key]) for key in ("base", "head")):
        raise ValueError("Invalid related PR commit identity")
    return value


def can_share(source, target, actor, request):
    """Never send private code to a public PR or to an author lacking source access."""
    source_public = source.get("visibility", "").lower() == "public" if source.get("visibility") else source.get("private") is False
    if source_public:
        return True
    target_public = target.get("visibility", "").lower() == "public" if target.get("visibility") else target.get("private") is not True
    if target_public:
        return False
    if source.get("id") == target.get("id"):
        return True
    if not actor:
        return False
    try:
        access = request(f"/repos/{source['full_name']}/collaborators/{quote(actor, safe='')}/permission")
        return access.get("permission") in {"read", "triage", "write", "maintain", "admin"}
    except RuntimeError:
        return False


def clip(text, limit):
    """Bound prompt bytes, including multibyte UTF-8 text."""
    raw = text.encode()
    return raw[:max(0, limit)].decode("utf-8", errors="ignore"), len(raw) > limit


def load(repository, number, pr, request):
    """Fetch direct related diffs, preserving explicit partial-context warnings."""
    refs = [ref for ref in references(pr.get("body"), repository) if ref != (repository.casefold(), number)]
    warnings = []
    if len(refs) > MAX_LINKS:
        warnings.append(f"Only the first {MAX_LINKS} linked PRs fit the context limit.")
    records, snapshots = [], []
    budget = 44000
    for index, (repo, linked_number) in enumerate(refs[:MAX_LINKS]):
        label = f"{repo}#{linked_number}"
        if repo.split('/')[0] != ORGANIZATION:
            warnings.append(f"Linked PR {label} is outside the allowed organization.")
            continue
        path = f"/repos/{repo}/pulls/{linked_number}"
        try:
            linked = request(path)
            source = linked["base"]["repo"]
            if source["full_name"].casefold() != repo or not can_share(source, pr["base"]["repo"], pr["user"]["login"], request):
                warnings.append(f"Linked PR {label} is unavailable under the access policy.")
                continue
            frozen = signature(linked)
            allowance = max(2000, budget // (len(refs[:MAX_LINKS]) - index))
            diff, fetched, partial = [], 0, False
            for page in range(1, 31):
                files = request(f"{path}/files?per_page=100&page={page}")
                for file in files:
                    fetched += 1
                    patch = file.get("patch")
                    if not isinstance(patch, str):
                        partial = True
                        patch = "[Diff unavailable: binary or omitted patch]"
                    else:
                        added = sum(line.startswith("+") for line in patch.splitlines())
                        removed = sum(line.startswith("-") for line in patch.splitlines())
                        partial |= added != file.get("additions") or removed != file.get("deletions")
                    diff.append(f"File: {file['filename']} ({file['status']})\n{patch}")
                if len(files) < 100 or len("\n\n".join(diff).encode()) > allowance:
                    break
            partial |= fetched != linked.get("changed_files")
            description, shortened = clip(linked.get("body") or "", 2000)
            record = {"pr": label, "url": f"https://github.com/{repo}/pull/{linked_number}",
                      "title": clip(linked.get("title") or "", 500)[0], "description": description,
                      "description_truncated": shortened, **frozen, "diff": "\n\n".join(diff)}
            while len(json.dumps(record, ensure_ascii=False).encode()) > allowance and record["diff"]:
                excess = len(json.dumps(record, ensure_ascii=False).encode()) - allowance
                record["diff"], _ = clip(record["diff"], max(0, len(record["diff"].encode()) - excess - 64))
                partial = True
            if partial:
                warnings.append(f"Linked PR {label} has incomplete diff context.")
                record["diff_truncated"] = True
            if signature(request(path)) != frozen:
                warnings.append(f"Linked PR {label} changed while its context was fetched.")
                continue
            snapshots.append({"repo": repo, "number": linked_number, "snapshot": frozen})
            records.append(record)
            budget -= len(json.dumps(record, ensure_ascii=False).encode())
        except RuntimeError:
            warnings.append(f"Linked PR {label} could not be loaded.")
    target = {"title": clip(pr.get("title") or "", 500)[0], "description": clip(pr.get("body") or "", 4000)[0]}
    text = json.dumps({"target_pr": target, "related_prs": records, "limitations": warnings}, ensure_ascii=False)
    if len(text.encode()) > 56000:
        raise ValueError("Related PR context exceeds the model input bound")
    return {"text": text, "snapshots": snapshots, "warnings": warnings, "complete": not warnings}


def unchanged(context, request):
    """Recheck every related revision before publishing an approval or state transition."""
    return all(signature(request(f"/repos/{ref['repo']}/pulls/{ref['number']}")) == ref['snapshot']
               for ref in context['snapshots'])


def revision(pr, context):
    """Only code/deployment evidence changes may resolve an earlier finding."""
    return {"head": pr["head"]["sha"], "dependencies": [
        {"repo": ref["repo"], "number": ref["number"], "head": ref["snapshot"]["head"],
         "repository_id": ref["snapshot"]["repository_id"], "merged": ref["snapshot"]["merged"]}
        for ref in context["snapshots"]
    ]}


def render(context):
    """Describe linked evidence without exposing skipped private metadata."""
    lines = []
    if context["snapshots"]:
        lines.append("### Related PR context\n")
        for ref in context["snapshots"]:
            snapshot = ref["snapshot"]
            status = "merged" if snapshot["merged"] else snapshot["state"]
            lines.append(f"- [{ref['repo']}#{ref['number']}](https://github.com/{ref['repo']}/pull/{ref['number']}) "
                         f"at `{snapshot['head'][:12]}` ({status})")
    if context["warnings"]:
        lines.append("\n**Related context incomplete — no automatic approval.**")
        lines.extend(f"- {warning}" for warning in context["warnings"])
    return "\n".join(lines)
