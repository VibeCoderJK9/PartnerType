"""The read-only MCP demo server: protocol shape and data minimisation."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("partnertype_mcp", ROOT / "tools/mcp/partnertype_mcp.py")
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


class FakeClient:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path.startswith("/api/notifications"):
            return {"notifications": [
                {"title_key": "ntf_block_inactive_title", "is_read": 0, "created_at": "2026-10-04 10:00:00",
                 "params": {"partner": "Anna Nowak", "days": 41, "date": "24.08.2026"}},
                {"title_key": "ntf_new_lead_title", "params": {"client": "Klient X"}},
            ]}
        if path == "/api/partners":
            return {"partners": [{"name": "Anna Nowak", "email": "anna@firma.pl", "phone": "600100200",
                                  "company": "Firma", "tier_name": "Gold", "joined_at": "2026-01-02",
                                  "total_leads": 3, "accepted_leads": 2, "total_earnings": 9000}]}
        raise AssertionError(path)


def call(method, params=None, msg_id=1, client=None):
    return mcp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}},
                      client or FakeClient())


def test_initialize_offers_tools_and_echoes_protocol():
    reply = call("initialize", {"protocolVersion": "2025-06-18"})
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in reply["result"]["capabilities"]


def test_notifications_get_no_reply():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, FakeClient()) is None


def test_every_tool_is_read_only():
    tools = call("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_reminders", "list_partners"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_reminders_keep_only_block_notifications():
    result = call("tools/call", {"name": "list_reminders", "arguments": {}})["result"]
    data = result["structuredContent"]
    assert data["count"] == 1
    assert data["reminders"][0] == {"kind": "inactive_partner", "partner": "Anna Nowak", "days_quiet": 41,
                                    "last_activity": "24.08.2026", "raised_at": "2026-10-04 10:00:00",
                                    "read": False}


def test_partner_list_drops_contact_details_and_money():
    data = call("tools/call", {"name": "list_partners"})["result"]["structuredContent"]
    partner = data["partners"][0]
    assert "email" not in partner and "phone" not in partner and "total_earnings" not in partner
    assert partner["tier"] == "Gold"


def test_unknown_tool_and_method_are_errors():
    assert call("tools/call", {"name": "delete_everything"})["error"]["code"] == -32602
    assert call("resources/list")["error"]["code"] == -32601


def test_api_failure_is_reported_to_the_model_without_crashing():
    class Broken:
        def get(self, path):
            raise mcp.ApiError("Brak dostępu")
    result = call("tools/call", {"name": "list_partners"}, client=Broken())["result"]
    assert result["isError"] is True and "Brak dostępu" in result["content"][0]["text"]
