import type { CubeView } from "../lib/api";

// Channels by month. Rates: App 2.00%, Web 3.30%, Telesales 3.52%, Marketplace 2.00%.
// Overall 264 / 10,000 = 2.64%, while the mean of the four channel rates is 2.705%.
// Marketplace has a December row with no orders (den = 0).
export const ROWS: [string, string, number, number, number, number][] = [
  // channel, month, loss, orders, fraud, checked
  ["App", "2024-12-01", 600, 2500, 50, 2500],
  ["App", "2025-01-01", 700, 2500, 50, 2500],
  ["Marketplace", "2024-12-01", 0, 0, 0, 0],
  ["Marketplace", "2025-01-01", 20, 500, 10, 500],
  ["Telesales", "2024-12-01", 400, 1250, 40, 1250],
  ["Telesales", "2025-01-01", 500, 1250, 48, 1250],
  ["Web", "2024-12-01", 300, 1000, 30, 1000],
  ["Web", "2025-01-01", 350, 1000, 36, 1000],
];

export const CUBE: CubeView = {
  dimensions: ["orders.sales_channel", "month"],
  labels: { "orders.sales_channel": "Sales channel", month: "Month" },
  vocabulary: { "orders.sales_channel": "Sales channel", month: "Month", "orders.fraud_type": "Fraud type" },
  metrics: {
    fraud_loss: { kind: "sum", num: "fraud_loss", den: null, format: "currency", label: "Fraud loss" },
    order_count: { kind: "count", num: "order_count", den: null, format: "count", label: "Orders" },
    fraud_rate: { kind: "ratio", num: "fraud_rate__num", den: "fraud_rate__den", format: "percent", label: "Fraud rate" },
    dealers: { kind: "other", num: "dealers", den: null, format: "count", label: "Dealers" },
  },
  columns: ["orders.sales_channel", "month", "fraud_loss", "order_count", "fraud_rate__num", "fraud_rate__den", "dealers"],
  rows: ROWS.map(([c, m, loss, orders, fraud, checked]) => [c, m, loss, orders, fraud, checked, 3]),
  span: [null, null],
  query_hash: "cube",
};
