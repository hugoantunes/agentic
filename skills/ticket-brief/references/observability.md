# Observability queries

Which source answers which question, and query shapes that already work. The examples use the
Datadog MCP server's `execute_code` tool; the rules apply to any tool (Grafana with Prometheus,
Loki or Tempo, New Relic, Honeycomb, Sentry, CloudWatch, Elastic). Change only the filters, the
facets and the window. Put each question in its own call inside one script.

## Rules for every query

- **Filter by service.** A span search without a service filter also matches database and HTTP
  client spans, and one returned 25k characters.
- **Cut long values before returning them,** e.g. `String(v).slice(0, 120)`. Return counts, not
  raw rows.
- **Pick the source by the question:**
  - How many requests or errors a route gets, or whether it's used at all: request metrics that
    count every request (trace metrics, Prometheus counters, load-balancer metrics).
  - A breakdown by something the metrics don't carry, such as a tenant or a custom span
    attribute: spans. They are usually a sample.
  - A message or an id that only appears in logs: logs.
- **Confirm the metric name exists before trusting an empty result.** Names depend on the
  instrumentation: in Datadog, a service traced with OpenTelemetry reports
  `trace.http.server.request.hits`, while the vendor's framework integration reports names like
  `trace.flask.request.hits` or `trace.express.request.hits`. Querying the wrong one returns zero
  series, even for `{*}`.
- **Check whether a sampler is configured** before calling trace metrics complete. Read the
  tracer config in the repo and say so in the brief: config is a lead, not live state.
- **An empty result** from spans or logs means "no matching events in <window>", not "unused".
  Only an empty unsampled-metric result, from a metric name you've confirmed exists, supports "no
  traffic", and the brief still names the window.
- **A missing series for an error filter** (e.g. `http.status_code:5*`) usually means zero, not a
  failed query. Check it against the unfiltered total.

## Requests per route and status (unsampled, Datadog)

Ran in under 1 s.

- Resource names are normalized, e.g. `post_/orgs/_int:org_id_/items`. Wildcards work:
  `resource_name:*<route part>*`.
- For status codes, use the `.by_http_status` variant and group by `http.status_code`.

```js
import { client, v2 } from "@datadog/datadog-api-client";
const api = new v2.MetricsApi(client.createConfiguration());
const from = Date.parse(dd.time.hoursAgo(24 * 14));
const to = Date.parse(dd.time.now());
async function scalar(query) {
  const r = await api.queryScalarData({ body: { data: { type: "scalar_request", attributes: {
    from, to,
    queries: [{ dataSource: "metrics", name: "a", query, aggregator: "sum" }],
    formulas: [{ formula: "a" }],
  } } } });
  const cols = r.data?.attributes?.columns ?? [];
  const groups = cols.filter(c => c.type === "group");
  const nums = cols.find(c => c.type === "number");
  return (nums?.values ?? []).map((v, i) => ({ by: groups.map(g => String(g.values[i]).slice(0, 120)), hits: Math.round(v) }));
}
return {
  byRoute: await scalar("sum:trace.http.server.request.hits{service:<service>,resource_name:*<route part>*} by {resource_name,env}.as_count()"),
  byStatus: await scalar("sum:trace.http.server.request.hits.by_http_status{service:<service>,resource_name:*<route part>*} by {env,http.status_code}.as_count()"),
};
```

The Prometheus equivalent, with the metric and label names your instrumentation uses:
`sum by (route, status) (increase(<requests_total>{service="<service>"}[14d]))`.

## Span counts grouped by an attribute (sampled, Datadog)

Ran in about 3 s and returned under 1k characters.

```js
import { client, v2 } from "@datadog/datadog-api-client";
const api = new v2.SpansApi(client.createConfiguration());
async function agg(query) {
  const r = await api.aggregateSpans({ body: { data: { type: "aggregate_request", attributes: {
    filter: { from: "now-14d", to: "now", query },
    compute: [{ aggregation: "count", type: "total" }],
    groupBy: [
      { facet: "env", limit: 10, missing: "none" },
      { facet: "@<custom.attribute>", limit: 5, missing: "missing" },
    ],
  } } } });
  return { status: r.meta?.status, buckets: (r.data || []).map(b => ({ by: b.attributes.by, count: b.attributes.compute?.c0 })) };
}
return { op: await agg("service:<service> (resource_name:<operation> OR operation_name:<operation>)") };
```

## Log counts per service and env (Datadog)

Ran in 3 to 6 s.

- Structured fields often hold the ids a full-text search misses: a full-text search for a
  filename returned 0 while `@<field>:*<filename part>*` matched. Find the field from one sample
  log line first.

```js
import { client, v2 } from "@datadog/datadog-api-client";
const api = new v2.LogsApi(client.createConfiguration());
async function agg(query) {
  const r = await api.aggregateLogs({ body: {
    filter: { from: "now-14d", to: "now", query },
    compute: [{ aggregation: "count", type: "total" }],
    groupBy: [{ facet: "service", limit: 10 }, { facet: "env", limit: 5 }],
  } });
  return { status: r.meta?.status, buckets: (r.data?.buckets || []).map(b => ({ by: b.by, count: b.computes?.c0 })) };
}
return { hits: await agg("\"<message or id>\"") };
```

## Errors during each deploy vs the same window a day earlier (unsampled)

Ran 10 deploys in 6 s.

- Take deploy windows from what deployed, not from tag or merge dates: the GitOps controller's
  history (e.g. Argo CD `.status.history[*].deployStartedAt/deployedAt/revision`), the platform's
  release list, or the CD pipeline's run times. Map each revision to its commit with
  `git log --no-walk --format="%h %cs %s" <revs>`.
