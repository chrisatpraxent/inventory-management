"""
Tests for restocking API endpoints.
"""
from datetime import datetime

import pytest


class TestDemandForecastCostFields:
    """Test suite for the costing fields added to demand forecasts."""

    def test_get_all_forecasts_include_cost_and_lead_time(self, client):
        """Test that every demand forecast carries unit_cost and lead_time_days."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        for forecast in data:
            assert "unit_cost" in forecast, \
                f"Forecast {forecast['item_sku']} is missing unit_cost"
            assert "lead_time_days" in forecast, \
                f"Forecast {forecast['item_sku']} is missing lead_time_days"

    def test_forecast_cost_and_lead_time_types(self, client):
        """Test that costing fields are proper numeric types in sensible ranges."""
        response = client.get("/api/demand")
        data = response.json()

        for forecast in data:
            assert isinstance(forecast["unit_cost"], (int, float))
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["unit_cost"] > 0, \
                f"{forecast['item_sku']} has non-positive unit_cost {forecast['unit_cost']}"
            assert 1 <= forecast["lead_time_days"] <= 60, \
                f"{forecast['item_sku']} has implausible lead time {forecast['lead_time_days']}"

    def test_stable_trend_items_have_small_changes(self, client):
        """Test that adding cost fields did not disturb the stable-trend data."""
        response = client.get("/api/demand")
        data = response.json()

        stable_items = [item for item in data if item["trend"].lower() == "stable"]

        assert len(stable_items) >= 5, \
            f"Expected at least 5 stable items, found {len(stable_items)}"

        for item in stable_items:
            current = item["current_demand"]
            forecasted = item["forecasted_demand"]

            if current > 0:
                percent_change = abs((forecasted - current) / current) * 100
                assert percent_change < 2.0, \
                    f"Item {item['item_name']} has {percent_change:.2f}% change, expected < 2%"


class TestRestockOrderEndpoints:
    """Test suite for restock order endpoints."""

    def _first_eligible_forecast(self, client):
        """Return the first forecast whose demand is rising, with its shortfall."""
        forecasts = client.get("/api/demand").json()
        for forecast in forecasts:
            shortfall = forecast["forecasted_demand"] - forecast["current_demand"]
            if shortfall > 0:
                return forecast, shortfall
        pytest.fail("No forecast item has a positive shortfall to restock")

    def test_get_all_restock_orders(self, client):
        """Test getting all restock orders."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

    def test_create_restock_order(self, client):
        """Test submitting a restock order."""
        forecast, shortfall = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "budget": 100000,
            "items": [{"item_sku": forecast["item_sku"], "quantity": shortfall}]
        })
        assert response.status_code == 201

        order = response.json()
        for field in ("id", "order_number", "items", "status", "order_date",
                      "expected_delivery", "lead_time_days", "total_value", "budget"):
            assert field in order, f"Created order is missing {field}"

        assert order["status"].lower() == "submitted"
        assert len(order["items"]) == 1
        assert order["items"][0]["item_sku"] == forecast["item_sku"]
        assert order["items"][0]["quantity"] == shortfall

    def test_create_restock_order_uses_server_side_cost(self, client):
        """Test that a client-supplied unit_cost is ignored in favour of forecast data."""
        forecast, shortfall = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "items": [{
                "item_sku": forecast["item_sku"],
                "quantity": shortfall,
                "unit_cost": 0.01  # deliberately wrong; must not be trusted
            }]
        })
        assert response.status_code == 201

        line = response.json()["items"][0]
        assert abs(line["unit_cost"] - forecast["unit_cost"]) < 0.01, \
            f"Expected server cost {forecast['unit_cost']}, got {line['unit_cost']}"

    def test_create_restock_order_total_value_calculation(self, client):
        """Test that total_value equals the sum of the line totals."""
        forecast, shortfall = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "items": [{"item_sku": forecast["item_sku"], "quantity": shortfall}]
        })
        order = response.json()

        expected_line = shortfall * forecast["unit_cost"]
        assert abs(order["items"][0]["line_total"] - expected_line) < 0.01
        assert abs(order["total_value"] - expected_line) < 0.01

    def test_create_restock_order_lead_time_is_max_across_items(self, client):
        """Test that an order's lead time is the slowest of its line items."""
        forecasts = client.get("/api/demand").json()
        eligible = [
            f for f in forecasts
            if f["forecasted_demand"] - f["current_demand"] > 0
        ]
        assert len(eligible) >= 2, "Need at least two eligible items for this test"

        by_lead_time = sorted(eligible, key=lambda f: f["lead_time_days"])
        fastest, slowest = by_lead_time[0], by_lead_time[-1]
        assert slowest["lead_time_days"] > fastest["lead_time_days"], \
            "Test data needs two items with differing lead times"

        response = client.post("/api/restock-orders", json={
            "items": [
                {"item_sku": fastest["item_sku"], "quantity": 1},
                {"item_sku": slowest["item_sku"], "quantity": 1}
            ]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["lead_time_days"] == slowest["lead_time_days"], \
            f"Expected lead time {slowest['lead_time_days']}, got {order['lead_time_days']}"

    def test_create_restock_order_expected_delivery_matches_lead_time(self, client):
        """Test that expected_delivery is order_date plus the order's lead time."""
        forecast, _ = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "items": [{"item_sku": forecast["item_sku"], "quantity": 1}]
        })
        order = response.json()

        order_date = datetime.fromisoformat(order["order_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery"])

        assert (expected_delivery - order_date).days == order["lead_time_days"], \
            f"Delivery gap does not match lead time of {order['lead_time_days']} days"
        assert "T" in order["expected_delivery"]  # ISO format with time component

    def test_create_restock_order_number_format(self, client):
        """Test that submitted orders get an RST-YYYY-NNNN order number."""
        forecast, _ = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "items": [{"item_sku": forecast["item_sku"], "quantity": 1}]
        })
        order_number = response.json()["order_number"]

        parts = order_number.split("-")
        assert len(parts) == 3, f"Unexpected order number format: {order_number}"
        assert parts[0] == "RST"
        assert len(parts[1]) == 4 and parts[1].isdigit()
        assert len(parts[2]) == 4 and parts[2].isdigit()

    def test_created_restock_order_appears_in_list(self, client):
        """Test that a submitted order is returned by the list endpoint."""
        forecast, _ = self._first_eligible_forecast(client)

        # Restock orders accumulate in memory for the whole session, so compare
        # against the count before this test rather than assuming an empty list.
        count_before = len(client.get("/api/restock-orders").json())

        created = client.post("/api/restock-orders", json={
            "items": [{"item_sku": forecast["item_sku"], "quantity": 1}]
        }).json()

        orders_after = client.get("/api/restock-orders").json()
        assert len(orders_after) == count_before + 1

        order_numbers = [order["order_number"] for order in orders_after]
        assert created["order_number"] in order_numbers

    def test_create_restock_order_with_empty_items(self, client):
        """Test that an order with no line items is rejected."""
        response = client.post("/api/restock-orders", json={"items": []})
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "at least one item" in data["detail"].lower()

    def test_create_restock_order_with_nonexistent_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post("/api/restock-orders", json={
            "items": [{"item_sku": "nonexistent-sku-999", "quantity": 5}]
        })
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()

    def test_create_restock_order_with_zero_quantity(self, client):
        """Test that a non-positive quantity is rejected."""
        forecast, _ = self._first_eligible_forecast(client)

        response = client.post("/api/restock-orders", json={
            "items": [{"item_sku": forecast["item_sku"], "quantity": 0}]
        })
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "at least 1" in data["detail"].lower()

    def test_create_restock_order_missing_items_field(self, client):
        """Test that a request body without an items field fails validation."""
        response = client.post("/api/restock-orders", json={"budget": 5000})
        assert response.status_code == 422  # Validation error
