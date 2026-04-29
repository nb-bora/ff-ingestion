## Architecture & prise en main (Ingestion)

### Vue d’ensemble des flux

#### Flux API (`POST /parse`)

1. `presentation.api.ingestion_router.parse_airfare` reçoit `email_body` + `sender?`
2. Si le payload ressemble à un email RFC822, tentative d’extraction `From/Subject/Message-ID/...`
3. Construction d’un `domain.entities.EmailMessage`
4. Appel `application.use_cases.ParseEmailUseCase.execute`
5. Appel OpenAI via `infrastructure.parsers.OpenAIEmailParser`
6. Normalisation en `domain.entities.FareEvent` (avec `ParsingStatus`)
7. Retour HTTP `{ fare_event_id, status="published" }`

Note importante: parité `ff-ingestion` → le chemin API **ne publie pas** de message SQS.

#### Flux SQS Consumer (SES → SNS → SQS)

1. `main.lifespan` démarre `infrastructure.messaging.SQSConsumer` si `CONSUMER_ENABLED=true`
2. Long-poll `receive_message` sur `SQS_EMAIL_QUEUE_URL`
3. Dépaquetage:
   - wrapper SNS (`data["Message"]`)
   - notification SES (`inner["mail"]` + `inner["content"]` base64)
4. Construction d’un `domain.entities.EmailMessage`
5. Appel `application.use_cases.ProcessEmailUseCase.execute`
   - `ParseEmailUseCase.execute` (parse + crée FareEvent)
   - publish via `IMessagePublisher` (impl: `SQSPublisher`)
6. Delete du message source (SQS)

### Dépendances (règles Clean Architecture)

- `presentation` dépend de `application` (use-cases) et de `domain`
- `application` dépend de `domain` + d’interfaces (Protocols) qu’implémente `infrastructure`
- `domain` ne dépend d’aucune couche technique
- `infrastructure` dépend de `application` (interfaces) + `domain` + librairies externes (boto3, openai)

### Observabilité (X-Ray)

- `xray_config.begin_segment/end_segment` autour du traitement d’un message SQS
- `xray_config.subsegment` autour des appels coûteux:
  - extraction OpenAI
  - publication SQS

### Où modifier quoi

- **Contrat HTTP**: `src/presentation/api/ingestion_router.py`
- **Orchestration / règles de parité**: `src/application/use_cases/*.py`
- **Parsing OpenAI / prompts**: `src/infrastructure/parsers/openai_email_parser.py`
- **SQS unwrap SES/SNS**: `src/infrastructure/messaging/sqs_consumer.py`
- **Publication FareEvent**: `src/infrastructure/messaging/sqs_publisher.py`
- **Settings**: `src/config.py`