- Deploy history often keeps only successful syncs. A failed attempt whose pre-deploy hook
  (migrations, jobs) already ran isn't listed, and in one case errors started 12 minutes before
  the recorded start. Widen the window before the start.

```js
import { client, v2 } from "@datadog/datadog-api-client";
const metrics = new v2.MetricsApi(client.createConfiguration());
async function errOfAll(from, to) {
  const r = await metrics.queryScalarData({ body: { data: { type: "scalar_request", attributes: {
    from: Date.parse(from), to: Date.parse(to),
    queries: [
      { dataSource: "metrics", name: "e", query: "sum:trace.http.server.request.hits.by_http_status{service:<service>,env:<env>,http.status_code:5*}.as_count()", aggregator: "sum" },
      { dataSource: "metrics", name: "a", query: "sum:trace.http.server.request.hits{service:<service>,env:<env>}.as_count()", aggregator: "sum" }],
    formulas: [{ formula: "e" }, { formula: "a" }] } } } });
  return (r.data?.attributes?.columns ?? []).filter(c => c.type === "number").map(c => Math.round(c.values?.[0] ?? 0)).join("/");
}
const day = 86400000, shift = (iso, ms) => new Date(Date.parse(iso) + ms).toISOString();
const deploys = [["<deployStartedAt ISO>", "<deployedAt ISO>"]];
const out = [];
for (const [s, e] of deploys) {
  const s0 = shift(s, -15 * 60000), e0 = shift(e, 3 * 60000);
  out.push({ deploy: s, errorsOfAll: await errOfAll(s0, e0), dayBefore: await errOfAll(shift(s0, -day), shift(e0, -day)) });
}
return out;
```

## OOM kills per deployment, and the job each kill was running

- **Count kills from the kill metric** (Datadog `oom_kill.oom_process.count`, or
  `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}` in Prometheus), grouped
  by deployment.
- **Use working set or RSS for memory.** Container "usage" includes page cache and can sit at the
  limit for weeks without meaning anything. Expect short spikes to fall between samples.
- **Group by deployment before blaming one.** An alert's container name can match more than one
  deployment, e.g. an API and its worker built from the same image with the same pod labels.
- **Tie a kill to its input.** Find the job or task arguments in the worker's logs (a structured
  field, see above), pair "received" with "succeeded" counts, and line up the received times with
  the pods' `lastState.terminated.finishedAt` from `kubectl get pod -o jsonpath`.

```js
import { client, v2 } from "@datadog/datadog-api-client";
const metrics = new v2.MetricsApi(client.createConfiguration());
const from = Date.parse(dd.time.hoursAgo(24 * 30)), to = Date.parse(dd.time.now());
const r = await metrics.queryTimeseriesData({ body: { data: { type: "timeseries_request", attributes: {
  from, to, interval: 86400000,
  queries: [{ dataSource: "metrics", name: "a", query: "sum:oom_kill.oom_process.count{env:<env>,kube_deployment:<deployment prefix>*} by {kube_deployment}.as_count()" }],
  formulas: [{ formula: "a" }] } } } });
const a = r.data?.attributes ?? {};
return (a.series ?? []).map((s, i) => ({ by: s.groupTags, days: (a.times ?? []).map((t, j) => [new Date(t).toISOString().slice(0, 10), a.values?.[i]?.[j]]).filter(([, v]) => v) }));
```

## Monitor inventory: state, mutes, routing, and whether the metric exists

- **Mutes live in downtimes,** not only on the monitor. In Datadog, read v1
  `DowntimesApi.listDowntimes({ currentOnly: true })`.
- **`OK` doesn't prove a monitor works.** With no-data alerts off, a `sum(...)` monitor on a
  metric with no series sits in OK forever. Check that the metric has series with a scalar query.
- **Routing:** list the notification handles in each monitor's message. A monitor that pages
  nobody is the same as no monitor.

```js
import { client, v1, v2 } from "@datadog/datadog-api-client";
const conf = client.createConfiguration();
const mon = new v1.MonitorsApi(conf), dt = new v1.DowntimesApi(conf), metrics = new v2.MetricsApi(conf);
const all = await mon.listMonitors({ page: 0, pageSize: 1000 });
const muted = new Set((await dt.listDowntimes({ currentOnly: true })).map(d => d.monitorId));
const handles = m => [...new Set((m.message || "").match(/@[\w\-\.]+/g) || [])].join(" ");
const mine = all.filter(m => /<service>/i.test(m.name) || /<service>/i.test(m.query));
const from = Date.parse(dd.time.hoursAgo(24 * 7)), to = Date.parse(dd.time.now());
async function exists(query) {
  const r = await metrics.queryScalarData({ body: { data: { type: "scalar_request", attributes: { from, to,
    queries: [{ dataSource: "metrics", name: "a", query, aggregator: "sum" }], formulas: [{ formula: "a" }] } } } });
  return (r.data?.attributes?.columns ?? []).find(c => c.type === "number")?.values?.length ? "has series" : "no series";
}
return {
  rows: mine.map(m => `${m.id}|${m.overallState}|${muted.has(m.id) ? "MUTED" : ""}|${String(m.name).slice(0, 60)}|${handles(m).slice(0, 60)}`),
  metric: await exists("sum:<metric the monitor uses>{service:<service>}.as_count()"),
};
```

## Anything else

Follow the observability server's own instructions. For Datadog: run skill discovery, then
`execute_code`, and give `search_datadog_sdk` a narrow query that names the API and method,
because a broad one returned 14k characters.

## Saving a proven query

This file ships with the skill and is replaced on update, so don't edit it. When a query works on
this project and isn't just a filter change of one above, save it as a project memory: what it
answers, the metric, service and env names it uses, the gotchas you hit, and the trimmed script.
Next time, check memory for it before writing a new one.
