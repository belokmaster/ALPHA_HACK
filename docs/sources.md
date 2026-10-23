# Источники, 21.10.2026

Пути относительно корня repo (manifest root = `.`); машинный manifest: `config/inputs.manifest.json`. В `docs/references/` хранится только официальный Q&A организаторов. Актуальные требования и решения сведены в [план](plan.md), [решения](decisions.md), [матрицу приёмки](requirements.md) и [разбор Q&A](official-qa-impact.md); старые планы, bootstrap-задание, транскрипция и пересказы не хранятся отдельными файлами. Номера строк и SHA прежней транскрипции в плане и решениях оставлены как историческое происхождение уже сведённых уточнений, а не ссылки на файл в репозитории. Raw CSV/PDF размещаются локально в `raw/csv/` и `raw/pdf/`, не входят в Git и Docker context; подготовка — [локальные материалы](local-inputs.md). Секреты не читались. Сканированные аналитические PDF только инвентаризированы; OCR и таблицы заново не проверены. Steel 2021 не доказывает наличие отдельного Commodities performance.

| Путь | SHA-256 | Тип | Проверка |
|---|---|---|---|
| `raw/pdf/Альфа Хак_Новостная аналитика.pdf` | `e9dcd7e0f68ff8664e2fcc5b3124632acb7accc671a29fcdd971daf6353143ef` | pdf | text_extracted_and_read |
| `raw/pdf/ALFA-7M26.pdf` | `3c64c1e43f3b46a795f9581967f2dbc8efb0994cb77ab82e95da180830e9ff8c` | pdf | inventoried; OCR not repeated |
| `raw/pdf/ALFA-Macro_Insights-Policy_Rate_11Sep.pdf` | `92a96805f2b720f76bd0c52d2491044812b38401ab62d0f4a9157b4ca81c49bb` | pdf | inventoried; OCR not repeated |
| `raw/pdf/ALFA-consumption.pdf` | `807ee9925cf7d15ecffc72ad7142f9a83d342cd19690e12dd3b90aa2ba8ea1c6` | pdf | inventoried; OCR not repeated |
| `raw/pdf/ALFA-macro_stat1H26.pdf` | `84899553fadf3e49abac7074c7b7d2afc2fa5c70b7fde0bf4c3eabe3d7402266` | pdf | inventoried; OCR not repeated |
| `raw/pdf/ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf` | `36eaaa58ae68d858db3600dbf3cc93d0bdefb8ca03f1f5cc154c532cc32e0c86` | pdf | inventoried; OCR not repeated |
| `raw/pdf/FI_Strategy_3Q26_22072026.pdf` | `6c3a4882ee784b608880693d8ba2b62d0ba2236e34b404179adaab909c6dd046` | pdf | inventoried; OCR not repeated |
| `raw/pdf/Gold_digest_August26.pdf` | `4159576a6223985dd318b6f5a04ad4aeb888f47a6c266d0e9ee9eb35bf01e245` | pdf | inventoried; OCR not repeated |
| `raw/pdf/Inflation_tracker_02.pdf` | `09d5537f6a52f5f8f2d9518ed9171e794382972ce38cbdc4bd3099b2db129d0c` | pdf | inventoried; OCR not repeated |
| `raw/pdf/Бонд_Бриф_14092026.pdf` | `87dc17520e2a744f95580ee2ec42461d0ee6ebf19e752622a49c49600cb1b055` | pdf | inventoried; OCR not repeated |
| `raw/csv/df_Fid_Uid_link_hack.csv` | `3d9c718e2a87304f48bbc7447607116bb8e4bd61dded70b0a1d0558f086a1b58` | csv | audited: 12514 rows; not imported |
| `raw/csv/df_Fid_Uid_link_hack_manualcrop_500_of_12515_rows.csv` | `6d058b5150c34b6f085b2f3377640496593c284cade37fafd1f0b0104271ede7` | csv | discovered; audit pending |
| `raw/csv/df_Uid_data_hack.csv` | `cb48983806ce7ca0ae045a40c9627ccbe47cc72bfb54058db04df2386da4a983` | csv | audited: 85525 rows; not imported |
| `raw/csv/df_Uid_data_hack_manualcrop_500_of_85526_rows.csv` | `e04332989ab9a4c581e7eb9a49c093ce80fd9077c7231e1c5fa94041d94279e7` | csv | discovered; audit pending |
| `raw/csv/df_Uid_payment_hack.csv` | `30095f75bead1a1dac0d7232b0da09a326b7ef643ee3b77b36ad3b4458a1cab3` | csv | audited: 1380585 rows; not imported |
| `raw/csv/df_Uid_payment_hack_manualcrop_500_of_1380586_rows.csv` | `c60c9ae678520548ba4dca2402c429be5008fe50ff76edd914c8cce9ea2c9a45` | csv | discovered; audit pending |
| `raw/pdf/Новостная_аналитика_Инструкция_по_ключам.pdf` | `f2ed65679b2931d78ef9655a36d31386a91ca6a14bf0b0d14f9a1f66db06d6e3` | pdf | text_extracted_and_read |

Новый CSV аудит: [input-audit-summary.md](input-audit-summary.md); полные файлы измерены отдельно, manualcrop варианты только инвентаризированы.

Официальный Q&A: `docs/references/Вопросы с вебинара _ Новостная лента.md`, SHA-256 `432723aa4c364995f47156c04223727746f05ed2cfa6d7ecfe052cef03c50992`; полностью прочитан, номера 1–74.
Это дополнительный первоисточник, а не прежний Q&A-пересказ вебинара.
