# Entscheidungen

<!-- Eine Zeile pro ADR. Wird in jede Session geladen – knapp halten.
     Bindend sind nur ADRs mit Status „angenommen". Abgelöste bleiben gelistet, mit Verweis auf den Nachfolger. -->

| ADR | Entscheidung | Status |
|---|---|---|
| [0001](0001-entscheidungen-dokumentieren.md) | Tragende Entscheidungen als ADR festhalten; nur per neuem ADR revidieren | angenommen |
| [0002](0002-stack-python-uv.md) | Python 3.12 mit uv, pytest, ruff und mypy strict im src-Layout | angenommen |
| [0003](0003-arena-module-uebernehmen.md) | Arena-Module mit Herkunftsvermerk übernehmen statt die Arena einzubinden | angenommen |
| [0004](0004-paper-zuerst-echtgeld-gesperrt.md) | Paper-Modus zuerst; Echtgeld gesperrt bis zu eigener Spec und eigenem ADR | angenommen |
| [0005](0005-betrieb-hetzner-cloud.md) | Betrieb auf Hetzner Cloud CX23 mit Ubuntu und systemd; Status nur per SSH-Tunnel | abgelöst durch [0024](0024-weboberflaeche-oeffentlich-https.md) |
| [0006](0006-server-zugang-benutzer.md) | Admin-Benutzer mit Key und sudo NOPASSWD; Dienst unter eigenem Systembenutzer | angenommen |
| [0007](0007-strategien-identisch-zur-arena.md) | Übernommene Strategien identisch zu Arena v0.3.1; Änderungen nur als neue Version | angenommen |
| [0008](0008-paper-schema-ein-agent.md) | Eigenes versioniertes SQLite-Schema; ein aktiver Agent plus Buy & Hold | abgelöst durch [0010](0010-mehrere-llm-depots.md) |
| [0009](0009-llm-agent-openai-budget.md) | LLM-Agent über OpenAI (gpt-6.1-sol, openai-SDK); hartes EUR-Tagesbudget und Provenienz in der DB | abgelöst durch [0011](0011-gpt-gemeinsame-anfrage.md) |
| [0010](0010-mehrere-llm-depots.md) | Drei GPT-Depots (sol, terra, luna) zum Modellvergleich plus Buy & Hold; Schema bleibt versioniert (löst 0008 ab) | abgelöst durch [0012](0012-luna-haupt-sol-gegenprobe.md) |
| [0011](0011-gpt-gemeinsame-anfrage.md) | Eine GPT-Anfrage für BTC+ETH mit Prompt v1, 4/h + ≤ 4 Ereignis/h, Effort low; Budget je Agent in der DB (löst 0009 ab) | abgelöst durch [0013](0013-gpt-abfragen-nur-bei-bedarf.md) |
| [0012](0012-luna-haupt-sol-gegenprobe.md) | Zwei GPT-Depots: gpt-6-luna Haupt-Trader, gpt-6.1-sol Gegenprobe; Anteile 0,50 / 2,50 € von 3 € (löst 0010 ab) | angenommen |
| [0013](0013-gpt-abfragen-nur-bei-bedarf.md) | GPT nur bei Bedarf: stündlich, bei ≥ 1,8 % Bewegung oder Wechsel eines Referenzsignals, max. 4/h; sonst wie 0011 (löst 0011 ab) | angenommen |
| [0014](0014-weboberflaeche-zugangsdaten.md) | Weboberfläche für Zugangsdaten nur auf 127.0.0.1, Passwort-Login mit Einrichtungscode, secrets.env (600) außerhalb des Repos | abgelöst durch [0015](0015-weboberflaeche-passwort-8.md) |
| [0015](0015-weboberflaeche-passwort-8.md) | Weboberfläche wie 0014, Passwort mindestens 8 Zeichen (löst 0014 ab) | angenommen |
| [0016](0016-weboberflaeche-liest-bot-db.md) | Weboberfläche liest die Bot-DB nur lesend (`mode=ro`, `query_only`); genau ein Schreiber | angenommen |
| [0017](0017-dienst-eine-runde-pro-minute.md) | Dienst: ein Prozess, eine Paper-Runde pro Minute, systemd-Watchdog + Statusdatei, Config je Runde | angenommen |
| [0018](0018-deploy-releases-backup-rollback.md) | Deploy als Release je Commit per SSH mit automatischem Code-Rollback; DB-Backups täglich nur auf dem Server, 14 Tage | abgelöst durch [0020](0020-deploy-gesund-trotz-marktdaten-luecke.md) |
| [0019](0019-uebersicht-live-eigenes-skript.md) | Übersicht live über ein eigenes Skript `/app.js` (`script-src 'self'`); Server rendert, Skript tauscht aus | angenommen |
| [0020](0020-deploy-gesund-trotz-marktdaten-luecke.md) | Deploy wie 0018; gesund ist auch eine Runde, die nur an Marktdaten scheitert (löst 0018 ab) | angenommen |
| [0021](0021-risiko-grenzen-m1.md) | Risiko in M1: Drawdown-Stopp 10 % wie Arena, Freigabe und Handelspause über die Weboberfläche, Kurse je Markt | angenommen |
| [0022](0022-broker-naht-m1.md) | Broker-Naht in M1: Protokoll plus Paper-Delegation, Sperren als Tests, keine Laufzeitänderung | angenommen |
| [0023](0023-live-ausfuehrung-m2-vorschlag.md) | Live-Ausführung für M2: zwei Phasen, nie blind wiederholen, getrennte Historie (Vorschlag bis M2) | vorgeschlagen |
| [0024](0024-weboberflaeche-oeffentlich-https.md) | Weboberfläche öffentlich per HTTPS hinter Caddy mit gehärtetem Login; App bleibt auf 127.0.0.1 (löst 0005 ab) | abgelöst durch [0028](0028-uebersicht-ohne-login.md) |
| [0025](0025-rechtstexte.md) | Impressum nur auf dem Server, Datenschutzerklärung im Repo an den Code gebunden; Log-Fristen auf dem Server durchgesetzt | angenommen |
| [0026](0026-strategie-labor.md) | Strategie-Labor auf dem PC: unveränderter Live-Code im Backtest, Treue-Test, Prüfregister, versiegelter Prüfzeitraum ab 2025-10-01, GPT nur live bewertet | abgelöst durch [0029](0029-strategie-labor-ausfuehrungsvarianten.md) |
| [0027](0027-beobachtungs-db.md) | Zusätzliche Live-Aufzeichnung (Quotes mit Tiefe, GPT-Rohantworten, Auslöser, Version, Ausfälle) in eigener `observations.db`; `bot.db` bleibt v2 | angenommen |
| [0028](0028-uebersicht-ohne-login.md) | Weboberfläche wie 0024; Übersicht ohne Login lesbar (ohne Fehlerdetails, 10-s-Zwischenspeicher, noindex, abschaltbar), nur im Paper-Modus (löst 0024 ab) | angenommen |
| [0029](0029-strategie-labor-ausfuehrungsvarianten.md) | Strategie-Labor wie 0026; Ausführungsvarianten ohne Regeländerung (z. B. USDC) als eigene Paper-Depots ohne Hürden nach Freigabe (löst 0026 ab) | angenommen |
| [0030](0030-usdc-depots.md) | USDC-Depots v0.3.1-usdc: Signale aus BTC/EUR, BTC über BTC/USDC ausgeführt, Geld in Euro mit Umrechnung je Trade | angenommen |
