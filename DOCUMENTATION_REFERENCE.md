## Documentation de référence (exhaustive) — Ingestion

Ce document sert de “carte” du code: **fichier par fichier**, il décrit chaque
classe/fonction et ses dépendances.

> Convention: “Utilisé par” = appelants (entrypoints), “Utilise” = dépendances directes.

---

### `src/main.py`

- **`lifespan(app)`**
  - **Utilisé par**: `FastAPI(..., lifespan=lifespan)`
  - **Utilise**: `get_sqs_consumer().start/stop`, `settings.consumer_enabled`
  - **Impact**: démarre/arrête le consumer SQS
- **`validation_exception_handler(request, exc)`**
  - **Utilisé par**: pipeline FastAPI
  - **Utilise**: `JSONResponse`
  - **Impact**: force HTTP 400 sur erreurs de validation

---

### `src/presentation/api/ingestion_router.py`

- **`root()`**
  - **Utilisé par**: clients HTTP
  - **Utilise**: `SERVICE_DISPLAY_NAME`, `SERVICE_VERSION`
  - **Impact**: aucun (pure réponse)
- **`health_check()`**
  - **Utilisé par**: ALB/ECS/monitoring
  - **Utilise**: `settings`, `get_metrics()`
  - **Impact**: aucun (lecture métriques)
- **`get_metrics_endpoint()`**
  - **Utilisé par**: debug/monitoring
  - **Utilise**: `get_metrics()`
  - **Impact**: aucun
- **`parse_airfare(request)`**
  - **Utilisé par**: clients HTTP
  - **Utilise**:
    - `looks_like_raw_email`, `parse_eml_bytes`
    - `EmailMessage`, `EmailThreadMetadata`
    - `get_ingestion_service().parse_email_use_case.execute`
  - **Impact**:
    - incrémente `Metrics`
    - **ne publie pas** sur SQS (parité `ff-ingestion`)
  - **Erreurs**: 400 (body vide ou sender absent), 500 (autres)

---

### `src/presentation/api/dependencies.py`

- **`Metrics`**
  - **Utilisé par**: `health_check`, `get_metrics_endpoint`, `parse_airfare`
  - **Impact**: compteur in-memory
- **`get_metrics()`**
  - **Utilise**: `lru_cache`
  - **Impact**: singleton
- **`get_ingestion_service()`**
  - **Utilise**: `OpenAIEmailParser`, `SQSPublisher`, `IngestionService.build`
  - **Impact**: instancie dépendances techniques
- **`get_sqs_consumer()`**
  - **Utilise**: `SQSConsumer(ingestion_service=...)`

---

### `src/application/services/ingestion_service.py`

- **`IngestionService`**
  - **Rôle**: facade qui expose les use-cases
  - **Utilisé par**: `get_ingestion_service`
- **`IngestionService.build(parser, publisher)`**
  - **Utilise**: `ParseEmailUseCase`, `ProcessEmailUseCase`

---

### `src/application/use_cases/parse_email_use_case.py`

- **`ParseEmailUseCase.execute(email)`**
  - **Utilisé par**: API (`/parse`), `ProcessEmailUseCase`
  - **Utilise**: `IEmailParser.parse`, `FareEvent.create`, `_is_valid_extraction`
  - **Impact**: aucun side-effect (pas de publish)
- **`_is_valid_extraction(extracted_travel)`**
  - **Utilisé par**: `ParseEmailUseCase.execute`
  - **Règle**: origin + destination requis (parité `ff-ingestion`)

---

### `src/application/use_cases/process_email_use_case.py`

- **`ProcessEmailUseCase.execute(email)`**
  - **Utilisé par**: `SQSConsumer`
  - **Utilise**: `ParseEmailUseCase.execute`, `IMessagePublisher.publish_fare_event`
  - **Impact**: publication SQS downstream

---

### `src/application/interfaces/email_parser.py`

- **`IEmailParser.parse(email)`**
  - **Implémenté par**: `OpenAIEmailParser`
  - **Utilisé par**: `ParseEmailUseCase`

---

### `src/application/interfaces/message_publisher.py`

- **`IMessagePublisher.publish_fare_event(fare_event)`**
  - **Implémenté par**: `SQSPublisher`
  - **Utilisé par**: `ProcessEmailUseCase`

