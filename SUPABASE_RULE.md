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

### Current (all stoves, wildcard — since 2026-09-30)

```sql
SELECT
  topic,
  payload,
  substr(nth(1, tokens(topic, '/')), 9) as device
FROM
  "+/status/full", "+/status/diag", "+/status/stats", "+/status/crash", "+/status/firmware"
```

`+` matches any single topic level, so `+/status/full` matches
`WoodMood<ANYSERIAL>/status/full`. New stoves are logged automatically — no rule
edits when a unit is added or renamed.

How `device` is built:
- `tokens(topic, '/')` → `["WoodMoodJJQF9D", "status", "full"]`
- `nth(1, …)` → `"WoodMoodJJQF9D"`  *(EMQX arrays are 1-based)*
- `substr(…, 9)` → `"JJQF9D"`  *(EMQX `substr` is 1-based; position 9 skips the 8-char `WoodMood` prefix)*

**Paste this SQL, hit *SQL Test*, then Save.** After saving, check *Statistics* —
`Passed` should track `Matched`. See Troubleshooting below if it doesn't.

> **History before 2026-09-30:** the rule used to be hard-coded to
> `"WoodMoodJJQF9D/status/full", "…/status/diag", "…/status/stats"`, so **only
> JJQF9D was ever logged**. No other stove has Supabase rows before the switch to
> wildcards, and crash/firmware rows start from that date too. This was why
> VEWGAX's History tab showed nothing while JJQF9D's worked.

### How the stoves are kept apart

