# Notifications — Contrat partagé `NotificationEvent` v1

Référence canonique du payload publié sur la **queue notifications dédiée**
(`fairfare-box-notifications`) consommée par `ff-notifier`. Producteurs :
`ff-ingestion` et `ff-intelligence-engine`.

> Toute évolution de ce contrat doit incrémenter `schema_version` et être
> diffusée aux trois services concernés.

## 1. Routage

```
                 (parse fail / Tier 1 hard fail)
ff-ingestion ──────────────────────────────────► fairfare-box-notifications ──► ff-notifier ──► SES (user)
ff-intelligence-engine ────────────────────────►            ▲
                                                            │
                                          (server error / poison / etc.)
                                                            │
ff-ingestion ───────────────────────────────────────────────┘                                  ──► SES (support)
```

- Une **seule** queue notifications. Chaque event porte sa `category`.
- DLQ AWS associée : `fairfare-box-notifications-dlq` (maxReceiveCount = 5).
- Le notifier choisit l'audience (user vs support) à partir de `recipient.type`
et le rendu HTML/texte à partir de `template_id` + `variables`.

## 2. Enveloppe commune

```json
{
  "schema_version": 1,
  "event_id": "uuid5(NAMESPACE_URL, source_message_id + failure_code)",
  "occurred_at": "2026-05-05T10:00:00Z",
  "service": "ff-ingestion",
  "environment": "prod",
  "category": "user_untreatable | support_alert",
  "severity": "info | warning | error | critical",
  "template_id": "user.untreatable.parse_failed | user.untreatable.tier1_hard | support.server_error | support.poison_message | support.missing_sender",
  "failure_code": "PARSE_FAILED | MISSING_SENDER | EMPTY_BODY | POISON_MESSAGE | OPENAI_UNAVAILABLE | T1_R1_INVALID_ITINERARY | ...",
  "recipient": { "type": "user | support", "email": "user@example.com | null", "locale": "fr | en" },
  "context": {
    "sender": "user@example.com",
    "subject": "Re: votre vol",
    "source_message_id": "<abc@mail.gmail.com>",
    "received_at": "2026-05-05T09:59:50Z",
    "trace_id": "1-abc-...",
    "correlation_id": "fare_event_id_or_uuid5",
    "sqs_source_message_id": "...",
    "receive_count": 4
  },
  "variables": { /* dépend de category — voir §3 et §4 */ }
}
```

`event_id` est déterministe (uuid5) → idempotence native côté notifier.

## 3. `variables` pour `category = user_untreatable`

```json
{
  "user_first_name": null,
  "original_email": {
    "subject": "Re: votre vol",
    "received_at": "2026-05-05T09:59:50Z",
    "snippet": "Bonjour, voici mon billet ..."
  },
  "missing_fields": [
    {
      "code": "T1_R3_CITY_DATE_REQUIRED",
      "path": "itineraries[0].segments[1].departure.iataCode",
      "label": "Code IATA aéroport de départ (segment 2)",
      "expected": "Code IATA à 3 lettres (ex: CDG)",
      "found": null,
      "fix_hint": "Indiquez clairement l'aéroport de départ du second segment."
    }
  ],
  "blocking_rules":     ["T1_R3_CITY_DATE_REQUIRED"],
  "non_blocking_rules": ["T1_R10_TICKETING_DATE"],
  "signals":            ["MISSING_TICKETING_DATE"],
  "human_summary": "Nous n'avons pas pu traiter votre demande : il manque le code IATA et la date d'arrivée du second segment.",
  "next_action": "reply_with_missing_info",
  "support_contact": "support@fairfare.example"
}
```

