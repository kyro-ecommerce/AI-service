// k6 load test for the AI service, through the API gateway (anonymous traffic).
//
//   docker run --rm -i -v "${PWD}/loadtest/k6:/scripts" grafana/k6:0.54.0 run \
//     -e BASE_URL=http://host.docker.internal:8080 /scripts/ai_service.js
//
// Read-only by default: recommendations and search. It writes no interaction
// rows and spends no LLM quota. CHAT=1 adds a low-rate chat scenario (parser
// path; set it only when no LLM key is configured or quota does not matter;
// the per-client chat rate limit is 20 requests/minute).
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE = (__ENV.BASE_URL || "http://host.docker.internal:8080") + "/api/v1/ai";
const PRODUCT_IDS = (__ENV.PRODUCT_IDS || "1,2,3,4,6,8,10,12,14,16,18,20").split(",");
const QUERIES = [
  "laptop gaming", "laptop văn phòng mỏng nhẹ", "điện thoại chụp ảnh đẹp",
  "tai nghe chống ồn", "chuột không dây", "bàn phím cơ", "máy tính bảng học online",
  "sạc dự phòng", "laptop dưới 20 triệu", "iphone",
];

const recLatency = new Trend("rec_latency", true);
const searchLatency = new Trend("search_latency", true);

const scenarios = {
  browse: {
    executor: "ramping-vus", exec: "browse", startVUs: 0,
    stages: [
      { duration: "20s", target: Number(__ENV.VUS || 30) },
      { duration: __ENV.HOLD || "60s", target: Number(__ENV.VUS || 30) },
      { duration: "10s", target: 0 },
    ],
  },
  search: {
    executor: "constant-arrival-rate", exec: "search",
    rate: Number(__ENV.SEARCH_RPS || 10), timeUnit: "1s",
    duration: "90s", preAllocatedVUs: 10, maxVUs: 40,
  },
};
if (__ENV.CHAT === "1") {
  scenarios.chat = {
    executor: "constant-arrival-rate", exec: "chat",
    rate: 15, timeUnit: "1m", duration: "60s", preAllocatedVUs: 2, maxVUs: 5,
  };
}

export const options = {
  scenarios,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    rec_latency: ["p(95)<300"],
    search_latency: ["p(95)<500"],
  },
};

const pick = (items) => items[Math.floor(Math.random() * items.length)];
const sessionId = () => `k6-${__VU}`;

export function browse() {
  const headers = { "X-Session-Id": sessionId() };
  const pid = pick(PRODUCT_IDS);
  const calls = [
    `${BASE}/recommendations/trending?limit=8`,
    `${BASE}/recommendations/similar/${pid}?limit=6`,
    `${BASE}/recommendations/accessories/${pid}?limit=6`,
  ];
  for (const url of calls) {
    const res = http.get(url, { headers, tags: { name: url.replace(/\/\d+(\?|$)/, "/:id$1").split("?")[0] } });
    recLatency.add(res.timings.duration);
    check(res, { "rec 200": (r) => r.status === 200 });
  }
  sleep(1 + Math.random());
}

export function search() {
  const q = encodeURIComponent(pick(QUERIES));
  const res = http.get(`${BASE}/search?q=${q}&limit=8`, { tags: { name: "/search" } });
  searchLatency.add(res.timings.duration);
  check(res, { "search 200": (r) => r.status === 200 });
}

export function chat() {
  const res = http.post(
    `${BASE}/chat`,
    JSON.stringify({ message: pick(QUERIES), limit: 4 }),
    { headers: { "Content-Type": "application/json", "X-Session-Id": sessionId() }, tags: { name: "/chat" } },
  );
  check(res, { "chat 200": (r) => r.status === 200 });
}