Every stove writes into the **same** `logs` table; the `device` column is the only
thing that separates them. The History tab therefore **always** filters by one
`device=eq.<SERIAL>` — it never fetches an unfiltered mix (which would plot two
stoves' temperatures as one jagged line). A stove must be picked, or the app must
be connected to one. The Topic box either takes a suffix (`status/full`, scoped to
the picked stove) or a full `WoodMood<SERIAL>/…` topic, whose serial wins.

A renamed unit keeps its old rows under its **old** serial (VEWGAX's history stays
under `VEWGAX`; after reflashing as `TEST001` new rows go under `TEST001`). Both
serials are in the app's `HIST_STOVES` list — pick whichever era you want.

**Required index.** With several stoves at 1 Hz, filtering by `device` without an
index scans the whole table and gets slower every day (eventually PostgREST
times out). Run once in the Supabase **SQL Editor**:

```sql
create index if not exists logs_device_time_idx on logs (device, received_at);
create index if not exists logs_topic_time_idx  on logs (topic, received_at);
```

#### About `status/firmware`

The History tab's header shows the stove's firmware version next to its serial.
Since Sep 2026 firmware no longer puts `fw_version` in `status/full` — the version
lives only on the retained `status/firmware` topic (published once per MQTT
connect, so a handful of rows per day). Without `"+/status/firmware"` in the
`FROM`, History shows **"version not logged"** for current firmware. The
demo/mainline badge still works either way: it is inferred from `status/full`.

#### About `status/crash` specifically

Unlike `status/full` (1 Hz), `status/crash` is published **once per boot** and is
**retained**. That matters for reading the numbers:

- Expect roughly **one row per stove reboot**, not a steady stream. A quiet `logs`
  table for this topic is the healthy case — it means nothing is rebooting.
- A **run of rows in quick succession is a reboot loop** — exactly the signal you want.
  That's why the firmware publishes once per boot rather than once per MQTT connect:
  a flaky link would otherwise manufacture fake "crashes" out of reconnects.
- Retained means the **live** Crash panel works even for a stove that crashed weeks
  ago and hasn't republished. The Supabase rows are the *history*; the retained topic
  is the *current state*. They answer different questions — keep both.
- Payload with no `last` key = that stove has never crashed. `n` is the lifetime count.

> Note: MQTT wildcards match a **whole level** — you cannot write `WoodMood+`.
> Use `+/status/...`. This broker only carries `WoodMood…` topics, so a bare `+`
> first level is safe here.

## Optional: retention / downsampling (staying on the Supabase free plan)

> **Status: NOT ENABLED.** Nothing deletes rows today — `logs` grows forever.
> Fine with two stoves; switch this on when the fleet grows or **Disk Usage**
> (Supabase dashboard → Overview) heads towards the free plan's **500 MB**
> database limit. Everything below is run by hand in the Supabase **SQL Editor**;
> nothing in this repo does it.

`status/full` arrives at 1 Hz per stove and is by far the biggest topic. The plan:

- **Last hour:** keep every row (full 1 s resolution for live debugging).
- **Older than 1 hour:** thin `status/full` to **one row per stove per minute** (~60× smaller).
- **Older than N days:** delete everything.
- `status/diag`, `status/stats`, `status/crash` are never thinned — they're small,
  infrequent, and a short-lived diag code is exactly what you want to find later.

### Check the current size first

```sql
select pg_size_pretty(pg_total_relation_size('logs')) as logs_size,
       count(*) as rows, min(received_at) as oldest
from logs;
```

### Enable

```sql
create extension if not exists pg_cron;
create index if not exists logs_time_idx        on logs (received_at);
create index if not exists logs_device_time_idx on logs (device, received_at);

-- Thin status/full older than 1 h to the first row of each minute, per stove.
-- Only looks back 1 day, so each run is cheap (anything older was already thinned).
create or replace function thin_logs() returns void language sql as $$
  delete from logs l
  using (
    select ctid,
           row_number() over (
             partition by device, date_trunc('minute', received_at)
             order by received_at
           ) as rn
    from logs
    where topic like '%/status/full'
      and received_at <  now() - interval '1 hour'
      and received_at >= now() - interval '1 day'
  ) d
  where l.ctid = d.ctid and d.rn > 1;
$$;

select cron.schedule('thin-logs',  '*/15 * * * *', 'select thin_logs()');

-- Hard cutoff: drop everything older than 60 days (tune — see below)
select cron.schedule('prune-logs', '17 3 * * *',
  $$delete from logs where received_at < now() - interval '60 days'$$);
```

**One-time backlog cleanup** (the cron job only looks back 1 day, so thin what's
already there once, then reclaim the disk space):

```sql
delete from logs l
using (select ctid, row_number() over (partition by device, date_trunc('minute', received_at)
                                        order by received_at) rn
       from logs where topic like '%/status/full' and received_at < now() - interval '1 hour') d
where l.ctid = d.ctid and d.rn > 1;

vacuum full logs;   -- shrinks the file on disk; locks the table for a few seconds
```

**Tuning the 60 days:** after a day, re-run the size query, work out MB/day, and
pick a cutoff that stays under ~350 MB. Postgres reuses space freed by deletes, so
the size plateaus rather than creeping up.

### Check / disable

```sql
select jobname, schedule, active from cron.job;                    -- is it on?
select * from cron.job_run_details order by start_time desc limit 10;  -- did it run OK?

select cron.unschedule('thin-logs');    -- turn off thinning
select cron.unschedule('prune-logs');   -- turn off the hard cutoff
```

### Effect on the History tab

Older than 1 hour, charts show one point per minute — temperature and fan curves
look the same. The Timeline's **insertion-active lane** and **between-log gaps**
become approximate (±1 min) for that older data, because a few-second insertion can
fall between kept samples. The last hour is unaffected.

## Adding a new stove — checklist

1. **Firmware:** set `MQTT_DEVICE_SERIAL` (and, in per-serial mode, `MQTT_DEVICE_SECRET`) in that unit's untracked `Secrets.h` (Arduino lib repo). See `PER_SERIAL_AUTH_GUIDE.md`.
2. **EMQX Cloud auth:** create the authentication user (username = the serial) and, if the broker is in whitelist mode, the ACL rule allowing `WoodMood<SERIAL>/#`.
3. **EMQX rule:** nothing to do — the wildcard `+/status/...` `FROM` above picks the new serial up automatically.
4. **Debug App:** add the serial to `HIST_STOVES` in `index.html` so it appears in the History stove picker (until then, connect to the stove and leave the picker on "Connected").
5. **Verify:** connect the app to that serial, open History, Fetch. Or check the rule's **Statistics** panel — `Passed` and `Action → Success` should be climbing.

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

## Known failure (2026-09-30): every stove logged as JJQF9D

After switching the rule to the wildcard `FROM`, VEWGAX's History stayed empty
even though the rule showed ~1 msg/s Matched/Passed and >1,000 Action Success.
The rows **were** being inserted — but the **action's HTTP body** had `device`
hard-coded to `"JJQF9D"` from the single-stove days, so every stove's rows were
labelled JJQF9D. Tell: pick JJQF9D in History and you see `WoodMood<OTHER>/…`
topics.

Fix: in action `a-pb666061-6da21f` → Settings → Body, set `"device": "${device}"`
(the value the rule SQL computes), never a literal serial. Then relabel the
mis-tagged rows from their own topic in the Supabase SQL Editor:

```sql
update logs
set device = substr(split_part(topic, '/', 1), 9)
where device is distinct from substr(split_part(topic, '/', 1), 9);
```

**Lesson:** a rule going multi-stove needs the SQL `FROM` **and** the sink body
checked — "Action Success" only proves a row was written, not that it was
labelled right.

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
