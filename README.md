# WoodMood-DebugApp

This app is designed to parse through the MQTT out and have a view of what is going on with the stove for the installers
to be able to debug the system for example: right door open so no insertion. 

https://henri-dlgx.github.io/WoodMood-DebugApp

## Docs

- [SUPABASE_RULE.md](SUPABASE_RULE.md) — how the **History** tab gets its data
  (EMQX Cloud rule → Supabase `logs` table), the working rule SQL, how to add
  more stoves, and how to troubleshoot when History shows only 0s.
- [logger/README.md](logger/README.md) — optional always-on CSV logger for a
  cloud VM (separate from the Supabase pipeline).

