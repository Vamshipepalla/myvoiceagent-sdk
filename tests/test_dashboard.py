import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from myagentrunner import dashboard


class DashboardTests(unittest.TestCase):
    def test_dashboard_health_and_agent_discovery_routes(self) -> None:
        with TestClient(dashboard.app) as client:
            health = client.get("/api/health")
            agents = client.get("/api/agents")
            removed_upload_route = client.post("/api/agents/upload")
            removed_create_route = client.post("/api/agents", json={"name": "SupportBot"})

        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(agents.status_code, 200)
        self.assertIn("MyAgent", [agent["name"] for agent in agents.json()])
        self.assertEqual(removed_upload_route.status_code, 404)
        self.assertEqual(removed_create_route.status_code, 405)
        my_agent = next(agent for agent in agents.json() if agent["name"] == "MyAgent")
        self.assertEqual(my_agent["directions"], ["outbound"])

    def test_browser_call_issues_a_room_token_and_persists_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = dashboard.DashboardStore(temporary_directory)
            credentials = {
                "LIVEKIT_URL": "wss://test.livekit.cloud",
                "LIVEKIT_API_KEY": "test-api-key-long-enough-for-hmac-validation",
                "LIVEKIT_API_SECRET": "test-api-secret-long-enough-for-hmac-validation",
            }
            with patch.object(dashboard, "store", store), patch.dict(os.environ, credentials):
                result = dashboard.create_call(
                    dashboard.CreateCallRequest(agent_name="MyAgent")
                )

            call = store.call(result["call_id"])
            self.assertIsNotNone(call)
            self.assertEqual(call["agent_name"], "MyAgent")
            self.assertTrue(result["token"])
            self.assertEqual(result["url"], credentials["LIVEKIT_URL"])

    def test_transcript_routes_list_and_read_saved_call_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = dashboard.DashboardStore(temporary_directory)
            store.create_call("call-1", "MyAgent", "outbound")
            store.add_transcript(
                "call-1",
                {
                    "id": "stt-1",
                    "role": "user",
                    "text": "Hi",
                    "final": True,
                    "created_at": 1.0,
                },
            )
            store.add_transcript(
                "call-1",
                {
                    "id": "llm-1",
                    "role": "agent",
                    "text": "Hello",
                    "final": True,
                    "created_at": 2.0,
                },
            )
            with patch.object(dashboard, "store", store), TestClient(dashboard.app) as client:
                calls = client.get("/api/calls")
                transcript_calls = client.get("/api/transcripts")
                transcript = client.get("/api/transcripts/call-1")
                removed_recording_route = client.get("/api/recordings")

            self.assertTrue(calls.json()[0]["transcript_available"])
            self.assertEqual([call["id"] for call in transcript_calls.json()], ["call-1"])
            self.assertEqual([line["text"] for line in transcript.json()], ["Hi", "Hello"])
            self.assertEqual(removed_recording_route.status_code, 404)

    def test_metrics_report_interpolated_p50_and_p90_in_milliseconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = dashboard.DashboardStore(temporary_directory)
            store.create_call("call-1", "MyAgent", "outbound")
            for value in (1.0, 2.0, 3.0, 4.0):
                store.add_metrics("call-1", "assistant", {"e2e_latency": value})
            with patch.object(dashboard, "store", store):
                metric = dashboard._metrics_response("MyAgent")[0]

            self.assertEqual(metric["p50_ms"], 2500.0)
            self.assertEqual(metric["p90_ms"], 3700.0)
            self.assertEqual(metric["count"], 4)

    def test_dashboard_call_history_and_end_endpoint_are_outbound_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = dashboard.DashboardStore(temporary_directory)
            store.create_call("outbound-call", "MyAgent", "outbound")
            store.create_call("inbound-call", "MyAgent", "inbound")
            with patch.object(dashboard, "store", store), TestClient(dashboard.app) as client:
                calls = client.get("/api/calls")
                ended_once = client.post("/api/calls/outbound-call/end")
                ended_twice = client.post("/api/calls/outbound-call/end")
                missing = client.post("/api/calls/missing-call/end")

            self.assertEqual([call["id"] for call in calls.json()], ["outbound-call"])
            self.assertEqual(ended_once.json()["status"], "ended")
            self.assertEqual(ended_twice.json()["status"], "ended")
            self.assertIsNotNone(store.call("outbound-call")["ended_at"])
            self.assertEqual(missing.status_code, 404)

    def test_agent_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(dashboard.HTTPException) as error:
            dashboard._agent_directory("../outside", "outbound")

        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
