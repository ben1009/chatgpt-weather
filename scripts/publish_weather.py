#!/usr/bin/env python3
"""Publish a result only while the branch still contains the captured request."""

import base64
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repository: str, branch: str, token: str):
        self.repository = repository
        self.branch = branch
        self.token = token
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.graphql_url = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

    def request(self, path: str, body: dict | None = None, *, graphql: bool = False) -> dict:
        request = urllib.request.Request(
            self.graphql_url if graphql else f"{self.api_url}/{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "chatgpt-weather/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and body is None and "/contents/" in path:
                return {}
            raise GitHubError(f"GitHub API returned HTTP {error.code}.") from None
        except (OSError, http.client.HTTPException):
            # Reads can fail after the mutation has already been applied.
            # Keep these failures on the same reconciliation path as timeouts.
            raise GitHubError("The GitHub API connection was interrupted.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GitHubError("GitHub returned an invalid JSON response.") from None
        if not isinstance(payload, dict) or payload.get("errors"):
            raise GitHubError("GitHub rejected the branch update.")
        return payload

    def branch_head(self) -> str:
        branch = urllib.parse.quote(self.branch, safe="")
        return self.request(f"repos/{self.repository}/git/ref/heads/{branch}")["object"]["sha"]

    def read_file(self, path: str, head: str) -> str | None:
        query = urllib.parse.urlencode({"ref": head})
        payload = self.request(f"repos/{self.repository}/contents/{path}?{query}")
        if not payload:
            return None
        return base64.b64decode(payload["content"]).decode("utf-8")

    def create_commit(self, expected_head: str, result_text: str) -> None:
        payload = self.request(
            "",
            {
                "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
                "variables": {
                    "input": {
                        "branch": {
                            "repositoryNameWithOwner": self.repository,
                            "branchName": self.branch,
                        },
                        "expectedHeadOid": expected_head,
                        "message": {"headline": "chore: update weather forecast"},
                        "fileChanges": {
                            "additions": [{
                                "path": "weather/weather.json",
                                "contents": base64.b64encode(result_text.encode("utf-8")).decode("ascii"),
                            }],
                        },
                    },
                },
            },
            graphql=True,
        )
        data = payload.get("data")
        mutation = data.get("createCommitOnBranch") if isinstance(data, dict) else None
        commit = mutation.get("commit") if isinstance(mutation, dict) else None
        oid = commit.get("oid") if isinstance(commit, dict) else None
        if not isinstance(oid, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", oid):
            raise GitHubError("GitHub did not acknowledge the result commit.")


def publish_weather(client, captured_trigger: dict, result_text: str, attempts: int = 3) -> bool:
    if attempts < 1:
        raise ValueError("Publishing requires at least one commit attempt.")
    result = json.loads(result_text)
    if (
        not isinstance(result, dict)
        or not isinstance(captured_trigger.get("request_id"), str)
        or result.get("request_id") != captured_trigger["request_id"]
    ):
        raise ValueError("The result does not match the captured request.")

    last_error = None
    for attempt in range(attempts + 1):
        head = client.branch_head()
        current_trigger = client.read_file("weather/trigger.json", head)
        if current_trigger is None:
            raise GitHubError("The branch no longer contains weather/trigger.json.")
        if json.loads(current_trigger) != captured_trigger:
            return False
        if client.read_file("weather/weather.json", head) == result_text:
            return True
        if attempt == attempts:
            break
        try:
            client.create_commit(head, result_text)
            return True
        except GitHubError as error:
            # Reconcile at a fresh head even after the final failure: GitHub
            # may have committed successfully before its response was lost.
            last_error = error
    raise GitHubError(f"Could not publish after {attempts} branch update attempts.") from last_error


def main() -> None:
    if os.environ.get("GITHUB_REF_TYPE") != "branch":
        raise ValueError("Weather results can only be published to a branch.")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("The publishing step requires GH_TOKEN.")
    trigger = json.loads((ROOT / "weather/trigger.json").read_text(encoding="utf-8"))
    result_text = (ROOT / "weather/weather.json").read_text(encoding="utf-8")
    client = GitHubClient(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF_NAME"], token)
    if publish_weather(client, trigger, result_text):
        print("Weather result is published for the current request.")
    else:
        print("A newer request replaced this one; skipped its result.")


if __name__ == "__main__":
    try:
        main()
    except (GitHubError, ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
