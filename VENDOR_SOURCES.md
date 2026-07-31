# Vendor Sources Added

The fresh-news collector now includes vendor/manufacturer sources in addition to media/RSS sources.

## Why

Vendor sites often publish product launches before industry media republishes them. This is especially important for:

- AOI / SPI / AXI inspection systems;
- pick-and-place / placement platforms;
- reflow / soldering / cleaning / materials equipment;
- software and Industry 4.0/MES connectivity updates.

## Vendor RSS feeds added

```text
Saki Vendor          https://www.sakicorp.com/en/feed/
Juki SMT Vendor      https://www.juki.co.jp/smt/en/feed/
Fuji Europe Vendor   https://www.fuji-euro.de/en/feed/
Europlacer Vendor    https://europlacer.com/feed/
Pillarhouse Vendor   https://www.pillarhouse.co.uk/feed/
KYZEN Vendor         https://kyzen.com/news/feed/
```

## Vendor HTML pages added

```text
Koh Young            https://kohyoungamerica.com/category/press-releases/
Koh Young            https://kohyoungamerica.com/news/
TRI                  https://www.tri.com.tw/en/index.aspx
Viscom               https://www.viscom.com/en/company/news/events/
Saki                 https://www.sakicorp.com/en/news/
ViTrox               https://www.vitrox.com/news-and-events/news.php
Creative Electron    https://creativeelectron.com/newsroom/
Yamaha SMT           https://global.yamaha-motor.com/business/smt/news/
Juki SMT             https://www.juki.co.jp/smt/en/news/
ASMPT                https://www.asmpt.com/en/news-center/press-releases/
Fuji Europe          https://www.fuji-euro.de/en/
Essemtec             https://essemtec.com/en/news/
Europlacer           https://europlacer.com/news-hub/
Heller               https://hellerindustries.com/news/
Rehm                 https://www.rehm-group.com/en/news/dates.html
Pillarhouse          https://www.pillarhouse.co.uk/news/
AIM Solder           https://www.aimsolder.com/news/
KYZEN                https://kyzen.com/news/
```

## Configuration

```env
NEWS_VENDOR_SOURCES_ENABLED=1
NEWS_VENDOR_VERIFY_PAGES=0
NEWS_VENDOR_MAX_LINKS=8
NEWS_VENDOR_MAX_ITEMS=2
```

`NEWS_VENDOR_VERIFY_PAGES=0` is intentional by default. Some vendor sites contain page-level current dates or event dates that can be confused with article publication dates. The collector first trusts listing/card dates and RSS dates. Enable page verification only when you need deeper crawling:

```env
NEWS_VENDOR_VERIFY_PAGES=1
```

## Latest test

With vendor sources enabled, the collector found:

```text
Total fresh signals: 83
Vendor-specific fresh signals: 7
Vendor sources with fresh signals:
- Saki Vendor
- Fuji Europe Vendor
- Europlacer Vendor
- AIM Solder
```

Some vendor sites block bot access or had no fresh items inside the 30-day window. They remain in the source list and will work when accessible / when new dated items appear.

## Update 2026-07-11: THT / depaneling / test coverage added

Following a source gap analysis (Apodex deep-research report, independently
URL-verified), 5 vendors were added to the registry:

- **Sciencgo** (https://www.xzg-sciencgo.com/news.html) — THT insertion machines
- **Robotas** (https://www.robotas.com/news/) — THT manual/guided insertion (Mascot, VERIFY)
- **ASYS Group** (https://www.asys-group.com/en/news) — depaneling (DIVISIO product line)
- **Forwessun** (https://forwessun.net/news/) — in-circuit/functional test fixtures, TRI ICT distribution
- **TAGARNO** (https://tagarno.com/news/) — digital microscopy / manual inspection

See docs/SOURCE_REGISTRY.md, "THT scope decision" for what was and wasn't
changed alongside this addition (sources + keyword coverage only, not a new
editorial vertical).
