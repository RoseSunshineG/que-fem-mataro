# Què fem a Mataró

Tots els plans de Mataró en un sol lloc, en català, castellano, English i français.
Cada esdeveniment enllaça a la seva font original.

**Web:** https://rosesunshineg.github.io/que-fem-mataro/

## Com funciona

- `index.html`: la pàgina. Llegeix `data/events.json`.
- `scripts/update.py`: el robot. Llegeix les agendes públiques i escriu `data/events.json`.
- `.github/workflows/update.yml`: executa el robot cada dia de matinada amb GitHub Actions.
- `data/manual.json`: esdeveniments i correccions afegits a mà.

## Fonts

- Dades obertes de la Diputació de Barcelona (biblioteques, museus, espais escènics), CC BY 4.0
- Cultura Mataró · Agenda d'entitats · Xarxa d'Equipaments Cívics (Ajuntament de Mataró)

Només es mostra un resum curt de cada acte i sempre amb l'enllaç a la font.

## Traducció automàtica (opcional)

Afegeix un secret `DEEPL_API_KEY` (Settings → Secrets and variables → Actions) amb una clau gratuïta de DeepL
i els títols i descripcions nous es traduiran a ES/EN/FR.

---
Un projecte de Rosemarie García.
