# TLS Росстата

Предоставленные пользователем 22.10.2026 сертификаты Минцифры / Russian Trusted CA,
сохранённые в PEM. Приватных ключей здесь нет. Официальная страница получения и
обновления: [Госуслуги](https://www.gosuslugi.ru/crt).
Происхождение текущих копий — предоставленные файлы; независимая сверка отпечатков
с опубликованным реестром удостоверяющего центра не выполнялась.

| Файл | Действует до (UTC) | SHA-256 сертификата DER |
|---|---|---|
| Russian_Trusted_Root_CA.pem | 27.02.2032 21:04:15 | `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31` |
| Russian_Trusted_Sub_CA.pem | 06.03.2027 11:25:19 | `bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b` |
| Russian_Trusted_Sub_CA_2024.pem | 19.07.2029 12:50:41 | `2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2` |

`app/ingestion/news/fetch.py` дополняет стандартный HTTPX/certifi trust store этими
сертификатами **только** для exact hosts `rosstat.gov.ru` / `www.rosstat.gov.ru`.
Корневой и оба промежуточных сертификата проверены OpenSSL; hostname verification,
CERT_REQUIRED, сроки действия и построение цепочки остаются включены. Partial-chain
trust специально не включается. Настройка не устанавливает сертификаты в ОС,
не меняет certifi, переменные SSL_CERT_FILE/REQUESTS_CA_BUNDLE, доверие ЦБ или LLM.
Сертификаты входят в существующий Docker build context через `COPY . .`.

Проверка копий из корня репозитория:

```bash
openssl verify -CAfile config/certs/Russian_Trusted_Root_CA.pem config/certs/Russian_Trusted_Root_CA.pem config/certs/Russian_Trusted_Sub_CA.pem config/certs/Russian_Trusted_Sub_CA_2024.pem
openssl x509 -in config/certs/Russian_Trusted_Root_CA.pem -noout -dates -fingerprint -sha256
```

Обновление: получить актуальные CA через официальный канал, проверить издателя,
CA constraints, подпись/цепочку, сроки и отпечатки по независимому официальному
каналу; заменить соответствующие PEM, обновить эту таблицу и fingerprint tests,
перезапустить процесс (SSLContext кешируется), пересобрать образ. Повторить
`make check` и `make news-smoke NEWS_ARGS='--source rosstat --output artifacts/b02/rosstat-import-smoke.json'`
в изолированной `/alfa_test` БД. Ошибки TLS исправлять обновлением проверенной цепочки,
не отключением verify. Автоматической загрузки новых CA / проверки отзыва OCSP/CRL нет.
