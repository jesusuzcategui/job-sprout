# Job Sprout — Session Memory (2026-06-04)

## Goal
Build & release PyQt6 desktop job scraper (Computrabajo, El Empleo, Indeed, LinkedIn) with AI scoring, geo filters, GitHub Actions CI/CD para Windows/Linux/macOS.

## Constraints
- Python 3.14 + venv (`.venv/`); Playwright; PyQt6; SQLite
- Remote: `git@github-personal:jesusuzcategui/job-sprout.git`
- DB auto-init en cmd_gui/cmd_cli/cmd_cli_scraper (ya no requiere `--init-db` manual)
- Playwright Chromium se auto-instala en primer arranque (~300MB, vía `playwright.__main__.main()`)

## Branding
- **Nombre**: Job Sprout (antes py-find-jobs)
- **App icon**: `images/logo-cuadrado-app.png` (512x536)
- **Banner UI**: `images/logo-horizontal-app.png` (221x48)

## State (commits existentes en master, sin push aun)
```
bb22f9d fix: create vacantes table before indexes and migration
84be878 fix: use playwright internal API for browser install, auto-init db
ea2f44c feat: add auto browser install and update CI builds
4054044 fix: move fail-fast inside strategy block
8dad0c4 fix: add Pillow for macOS icon, fail-fast: false, no --windowed on Linux
c37d7e8 feat: add bundled playwright chromium and release workflow
f1a8d1d feat: add full Job Sprout job scraping agent project
```

## Build & Release (.github/workflows/build-release.yml)
| Platform | Format | Archivo |
|---|---|---|
| Linux | AppImage | `JobSprout-linux-x64.AppImage` |
| Windows | .exe (onefile) | `JobSprout-windows-x64.exe` |
| macOS | .dmg | `JobSprout-macos-x64.dmg` |
- **Trigger**: push tag `v*`
- **fail-fast**: false (cada SO independiente)

### Linux AppImage
- PyInstaller `--onedir` → AppDir → `appimagetool` (extracted, sin FUSE)
- Incluye todas las shared libs (portable cross-distro: Arch, Fedora, Ubuntu…)
- Requiere `squashfs-tools` + `desktop-file-utils` en runner
- NO se necesita instalar `libegl1 libxcb-cursor0` en el runner (las empaqueta el AppImage)

### Windows
- PyInstaller `--onefile` → `.exe` directo

### macOS
- PyInstaller `--onedir --windowed` → `JobSprout.app` → `hdiutil` → `.dmg`

## Database
- `data/py_find_jobs.db` auto-creado con `init_db()` en cmd_gui/cmd_cli
- Schema: perfiles, keywords, ejecuciones, settings, vacantes
- **Bug fix**: índices de vacantes movidos a SCHEMA_VACANTES_STATEMENTS (antes creaban en tabla inexistente)
- **Bug fix**: `_migrate_vacantes()` corre después de crear la tabla

## Browser auto-install (`ensure_playwright_browser()`)
- Usa `playwright.__main__.main()` directamente (no subprocess a sys.executable)
- Funciona dentro de PyInstaller bundle (no necesita Python externo)
- Se ejecuta en cmd_gui, cmd_cli, cmd_cli_scraper

## Issues pendientes (sin probar en distros reales)
1. Linux AppImage — no probado en Arch/CachyOS (el fix está puesto, testear)
2. macOS — error sin diagnosticar (usuario dijo "da errores", pegar el log)
3. Windows — no probado aun

## Proximo paso
1. Push commits + tag v0.1.0
2. Esperar build en Actions (ver logs si AppImage se genera bien)
3. Descargar y probar en Arch/CachyOS
4. Pedir log de macOS para debuggear
5. Si AppImage falla: verificar `appimagetool` URL o usar `linuxdeploy`
