<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budgetTitle') }}</h3>
        </div>
        <div class="budget-slider-container">
          <div class="budget-current">{{ formatCurrency(budget) }}</div>
          <input
            type="range"
            class="budget-slider"
            v-model.number="budget"
            :min="0"
            :max="maxBudget"
            :step="100"
          />
          <div class="budget-range-labels">
            <span>{{ formatCurrency(0) }}</span>
            <span>{{ formatCurrency(maxBudget) }}</span>
          </div>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budgetTitle') }}</div>
          <div class="stat-value">{{ formatCurrency(budget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.recommendedSpend') }}</div>
          <div class="stat-value">{{ formatCurrency(totalCost) }}</div>
        </div>
        <div class="stat-card" :class="remainingBudget === 0 ? 'warning' : 'success'">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remainingBudget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemsSelected') }}</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ recommendations.length }})</h3>
        </div>

        <div v-if="eligibleItems.length === 0" class="empty-state">
          {{ t('restocking.noShortfall') }}
        </div>
        <div v-else-if="recommendations.length === 0" class="empty-state">
          <div>{{ t('restocking.noRecommendations') }}</div>
          <div>{{ t('restocking.increaseBudget') }}</div>
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.current') }}</th>
                <th>{{ t('restocking.table.forecast') }}</th>
                <th>{{ t('restocking.table.shortfall') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.qtyToOrder') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recommendations" :key="item.item_sku">
                <td><strong>{{ item.item_sku }}</strong></td>
                <td>{{ translateProductName(item.item_name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">
                    {{ t(`trends.${item.trend}`) }}
                  </span>
                </td>
                <td>{{ item.current_demand }}</td>
                <td>{{ item.forecasted_demand }}</td>
                <td>{{ item.shortfall }}</td>
                <td>{{ formatUnitCost(item.unit_cost) }}</td>
                <td>
                  {{ item.quantity }}
                  <span v-if="item.isPartial" class="badge warning">{{ t('restocking.partial') }}</span>
                </td>
                <td><strong>{{ formatCurrency(item.lineTotal) }}</strong></td>
                <td>{{ item.lead_time_days }} {{ t('restocking.days') }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="place-order-row" v-if="recommendations.length > 0">
          <button
            class="place-order-btn"
            :disabled="!canPlaceOrder"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="submitError" class="error">{{ submitError }}</div>

        <div v-if="placedOrder" class="success-panel">
          <div class="success-title">{{ t('restocking.orderPlaced', { orderNumber: placedOrder.order_number }) }}</div>
          <div class="success-meta">
            <span>{{ t('restocking.orderTotal') }}: <strong>{{ formatCurrency(placedOrder.total_value) }}</strong></span>
            <span>{{ t('restocking.expectedDelivery') }}: <strong>{{ formatDate(placedOrder.expected_delivery) }}</strong></span>
          </div>
          <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency as formatCurrencyUtil, formatCurrencyWithDecimals } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName } = useI18n()
    const formatCurrency = (value) => formatCurrencyUtil(value, currentCurrency.value)

    // Per-unit prices need cents - formatCurrency rounds to whole units, which
    // would render an $8.75 gasket as "$9" and make the line totals look wrong.
    const formatUnitCost = (value) => formatCurrencyWithDecimals(value, currentCurrency.value, 2)

    const loading = ref(true)
    const error = ref(null)
    const allForecasts = ref([])

    const budget = ref(0)
    const submitting = ref(false)
    const submitError = ref(null)
    const placedOrder = ref(null)

    const loadForecasts = async () => {
      try {
        loading.value = true
        error.value = null
        allForecasts.value = await api.getDemandForecasts()
      } catch (err) {
        error.value = 'Failed to load demand forecasts: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // shortfall > 0 only
    const eligibleItems = computed(() => {
      return allForecasts.value
        .filter(f => (f.forecasted_demand - f.current_demand) > 0)
        .map(f => {
          const shortfall = f.forecasted_demand - f.current_demand
          return {
            ...f,
            shortfall,
            fullCost: shortfall * f.unit_cost,
            urgency: shortfall / f.current_demand
          }
        })
    })

    const rankedItems = computed(() => {
      return [...eligibleItems.value].sort((a, b) => {
        if (b.urgency !== a.urgency) return b.urgency - a.urgency
        if (a.unit_cost !== b.unit_cost) return a.unit_cost - b.unit_cost
        return a.item_sku.localeCompare(b.item_sku)
      })
    })

    const maxBudget = computed(() => {
      const total = eligibleItems.value.reduce((sum, f) => sum + f.fullCost, 0)
      if (!total || total <= 0) return 1000
      return Math.ceil(total / 1000) * 1000
    })

    const recommendations = computed(() => {
      let remaining = budget.value
      const result = []

      for (const f of rankedItems.value) {
        if (f.fullCost <= remaining) {
          result.push({
            ...f,
            quantity: f.shortfall,
            lineTotal: f.fullCost,
            isPartial: false
          })
          remaining -= f.fullCost
        } else {
          const qty = Math.floor(remaining / f.unit_cost)
          if (qty >= 1) {
            const lineTotal = qty * f.unit_cost
            result.push({
              ...f,
              quantity: qty,
              lineTotal,
              isPartial: true
            })
            remaining -= lineTotal
          }
        }
      }

      return result
    })

    const totalCost = computed(() => {
      return recommendations.value.reduce((sum, r) => sum + r.lineTotal, 0)
    })

    const remainingBudget = computed(() => {
      return Math.max(budget.value - totalCost.value, 0)
    })

    const orderLeadTime = computed(() => {
      if (recommendations.value.length === 0) return 0
      return Math.max(...recommendations.value.map(r => r.lead_time_days))
    })

    const canPlaceOrder = computed(() => {
      return recommendations.value.length > 0 && !submitting.value
    })

    const placeOrder = async () => {
      submitting.value = true
      submitError.value = null
      try {
        const order = await api.createRestockOrder({
          budget: budget.value,
          items: recommendations.value.map(r => ({
            item_sku: r.item_sku,
            quantity: r.quantity
          }))
        })
        placedOrder.value = order
      } catch (err) {
        submitError.value = t('restocking.orderFailed') + (err.response?.data?.detail ? `: ${err.response.data.detail}` : '')
      } finally {
        submitting.value = false
      }
    }

    const formatDate = (dateString) => {
      if (!dateString) return '-'
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return '-'
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    onMounted(async () => {
      await loadForecasts()
      // Start mid-range rather than at the ceiling so the budget constraint is
      // visible on load and the slider has room to move in both directions.
      budget.value = Math.round(maxBudget.value / 2 / 100) * 100
    })

    return {
      t,
      loading,
      error,
      budget,
      maxBudget,
      eligibleItems,
      recommendations,
      totalCost,
      remainingBudget,
      orderLeadTime,
      canPlaceOrder,
      submitting,
      submitError,
      placedOrder,
      placeOrder,
      formatCurrency,
      formatUnitCost,
      formatDate,
      translateProductName
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-slider-container {
  padding: 0.5rem 0.5rem 0.25rem;
}

.budget-current {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 1rem;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  -webkit-appearance: none;
  appearance: none;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}

.budget-slider::-moz-range-track {
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  margin-top: -6px;
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  cursor: pointer;
}

.budget-slider:focus-visible {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.budget-range-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: #94a3b8;
}

.empty-state {
  padding: 2.5rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.place-order-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 1.25rem;
}

.place-order-btn {
  padding: 0.75rem 1.75rem;
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.success-panel {
  margin-top: 1.25rem;
  padding: 1.25rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.success-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: #065f46;
}

.success-meta {
  display: flex;
  gap: 1.5rem;
  font-size: 0.875rem;
  color: #334155;
}

.success-panel a {
  color: #2563eb;
  font-weight: 600;
  text-decoration: none;
  width: fit-content;
}

.success-panel a:hover {
  text-decoration: underline;
}
</style>
