"""The AI assistant and the MCP gateway may read shop orders, never decide them."""
import pytest

from api import affiliate, agent_tools
from api.agent_tools import GatewayToolError, execute_gateway_tool, execute_tool, tools_for_user
from api.postgres import user_context
from api.tests.integration import gateway_helpers as gh
from api.tests.integration.affiliate_helpers import ORDER, enable, link_for, post_webhook


@pytest.fixture
def orders(admin_user, partner_user, partner_user_bronze):
    """One order of each partner; the gold partner's one is approved."""
    secret = enable(admin_user)
    ids = {}
    for number, partner in enumerate((partner_user, partner_user_bronze)):
        code = link_for(partner)["code"]
        ids[partner["id"]] = post_webhook(
            secret, {**ORDER, "order_id": f"T-{number}", "ref": code}).get_json()["conversion_id"]
    affiliate.approve(admin_user, [ids[partner_user["id"]]])
    return ids


def _run(user, name, args=None):
    with user_context(user):
        return execute_tool(user, name, args or {})["result"]


def _gateway(user, args=None):
    with user_context(user):
        return execute_gateway_tool(user, "list_affiliate_orders", args or {})["result"]


def test_no_tool_can_decide_an_order():
    for registry in (agent_tools.TOOL_REGISTRY, agent_tools.GATEWAY_TOOL_REGISTRY):
        for name, spec in registry.items():
            if "affiliate" in name:
                assert spec["risk"] == "read", name


def test_the_assistant_offers_the_read_to_the_admin_and_the_partner(admin_user, partner_user):
    for user in (admin_user, partner_user):
        names = {item["function"]["name"] for item in tools_for_user(user)}
        assert "list_affiliate_orders" in names


def test_a_partner_reads_only_his_own_orders(orders, partner_user):
    result = _run(partner_user, "list_affiliate_orders")
    assert result["ok"] is True
    assert [item["order_id"] for item in result["untrusted_data"]] == ["T-0"]
    assert result["untrusted_data"][0]["status"] == "approved"
    assert result["untrusted_data"][0]["commission"] is not None
    assert "partner_name" not in result["untrusted_data"][0]


def test_the_admin_reads_every_order_with_a_status_filter(orders, admin_user):
    result = _run(admin_user, "list_affiliate_orders", {"status": "pending"})
    assert [item["order_id"] for item in result["untrusted_data"]] == ["T-1"]
    assert _run(admin_user, "list_affiliate_orders", {"status": "nonsense"})["ok"] is False


def test_a_role_without_the_assistant_tool_is_refused(orders, manager_user):
    assert _run(manager_user, "list_affiliate_orders")["code"] == "forbidden_tool"


def test_the_gateway_scopes_a_partner_and_hides_money_by_default(orders, partner_user):
    gh.set_integration_settings(enabled=True, partners=True)
    result = _gateway(partner_user)
    [item] = result["untrusted_data"]
    assert item["order_id"] == "T-0"
    assert "commission" not in item


def test_the_gateway_shows_commission_and_names_only_when_shared(orders, admin_user):
    gh.set_integration_settings(enabled=True, field_groups=("commissions", "contact"))
    items = _gateway(admin_user)["untrusted_data"]
    assert {item["order_id"] for item in items} == {"T-0", "T-1"}
    assert all("commission" in item and "partner_name" in item for item in items)
    gh.set_integration_settings(enabled=True)
    items = _gateway(admin_user)["untrusted_data"]
    assert all("commission" not in item and "partner_name" not in item for item in items)


def test_the_observer_cannot_call_the_gateway_tool(orders):
    observer = gh.create_user("observer", "observer.aff@example.test")
    with pytest.raises(GatewayToolError) as caught:
        _gateway(observer)
    assert caught.value.forbidden is True


def test_the_gateway_rejects_an_unknown_status(orders, admin_user):
    with pytest.raises(GatewayToolError) as caught:
        _gateway(admin_user, {"status": "paid"})
    assert caught.value.code == "invalid_arguments"


def test_an_approval_is_announced_to_blocks_after_commit(admin_user, partner_user, mocker):
    emit = mocker.patch("api.blocks.emit")
    secret = enable(admin_user)
    code = link_for(partner_user)["code"]
    conversion_id = post_webhook(secret, {**ORDER, "ref": code}).get_json()["conversion_id"]
    emit.assert_not_called()
    [result] = affiliate.approve(admin_user, [conversion_id])
    emit.assert_called_once_with("affiliate.conversion", result["lead_id"], admin_user["id"])
