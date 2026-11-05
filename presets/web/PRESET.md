# Preset: web

## Passt für
Browser-Apps und Frontends in TypeScript: SPA mit Vite (Standard), optional Next.js für SSR/Fullstack.
Nicht für reine Node-CLIs/Server ohne Frontend (dann Vite weglassen, `tsx`/`tsc` direkt).

## Voraussetzungen
**Windows 11:**
- Node LTS: `winget install OpenJS.NodeJS.LTS` (Stand 11/2026: 26.x; Gerüst und CI nutzen 24, `engines` verlangt ≥ 24).
- pnpm: Version ist über `"packageManager": "pnpm@11.28.9"` in `package.json` gepinnt.
  - Node 24: `corepack enable` (Admin-Shell, da Node unter `C:\Program Files` liegt).
  - Node ≥ 25 bringt kein Corepack mehr mit: `npm install -g corepack` und dann `corepack enable`, oder `winget install pnpm.pnpm`
    (ein global installiertes pnpm wechselt selbst auf die Version aus `packageManager`).
- Prüfen: `node -v`, `pnpm -v`.
- Optional E2E: `pnpm add -D @playwright/test` und dann `pnpm exec playwright install chromium`.

**Linux-CI:** `pnpm/action-setup` (liest `packageManager`) + `actions/setup-node` mit `cache: pnpm`, siehe `ci.yml`.

Versionen (verifiziert 11/2026): Vite 8, Vitest 5, ESLint 10, typescript-eslint 8, Prettier 3, TypeScript **6.0**
(TS 7 ist erschienen, wird aber von typescript-eslint 8 noch nicht unterstützt: Peer-Abhängigkeit `<6.1`).

## Befehle für CLAUDE.md
Stack: `TypeScript 6 (strict) / Vite 8 / Vitest / Node 24 LTS / pnpm 11`