- `missing_fields[]` est **canonique** : le notifier itère et génère la liste
à puces directement sans logique métier.
- `original_email.snippet` ≤ 200 caractères (PII OK : c'est le contenu de
l'utilisateur lui-même).

## 4. `variables` pour `category = support_alert`

```json
{
  "error": {
    "class": "ParseError",
    "message": "OpenAI returned non-JSON content after 2 retries",
    "module": "infrastructure.parsers.openai_email_parser",
    "file": "src/infrastructure/parsers/openai_email_parser.py",
    "line": 187,
    "function": "_parse_with_retries",
    "stack": "Traceback (most recent call last):\n  File \"...\""
  },
  "occurrence": {
    "trace_id": "1-abc-...",
    "host": "ff-ingestion-pod-7c9",
    "deploy_sha": "9f3c1ab",
    "receive_count": 4,
    "first_seen_at": "2026-05-05T09:50:00Z"
  },
  "source_artifact": {
    "kind": "sqs_message",
    "queue_url": "https://sqs.../fairfare-box-email-ingestion",
    "sqs_message_id": "11111111-...",
    "receipt_handle_redacted": "AQEBLong...redacted",
    "raw_body_excerpt": "{\"Type\":\"Notification\",\"Message\":\"...\"}",
    "ses_message_id": "rkjs...@email.amazonses.com",
    "sender": "user@example.com",
    "subject": "Re: votre vol",
    "size_bytes": 12876
  },
  "runbook_url": "https://wiki.example/runbooks/ingestion/poison_message",
  "human_summary": "Poison message après 4 tentatives — parse OpenAI non-JSON répété"
}
```

Limites SQS (256 KB/message) :

- `error.stack` ≤ 4 KB
- `source_artifact.raw_body_excerpt` ≤ 1 KB
- `error.message` ≤ 1 KB
- Le full stack reste dans CloudWatch (logs structurés).

## 5. Table `failure_code → template_id`


| failure_code                      | category          | template_id                       | producteur             |
| --------------------------------- | ----------------- | --------------------------------- | ---------------------- |
| `PARSE_FAILED`                    | user_untreatable  | `user.untreatable.parse_failed`   | ff-ingestion           |
| `MISSING_SENDER`                  | support_alert     | `support.missing_sender`          | ff-ingestion           |
| `EMPTY_BODY`                      | support_alert     | `support.poison_message`          | ff-ingestion           |
| `POISON_MESSAGE`                  | support_alert (+) | `support.poison_message`          | ff-ingestion           |
| `POISON_MESSAGE` (sender connu)   | user_untreatable  | `user.untreatable.poison_message` | ff-ingestion           |
| `OPENAI_UNAVAILABLE`              | support_alert     | `support.server_error`            | ff-ingestion           |
| `T1_R1_INVALID_ITINERARY`         | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R2_SEGMENTS_REQUIRED`         | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R3_CITY_DATE_REQUIRED`        | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R4_PRICE_REQUIRED`            | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R5_CABIN_REQUIRED`            | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R6_FULL_NAME_REQUIRED`        | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |
| `T1_R8_SEGMENT_SEAT_AVAILABILITY` | user_untreatable  | `user.untreatable.tier1_hard`     | ff-intelligence-engine |


Codes Tier 1 SOFT (`T1_R1_SINGLE_ITINERARY_ROUNDTRIP`, `T1_R8_MISSING_SEAT_DATA`,
`T1_R9_MISSING_OPERATING_DETAILS`, `T1_R10_TICKETING_DATE`) → **pas de
notification** (l'offre est conservée et flaggée). Optionnellement, ils peuvent
apparaître dans `variables.non_blocking_rules` quand un autre code HARD a
déclenché un event.

## 6. Catalogue Tier 1 (source de vérité)

Le catalogue côté Ingestion vit dans
[src/domain/rules/tier1_catalog.py](../src/domain/rules/tier1_catalog.py) et
expose, pour chaque code Tier 1 :

- `kind` (`HARD` / `SOFT`)
- `severity`
- `paths` (JSONPath relatifs au `FareEvent`, `[*]` résolus dynamiquement)
- `label_fr` / `label_en`
- `expected`
- `fix_hint_fr` / `fix_hint_en`

`ff-intelligence-engine` doit produire des `missing_fields[]` au **même
format** (mêmes `code`, mêmes `path` symboliques) afin que le notifier ait un
rendu cohérent quelle que soit l'origine de l'event.

## 7. Idempotence et anti-spam

- `**event_id`** déterministe (`uuid5(URL, source_message_id|failure_code)`).
En FIFO côté SQS, peut servir de `MessageDeduplicationId`.
- **Throttle support** côté Ingestion : 1 alerte / 5 min / `failure_code`
(configurable via `SUPPORT_ALERT_THROTTLE_SECONDS`).
- **User notifications** : pas de throttle ; uuid5 garantit qu'un même couple
(mail source, code) ne génèrera pas un second envoi.

## 8. Kill switch

`NOTIFICATIONS_ENABLED=false` désactive intégralement la publication côté
Ingestion (utile en dev/test sans queue dédiée). Aucune erreur, aucun
side-effect : on log un debug et on retourne.

## 9. Évolution du schéma

1. Ne jamais retirer un champ : déprécier d'abord, retirer en `schema_version+1`.
2. Tout nouveau `failure_code` doit être ajouté simultanément :
  - dans [src/domain/enums/failure_code.py](../src/domain/enums/failure_code.py)
  - dans la table §5 ci-dessus
  - côté ff-notifier (template par défaut au minimum)
3. `MissingField.path` est une convention stable : ne pas renommer sans bump.

