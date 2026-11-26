// k6 load test aimed at FuelWatch, never at the simulator (design 8.5).
// Open model: requests arrive at a fixed rate no matter how slowly the system answers, so a slow
// server cannot hide its own queue (a closed model would back off with it).
//
//   TARGET=http://localhost:8080 k6 run loadtest/dashboard.js
//   docker compose --profile loadtest run --rm k6
//
// SCENARIO picks what runs: reads (default), knee (ramp to find the limit), proposals (the plan endpoints only).
import http from 'k6/http';
import { check } from 'k6';

const TARGET = __ENV.TARGET || 'http://localhost:8080';
const SCENARIO = __ENV.SCENARIO || 'reads';
const RATE = Number(__ENV.RATE || 50);
const DURATION = __ENV.DURATION || '60s';

const scenarios = {
  reads: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION, preAllocatedVUs: 50, maxVUs: 400, exec: 'dashboardRead' },
  knee: {
    executor: 'ramping-arrival-rate', startRate: 20, timeUnit: '1s', preAllocatedVUs: 100, maxVUs: 1000, exec: 'dashboardRead',
    stages: [{ target: 100, duration: '30s' }, { target: 300, duration: '30s' }, { target: 600, duration: '30s' }, { target: 1000, duration: '30s' }],
  },
  proposals: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION, preAllocatedVUs: 50, maxVUs: 400, exec: 'planRead' },
};

export const options = {
  scenarios: { [SCENARIO]: scenarios[SCENARIO] },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<250'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// What the dashboard fetches on every refresh.
export function dashboardRead() {
  const res = http.batch([
    ['GET', `${TARGET}/api/status`, null, { tags: { name: 'status' } }],
    ['GET', `${TARGET}/api/risks`, null, { tags: { name: 'risks' } }],
    ['GET', `${TARGET}/api/proposals?status=PROPOSED`, null, { tags: { name: 'proposals' } }],
    ['GET', `${TARGET}/api/alerts`, null, { tags: { name: 'alerts' } }],
  ]);
  for (const r of res) check(r, { 'status 200': (x) => x.status === 200 });
}

// The endpoints that expose the allocation plan.
export function planRead() {
  const res = http.batch([
    ['GET', `${TARGET}/api/proposals`, null, { tags: { name: 'proposals_all' } }],
    ['GET', `${TARGET}/api/allocations`, null, { tags: { name: 'allocations' } }],
    ['GET', `${TARGET}/api/forecast`, null, { tags: { name: 'forecast' } }],
  ]);
  for (const r of res) check(r, { 'status 200': (x) => x.status === 200 });
}