---

### `src/infrastructure/parsers/openai_email_parser.py`

- **`OpenAIEmailParser.parse(email)`**
  - **Utilisé par**: `ParseEmailUseCase`
  - **Utilise**: OpenAI Chat Completions, `extract_email_body`, `subsegment("parse_with_openai")`
  - **Impact**: appels réseau OpenAI
- **`OpenAIEmailParser._parse_with_openai(email)`**
  - **Utilisé par**: `parse` via `run_in_executor`
  - **Impact**: appels réseau OpenAI
- **`OpenAIEmailParser._failure_reasons(email)`**
  - **Utilisé par**: `parse` quand extraction invalide/incomplète
  - **Impact**: appels réseau OpenAI
- **`_build_system_prompt()`**, **`_next_monday()`**
  - **Utilisé par**: `_parse_with_openai`

---

### `src/infrastructure/messaging/sqs_consumer.py`

- **`SQSConsumer.start()` / `stop()`**
  - **Utilisé par**: `main.lifespan`
  - **Impact**: lance/stop la boucle de polling + ferme threadpool
- **`SQSConsumer._consume_loop()`**
  - **Utilise**: `receive_message` via threadpool
  - **Impact**: traffic AWS SQS
- **`SQSConsumer._process_message_inner(message)`**
  - **Utilise**: `_parse_message_body`, `ProcessEmailUseCase.execute`, `_delete_async`
  - **Impact**: parse + publish + delete
- **`SQSConsumer._parse_message_body(body)`**
  - **Rôle**: unwrap SNS/SES + decode base64
- **`SQSConsumer._delete_async(receipt_handle)`**
  - **Impact**: delete SQS

---

### `src/infrastructure/messaging/sqs_publisher.py`

- **`SQSPublisher.publish_fare_event(fare_event)`**
  - **Utilisé par**: `ProcessEmailUseCase`
  - **Utilise**: boto3 `send_message`, `current_trace_header`
  - **Impact**: publish SQS downstream

---

### `src/infrastructure/aws/xray_config.py` et `src/xray_config.py`

- **`init_xray()`**
  - **Utilisé par**: `main.py`
  - **Impact**: patch boto3 si activé
- **`begin_segment/end_segment/subsegment/xray_capture/current_trace_header`**
  - **Utilisés par**: consumer, router, publisher, parser
  - **Impact**: instrumentation X-Ray

---

### `src/config.py`

- **`Settings`**
  - **Utilisé par**: quasi tout via `settings`
  - **Impact**: peut appeler Secrets Manager si activé
- **`get_secret(...)`**
  - **Utilisé par**: `Settings.resolve_openai_api_key`

---

### `src/logger.py`

- **`JsonFormatter.format(record)`**
  - **Utilisé par**: handler logger
  - **Impact**: logs JSON stdout
- **`build_logger(service_name, environment, log_level)`**
  - **Utilisé par**: init module `logger`
  - **Impact**: installe record factory + handler

---

### `src/shared/utils.py`

- **`looks_like_raw_email(text)`**
  - **Utilisé par**: `extract_email_body`, `parse_airfare`
  - **Impact**: aucune; heuristique pure

---

### `src/shared/email_utils.py`

- **`parse_eml_bytes(eml_bytes)`**
  - **Utilisé par**: `parse_airfare`, `extract_email_body`
- **`extract_email_body(raw_or_plain_text)`**
  - **Utilisé par**: `OpenAIEmailParser`

---

### `src/shared/exceptions.py`

- **`MissingSenderError`**
  - **Utilisé par**: `ParseEmailUseCase`, `ProcessEmailUseCase`, `parse_airfare`, `SQSConsumer`
  - **Impact**: contrôle de flux (400 côté API, suppression côté consumer)

---

### `src/domain/*`

- **`EmailMessage`**
  - **Utilisé par**: router API, consumer SQS
- **`EmailThreadMetadata`**
  - **Utilisé par**: API (EML), SQS unwrap SES
- **`FareEvent`**
  - **Utilisé par**: use-cases, publisher
- **`ParsingStatus`**
  - **Utilisé par**: `ParseEmailUseCase`
- **`TravelExtract`**
  - **Utilisé par**: validation/typing (alignement extraction OpenAI)

