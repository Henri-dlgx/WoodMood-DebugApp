# History pipeline: EMQX Cloud rule → Supabase

The **History** tab in this app does not talk to MQTT. It only reads rows out of
a Supabase table called `logs`. Those rows are written **server-side by an EMQX
Cloud rule** — there is no code in this repo that writes them, so if History goes
blank the problem is almost always in the EMQX Cloud console, not here.

```
stove firmware ──publish──► EMQX Cloud broker ──[Rule + HTTP Sink]──► Supabase "logs" ──HTTPS read──► History tab
```

- **Broker:** `pb666061.ala.eu-central-1.emqxsl.com` (EMQX Cloud)
- **Supabase project:** `https://zgpmedlnstgnbvuvyukv.supabase.co`, table `logs`
- **EMQX rule id:** `r-pb666061-217569`  •  **Connector:** `c-pb666061-967a60` (HTTP Server) → posts to the Supabase REST API with a **service-role** key. The anon key this web app uses is read-only.

## The `logs` table

| column        | source                                              |
| ------------- | --------------------------------------------------- |
| `topic`       | MQTT topic, e.g. `WoodMoodJJQF9D/status/full`        |
| `payload`     | the message body, stored as JSON (`jsonb`)           |
| `device`      | the stove serial, e.g. `JJQF9D` — derived in the rule SQL |
| `received_at` | insert timestamp (UTC)                               |

The app filters by serial with `device=eq.<serial>` (see `fetchHistory()` in
`index.html`), so **`device` must be the bare serial** — `JJQF9D`, *not*
`WoodMoodJJQF9D`.

## The rule SQL

### Current (single stove)

```sql
SELECT
  topic,
  payload,
  substr(nth(1, tokens(topic, '/')), 9) as device
FROM
  "WoodMoodJJQF9D/status/full", "WoodMoodJJQF9D/status/diag", "WoodMoodJJQF9D/status/stats"
```

How `device` is built:
- `tokens(topic, '/')` → `["WoodMoodJJQF9D", "status", "full"]`
- `nth(1, …)` → `"WoodMoodJJQF9D"`  *(EMQX arrays are 1-based)*
- `substr(…, 9)` → `"JJQF9D"`  *(EMQX `substr` is 1-based; position 9 skips the 8-char `WoodMood` prefix)*

### Recommended (all stoves, auto-capturing)

When you add more stoves, **switch the `FROM` to single-level wildcards** so new
units are logged automatically without ever touching this rule again:

```sql
SELECT
  topic,
  payload,
  substr(nth(1, tokens(topic, '/')), 9) as device
FROM
  "+/status/full", "+/status/diag", "+/status/stats"
```

`+` matches any single topic level, so `+/status/full` matches
`WoodMood<ANYSERIAL>/status/full`. The `device` expression already strips the
`WoodMood` prefix, so each stove lands under its own serial with no per-stove
edits.

> Note: MQTT wildcards match a **whole level** — you cannot write `WoodMood+`.
> Use `+/status/...`. This broker only carries `WoodMood…` topics, so a bare `+`
> first level is safe here.

## Adding a new stove — checklist

1. **Firmware:** set `MQTT_DEVICE_SERIAL` (and, in per-serial mode, `MQTT_DEVICE_SECRET`) in that unit's untracked `Secrets.h` (Arduino lib repo). See `PER_SERIAL_AUTH_GUIDE.md`.
2. **EMQX Cloud auth:** create the authentication user (username = the serial) and, if the broker is in whitelist mode, the ACL rule allowing `WoodMood<SERIAL>/#`.
3. **EMQX rule:** nothing to do **if** you're on the wildcard `+/status/...` `FROM` above. If you're still on the hard-coded single-stove `FROM`, append the new topics: `"WoodMood<SERIAL>/status/full", "WoodMood<SERIAL>/status/diag", "WoodMood<SERIAL>/status/stats"`.
4. **Verify:** connect the app to that serial, open History, Fetch. Or check the rule's **Statistics** panel — `Passed` and `Action → Success` should be climbing.

## Troubleshooting: History shows only 0s / empty

History going blank means **no recent rows in `logs`** for that serial. Work down
the pipe, not the app:

1. **Is the stove publishing?** Subscribe read-only to `WoodMood<SERIAL>/status/full` (TLS 8883, paho-mqtt). Real values = firmware/broker fine; move on.
2. **Rule Statistics** (rule `r-pb666061-217569` → *Statistics*):
   - `Matched` climbing but `Passed = 0` / `Failed = Matched` → **the SQL is throwing**. Failure detail will say *"SQL syntax / function call error."* Fix the SQL and use the built-in **SQL Test** before saving. (This is exactly what bit us — see below.)
   - `Matched = 0` → messages aren't reaching the rule: wrong topic in `FROM`, or ACL/authorization is blocking that stove's publishes.
   - `Passed` climbing but `Action → Failed` climbing → the rule is fine but the **HTTP POST to Supabase** is rejected: check the connector's URL + the Supabase key in its headers.
3. **Connector/action "Connected" / "Available" is not enough** — those only mean the endpoint is reachable, not that individual rows are being inserted. Trust the numeric `Passed` / `Action Success` counters instead.
4. **Query Supabase directly** to see the newest row's timestamp and `device`:
   ```
   GET https://zgpmedlnstgnbvuvyukv.supabase.co/rest/v1/logs?select=received_at,device,topic&order=received_at.desc&limit=3
   Headers: apikey: <anon key>   Authorization: Bearer <anon key>
   ```

## Known failure (2026-07-07)

After migrating firmware to per-serial auth, the rule SQL had been rewritten to
`substr(topic(1), 9) as device`. **`topic()` is not an EMQX function** — `topic`
is a field — so `topic(1)` threw a function-call error on **100%** of messages
(`Matched 8571 / Failed 8571 / Passed 0`, `Action Total 0`). The action never ran,
so Supabase received nothing and History read all 0s. Writes had frozen at
`2026-07-06 14:26 UTC`. Fix: replace `topic(1)` with `nth(1, tokens(topic, '/'))`.
The whitelist→blacklist authorization change made at the same time was unrelated
(messages were matching fine).

**Lesson:** to reference a topic segment in EMQX rule SQL, use
`nth(N, tokens(topic, '/'))`, never `topic(N)`.
