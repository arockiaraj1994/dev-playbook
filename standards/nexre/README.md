# NexRe - dev-playbook project

Rule docs and AI playbook for **NexRe**, a privacy-first read-it-later Android app.

NexRe lets users save URLs and plain-text notes (via Android share sheet or in-app), optionally enriches them with Gemini AI summaries and tags, and stores everything locally on-device using Room. There is no backend - all data stays on the device.

## Quick start

```
playbook_start_task("nexre", "<your task description>")
```

The server returns guardrails + matched workflow + next calls. Follow the chain.

## Stack

| Component | Choice |
|---|---|
| Language | Kotlin 2.1.0 |
| UI | Jetpack Compose + Material3 |
| Architecture | Clean Architecture (data / domain / ui layers) |
| DI | Hilt 2.54 |
| Database | Room 2.6.1 |
| Networking | Retrofit 2.11 + OkHttp 4.12 + Moshi 1.15 |
| AI | Gemini API (REST via Retrofit) |
| Background work | WorkManager 2.10 |
| Key-value store | DataStore Preferences + EncryptedSharedPreferences |
| Image loading | Coil 2.7 |
| HTML parsing | Jsoup 1.18 |
| Build | AGP 8.7.3, KSP 2.1.0, minSdk 26, targetSdk 35 |

## Maintainer

Arockiaraj - sole developer.
