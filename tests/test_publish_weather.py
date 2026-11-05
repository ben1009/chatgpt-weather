import base64
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_weather import GitHubClient, GitHubError, publish_weather


class Branch:
    """Immutable snapshots and the same expected-head rule enforced by GitHub."""

    def __init__(self, trigger):
        self.head = "head-0"
        self.snapshots = {self.head: {
            "weather/trigger.json": json.dumps(trigger),
            "weather/weather.json": "old result",
        }}
        self.before_commit = None
        self.commits = []
        self.lose_response = False

    def advance(self, changes):
        files = copy.copy(self.snapshots[self.head])
        files.update(changes)
        self.head = f"head-{len(self.snapshots)}"
        self.snapshots[self.head] = files

    def branch_head(self):
        return self.head

    def read_file(self, path, head):
        return self.snapshots[head].get(path)

    def create_commit(self, expected_head, result_text):
        if self.before_commit:
            callback, self.before_commit = self.before_commit, None
            callback()
        if expected_head != self.head:
            raise GitHubError("Expected head no longer matches")
        self.commits.append(expected_head)
        self.advance({"weather/weather.json": result_text})
        if self.lose_response:
            self.lose_response = False
            raise GitHubError("The successful response was lost")


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.trigger = {"request_id": "A", "latitude": 39.9, "longitude": 116.4}
        self.result = json.dumps({"request_id": "A", "summary": "forecast A"})

    def test_request_change_between_check_and_commit_cannot_publish_old_result(self):
        branch = Branch(self.trigger)
        branch.before_commit = lambda: branch.advance({
            "weather/trigger.json": json.dumps({**self.trigger, "request_id": "B"}),
        })
        self.assertFalse(publish_weather(branch, self.trigger, self.result))
        self.assertEqual(branch.commits, [])
        self.assertEqual(branch.read_file("weather/weather.json", branch.head), "old result")

    def test_unrelated_branch_change_is_preserved_on_retry(self):
        branch = Branch(self.trigger)
        branch.before_commit = lambda: branch.advance({"README.md": "new documentation"})
        self.assertTrue(publish_weather(branch, self.trigger, self.result))
        self.assertEqual(branch.commits, ["head-1"])
        self.assertEqual(branch.read_file("README.md", branch.head), "new documentation")

    def test_reusing_id_with_changed_coordinates_is_still_a_new_request(self):
        branch = Branch(self.trigger)
        branch.before_commit = lambda: branch.advance({
            "weather/trigger.json": json.dumps({**self.trigger, "latitude": 44.5}),
        })
        self.assertFalse(publish_weather(branch, self.trigger, self.result))
        self.assertEqual(branch.commits, [])

    def test_lost_response_does_not_create_duplicate_commit(self):
        branch = Branch(self.trigger)
        branch.lose_response = True
        self.assertTrue(publish_weather(branch, self.trigger, self.result))
        self.assertEqual(len(branch.commits), 1)

    def test_lost_final_response_is_reconciled_as_success(self):
        branch = Branch(self.trigger)
        create_commit = branch.create_commit
        calls = 0

        def race_then_lose_final_response(head, result_text):
            nonlocal calls
            calls += 1
            if calls < 3:
                branch.advance({"README.md": f"concurrent edit {calls}"})
                raise GitHubError("Branch changed before commit")
            branch.lose_response = True
            create_commit(head, result_text)

        branch.create_commit = race_then_lose_final_response
        self.assertTrue(publish_weather(branch, self.trigger, self.result, attempts=3))
        self.assertEqual(calls, 3)
        self.assertEqual(len(branch.commits), 1)

    def test_request_replaced_on_final_attempt_is_skipped(self):
        branch = Branch(self.trigger)
        branch.before_commit = lambda: branch.advance({
            "weather/trigger.json": json.dumps({**self.trigger, "request_id": "B"}),
        })
        self.assertFalse(publish_weather(branch, self.trigger, self.result, attempts=1))
        self.assertEqual(branch.commits, [])

    def test_failed_final_attempt_still_reports_failure(self):
        branch = Branch(self.trigger)
        branch.create_commit = mock.Mock(side_effect=GitHubError("Commit rejected"))
        with self.assertRaises(GitHubError):
            publish_weather(branch, self.trigger, self.result, attempts=3)
        self.assertEqual(branch.create_commit.call_count, 3)

    def test_result_for_another_request_is_rejected(self):
        with self.assertRaises(ValueError):
            publish_weather(Branch(self.trigger), self.trigger, '{"request_id":"B"}')

    def test_real_client_sends_expected_head_in_atomic_mutation(self):
        client = GitHubClient("owner/repo", "main", "test-token")
        with mock.patch.object(client, "request", return_value={}) as request:
            client.create_commit("immutable-head", self.result)
        body = request.call_args.args[1]
        commit = body["variables"]["input"]
        self.assertEqual(commit["expectedHeadOid"], "immutable-head")
        self.assertEqual(commit["branch"]["branchName"], "main")
        self.assertEqual(base64.b64decode(commit["fileChanges"]["additions"][0]["contents"]).decode(), self.result)

    def test_trigger_read_uses_the_captured_commit(self):
        client = GitHubClient("owner/repo", "main", "test-token")
        payload = {"content": base64.b64encode(b"request").decode()}
        with mock.patch.object(client, "request", return_value=payload) as request:
            self.assertEqual(client.read_file("weather/trigger.json", "immutable-head"), "request")
        self.assertIn("ref=immutable-head", request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
