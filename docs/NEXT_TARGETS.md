# Next Decompilation Targets

Candidates for ground-up decompilation work. Sorted by unmatched function count.
Filtered to 8+ unmatched stubs. Activity = upstream commits in last 30 days.

## Selection Criteria

- **Best targets**: High stub count, zero or low recent activity, familiar module
- **Avoid**: Files with 3+ recent commits (active contributors = merge conflict risk)
- **Ideal**: Same module patterns we already know (mn/, gm/, gr/)

## Top Picks (Zero Activity, Big Work)

| File | Stubs | Size | Module | Activity | Notes |
|------|-------|------|--------|----------|-------|
| **mn/mnsnap.c** | 11 unmatched | 92 KB | mn | **0** | Familiar module, existing scaffold, 8 already matched |
| **ft/ftPr_SpecialN.c** | 36 | 10 KB | ft | **0** | Purin (Jigglypuff) special moves |
| **ft/ftCo_0A01.c** | 34 | 135 KB | ft | **0** | Common fighter code, massive |
| **mn/mnmainrule.c** | 14 | 9 KB | mn | **0** | Menus, small |
| **mn/mncharsel.c** | 9 | 34 KB | mn | **0** | Character select screen |
| **gr/grzebes.c** | 26 | 5 KB | gr | **0** | Zebes stage, compact |
| **gm/gmresultplayer.c** | 15 | 10 KB | gm | **0** | Results screen |
| **gm/gm_16F1.c** | 11 | 29 KB | gm | **0** | Game manager |
| **gr/gronett.c** | 11 | 6 KB | gr | **0** | Onett stage |
| **it/itpatapata.c** | 11 | 5 KB | it | **0** | Paratroopa item |
| **ty/tyfigupon.c** | 13 | 6 KB | ty | **0** | Trophy/figure |
| **gm/gmtou.c** | 8 | 10 KB | gm | **0** | Tournament mode |
| **gr/grgreens.c** | 8 | 16 KB | gr | **0** | Green Greens stage |
| **gr/groldpupupu.c** | 9 | 4 KB | gr | **0** | Past Dreamland stage |
| **gr/grkinokoroute.c** | 9 | 3 KB | gr | **0** | Mushroom Kingdom stage |
| **it/itkamex.c** | 8 | 4 KB | it | **0** | Blastoise item |
| **it/itnesspkflush.c** | 8 | 3 KB | it | **0** | Ness PK Flash |
| **gr/groldkongo.c** | 8 | 3 KB | gr | **0** | Past Kongo Jungle |
| **it/itseakneedlethrown.c** | 17 | 2 KB | it | **0** | Sheik needles |

## Moderate Activity (1-2 recent commits, probably safe)

| File | Stubs | Size | Module | Activity | Notes |
|------|-------|------|--------|----------|-------|
| **gm/gm_18A5.c** | 47 | 65 KB | gm | 2 | Huge target, game manager |
| **gr/grkongo.c** | 44 | 40 KB | gr | 1 | Kongo Jungle, lots of work |
| **gr/gricemt.c** | 39 | 35 KB | gr | 2 | Ice Mountain stage |
| **gm/gmregclear.c** | 32 | 31 KB | gm | 1 | Region clear screen |
| **gm/gm_1832.c** | 32 | 15 KB | gm | 1 | Game manager |
| **ty/tydisplay.c** | 25 | 2 KB | ty | 1 | Trophy display (tiny file, many stubs) |
| **it/itsamusgrapple.c** | 25 | 6 KB | it | 1 | Samus grapple beam |
| **mn/mndatadel.c** | 10 | 5 KB | mn | 1 | Data delete menu |

## Avoid (Active Contributors, 3+ commits)

| File | Stubs | Activity | Notes |
|------|-------|----------|-------|
| ft/ftKb_SpecialN.c | 39 | 6 | Kirby copy abilities, very active |
| ft/ftKb_SpecialNPk.c | 37 | 4 | Kirby copy, active |
| ft/ftKb_Init.c | 20 | 5 | Kirby init, active |
| ft/ftKb_SpecialNZd.c | 19 | 5 | Kirby copy, active |
| ft/ftKb_SpecialNYs.c | 14 | 7 | Kirby copy, very active |
| gm/gm_1601.c | 28 | 8 | Very active |
| gr/grcorneria.c | 30 | 3 | Active |
| gr/grcastle.c | 29 | 3 | Active |
| lb/lbaudio_ax.c | 22 | 3 | Active |
| ty/toy.c | 14 | 4 | Active |
| ft/ftCo_Attack100.c | 11 | 6 | Very active |
| mn/mnname.c | 25 | 2 | Someone just committed (2026-03-10) |

## mn/ Module Specific (Our Home Turf)

| File | Status | Stubs | Size | Activity |
|------|--------|-------|------|----------|
| mnsnap.c | 8/19 matched | 11 | 92 KB | **0** |
| mnmainrule.c | 2/17 | 14 | 9 KB | **0** |
| mncharsel.c | 11/25 | 9 | 34 KB | **0** |
| mndatadel.c | 1/13 | 10 | 5 KB | 1 |
| mnevent.c | 0/14 | 14 | stub | **0** |
| mnitemsw.c | 0/10 | 10 | stub | **0** |
| mnsound.c | 1/4 | 3 | 9 KB | **0** |
| mnstagesel.c | 13/14 | 1 | 29 KB | 2 |

---

*Generated 2026-03-10. Activity window: last 30 days of upstream/master.*