| Zweck | Befehl |
|---|---|
| Build | `pnpm install && pnpm build` (= `tsc` Typecheck + `vite build` → `dist/`) |
| Test (Host) | `pnpm test` (Vitest einmalig; `pnpm test:watch` für Watch) |
| Lint/Format | `pnpm lint` (ESLint + `prettier --check`); Fixen: `pnpm format` |
| Flash / Run | `pnpm dev` (Dev-Server http://localhost:5173); Build ansehen: `pnpm preview` – kein Flash; Server laufen endlos → Claude nur im Hintergrund/Preview-Tool oder mit Timeout |
| Monitor / Logs | Browser-DevTools (Konsole/Netzwerk); Dev-Server-Ausgabe im Terminal – interaktiv: der Nutzer schaut hin, Claude liest Logs nur aus Hintergrundprozess/Preview |

Hinweis: `&&` funktioniert in Git Bash und PowerShell 7, nicht in Windows PowerShell 5.1 (dort `;`).

**Variante Next.js:** Build `pnpm build` (= `next build`), Run `pnpm dev` (= `next dev`, Port 3000), Prod lokal `pnpm start`,
Lint `eslint .` mit `eslint-config-next` (`next lint` ist entfallen). Vitest bleibt für Unit-Tests; `vite.config.ts` entfällt
(dafür `vitest.config.ts`), `index.html`/`src/main.ts` durch `app/` ersetzen. Gerüst dann mit `pnpm create next-app` erzeugen.

## Projektgerüst
```
package.json        Scripts, packageManager-Pin, devDependencies
tsconfig.json       strict, moduleResolution "bundler", noEmit (Vite transpiliert, tsc prüft nur)
vite.config.ts      Vite + Vitest-Konfiguration (Abschnitt `test`)
eslint.config.js    Flat Config: @eslint/js + typescript-eslint recommended
.prettierrc         Prettier-Optionen (tabWidth 2 gegen .editorconfig, endOfLine auto wegen CRLF)
.prettierignore     dist/, Lockfile, *.md, .github/, .claude/ (Doku gehört nicht Prettier)
index.html          Einstieg, lädt /src/main.ts
src/main.ts         DOM-Anbindung (dünn halten)
src/lib/core.ts     reine Logik, ohne DOM → per Unit-Test prüfbar
src/lib/core.test.ts
```
Alles liegt in `presets/web/geruest/` und kommt 1:1 ins Projekt-Root. `pnpm-lock.yaml` entsteht beim ersten
`pnpm install` und wird committet.

## Teststrategie
- **Unit (Host):** Vitest, Testdateien `*.test.ts` neben dem Code. Logik in `src/lib/` ohne DOM halten → Umgebung `node`.
  DOM-nahe Tests: `jsdom` oder `happy-dom` als devDependency und `environment` im Test umstellen.
- **Integration:** Vitest mit gemockten Netzwerkaufrufen (`vi.fn`, `vi.mock`) oder MSW.
- **E2E (optional):** Playwright (`@playwright/test`, Script `test:e2e`), testet gegen `pnpm preview`; in CI nur Chromium.
- **Manuell:** Optik/Layout, echte Browser-Varianten (Safari/Mobil), Barrierefreiheit über Lighthouse hinaus,
  Verhalten gegen das echte Backend → als **offen – manuell prüfen** benennen.

## CI
`ci.yml`: ubuntu-latest, pnpm aus `packageManager`, Node 24, Cache des pnpm-Stores über `setup-node` (`cache: pnpm`,
Schlüssel = Hash von `pnpm-lock.yaml`). Schritte: `pnpm install --frozen-lockfile` → `lint` → `build` → `test`.
Grenzen: nur Linux (keine Windows-Pfadfehler sichtbar), kein Deployment, E2E nur als auskommentierter Block
(Playwright-Browser-Download kostet ~1 min). Ohne committeten Lockfile schlägt `--frozen-lockfile` fehl – gewollt.

## Typische Fallstricke
### node_modules im Dropbox-Ordner #dropbox #pnpm #windows
*Preset web · Quelle: Erfahrung*
- **Symptom:** `pnpm install` dauert Minuten, Dropbox synchronisiert zehntausende Dateien, `EPERM`/`EBUSY` beim Install, Konfliktkopien.
- **Ursache:** Dropbox überwacht und sperrt Dateien in `node_modules/` (und `dist/`) während Build/Install.
- **Lösung / Regel:** Ordner von Dropbox ausnehmen, bevor sie entstehen (PowerShell):
  `Set-Content -Path node_modules -Stream com.dropbox.ignored -Value 1` (Ordner vorher leer anlegen; ebenso `dist`; nach Löschen neu setzen). Alternativ Repo außerhalb von Dropbox klonen.

### Lockfile nicht committet oder veraltet #pnpm #ci
*Preset web · Quelle: Erfahrung*
- **Symptom:** CI bricht mit `ERR_PNPM_OUTDATED_LOCKFILE` bzw. fehlendem Lockfile ab; lokal und CI haben andere Versionen.
- **Ursache:** `pnpm-lock.yaml` fehlt im Commit oder `package.json` wurde ohne `pnpm install` geändert.
- **Lösung / Regel:** `pnpm-lock.yaml` immer mitcommitten; Abhängigkeiten nur per `pnpm add`/`pnpm remove` ändern.

### Frisch veröffentlichte Pakete werden abgelehnt #pnpm #supply-chain
*Preset web · Quelle: Gerüst-Test 2026-11*
- **Symptom:** `ERR_PNPM_MINIMUM_RELEASE_AGE_VIOLATION`, oder pnpm trägt ungefragt `minimumReleaseAgeExclude` in `pnpm-workspace.yaml` ein.
- **Ursache:** pnpm 11 installiert standardmäßig keine Versionen, die jünger als 1 Tag sind (Schutz vor kompromittierten Releases).
- **Lösung / Regel:** Versionsbereiche nicht auf die allerneueste Version setzen (`^8.3.0` statt `^8.3.6`); Ausnahmen nur bewusst.

### ESM/CJS-Verwechslung #node #esm
*Preset web · Quelle: Erfahrung*
- **Symptom:** `require is not defined in ES module scope`, `Cannot use import statement outside a module`, oder `ERR_REQUIRE_ESM`.
- **Ursache:** `"type": "module"` macht jede `.js`-Datei zu ESM; Konfigs/Skripte im CJS-Stil (`module.exports`, `__dirname`) brechen.
- **Lösung / Regel:** Durchgehend ESM. CJS-Dateien `.cjs` nennen; statt `__dirname` → `import.meta.dirname`.

### TypeScript-Major schneller als typescript-eslint #typescript #eslint
*Preset web · Quelle: Gerüst-Test 2026-11*
- **Symptom:** Peer-Dependency-Warnung, Parser-Fehler oder Warnung „unsupported TypeScript version" nach `pnpm up --latest`.
- **Ursache:** typescript-eslint unterstützt neue TS-Majors erst verzögert (8.x: `typescript <6.1`).
- **Lösung / Regel:** `typescript` mit `~` pinnen; Major-Updates nur gemeinsam mit typescript-eslint.

### pnpm blockiert Build-Skripte von Abhängigkeiten #pnpm
*Preset web · Quelle: Erfahrung*
- **Symptom:** pnpm 11: `pnpm install` bricht mit Fehler zu ignorierten Build-Skripten ab (pnpm 10: nur Warnung, später fehlen native Binaries, z. B. `sharp`, Prisma).
- **Ursache:** Seit pnpm 10 laufen `postinstall`-Skripte nur für freigegebene Pakete; pnpm 11 setzt `strictDepBuilds` standardmäßig auf `true`.
- **Lösung / Regel:** `pnpm approve-builds` ausführen; die Freigabe (`allowBuilds`) landet in `pnpm-workspace.yaml` → mitcommitten.

### `prettier --check` meldet jede Datei unter Windows #prettier #windows
*Preset web · Quelle: Gerüst-Review 2026-11*
- **Symptom:** `pnpm lint` lokal rot („Code style issues"), in CI grün; oder `index.html`/JSON mit 4er-Einrückung bemängelt.
- **Ursache:** Git für Windows checkt mit `core.autocrlf=true` CRLF aus, Prettier erwartet LF; Prettier liest außerdem `.editorconfig` (`indent_size = 4` für `[*]`).
- **Lösung / Regel:** In `.prettierrc` `"endOfLine": "auto"` und `"tabWidth": 2` setzen (im Gerüst so enthalten).

## ADR-Kandidaten
- Framework: Vite-SPA vs. Next.js (SSR/Fullstack) vs. Astro (überwiegend statisch).
- UI-Bibliothek: Vanilla TS vs. React vs. Vue vs. Svelte.
- Lint-Strenge: typescript-eslint `recommended` vs. `strictTypeChecked` (typbasiert, langsamer, findet mehr).
- E2E-Tests: keine vs. Playwright (nur Chromium vs. alle Browser).
- Hosting/Deploy: GitHub Pages vs. Vercel/Netlify vs. eigener Server/Container.
- Styling: plain CSS/CSS Modules vs. Tailwind vs. Komponentenbibliothek.

## Anwendung
1. Repo möglichst außerhalb von Dropbox anlegen; sonst `node_modules` und `dist` vorab von Dropbox ausnehmen (siehe Fallstrick).
2. `presets/web/ci.yml` → `.github/workflows/ci.yml` kopieren.
3. Inhalt von `presets/web/gitignore` an `.gitignore` anhängen.
4. Inhalt von `presets/web/geruest/` (inkl. `.prettierrc`, `.prettierignore`) ins Projekt-Root kopieren; `"name"` in `package.json`
   und `<title>` in `index.html` auf den Projektnamen setzen.
5. Bei Next.js-Variante: statt Schritt 4 `pnpm create next-app`, dann Vitest + Prettier ergänzen und Befehle anpassen.
6. Tabelle und Stack-Zeile aus „Befehle für CLAUDE.md" in `CLAUDE.md` übernehmen.
7. Fallstricke oben nach `docs/LEARNINGS.md` (Abschnitt „Toolchain & Build") kopieren, Datum einsetzen.
8. Verifizieren: `pnpm install`, `pnpm build`, `pnpm test`, `pnpm lint` – alle grün; Ausgabe zitieren.
   Dann `pnpm-lock.yaml` mit dem Gerüst committen.
