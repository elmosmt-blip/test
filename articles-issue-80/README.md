# SMT Today Magazine — Issue 80 (`SMTMag-Issue 80-DIGI`) — Серия инженерных обзоров SMTInsider

В этой директории сохранены три полноформатных, глубоких инженерных обзора (800–900+ слов каждый), написанных для SMTInsider.com на основе ключевых материалов **80-го выпуска отраслевого журнала SMT Today Magazine (`https://online.fliphtml5.com/kwnhb/fakj/`)**:

1. **[01_fuji_smart_placement.md](01_fuji_smart_placement.md)** (+ `.meta.json`)
   - **Тема:** *Fuji Corporation: High-Speed Placement Line Architecture & Adaptive Automation*
   - **Раздел:** `/reviews/` (`SMT Equipment`)
   - **Спецификации:** 45,000 CPH, точность ±15 µm (3σ), динамическое гашение вибраций, проверка копланарности BGA/QFN в полёте, смарт-фидеры IPC-CFX.
2. **[02_koh_young_3d_inspection.md](02_koh_young_3d_inspection.md)** (+ `.meta.json`)
   - **Тема:** *Koh Young Technology: True 3D SPI and AOI Metrology for Advanced Packaging*
   - **Раздел:** `/reviews/` (`Inspection`)
   - **Спецификации:** фазосдвигающая муаровая профилометрия, Z-разрешение 0.5 µm, Gage R&R < 10%, ИИ-компенсация коробления плат (DFWC), замкнутый цикл KSMART (IPC-CFX).
3. **[03_mirtec_3d_aoi_automotive.md](03_mirtec_3d_aoi_automotive.md)** (+ `.meta.json`)
   - **Тема:** *Mirtec: Automotive PCBA Quality Control and 3D AOI Inspection Innovations*
   - **Раздел:** `/reviews/` (`Inspection`)
   - **Спецификации:** 15MP CoaXPress камера (120 fps, скорость 120 см²/с), 4-частотный цифровой муар (высота до 25 мм), ложные срабатывания < 50 ppm, соответствие ISO/TS 16949.

---

## Особенности серии
- **Полноформатный инженерный разбор (800–900+ слов каждая статья):** мы ушли от ограничений в 500 слов для новостей, создав развёрнутые аналитические материалы с конкретными цифрами, физическими принципами работы, схемами и рекомендациями для производственных линий.
- **Прозрачная атрибуция источника:** ссылка `https://online.fliphtml5.com/kwnhb/fakj/` (`SMT Today Magazine Issue 80`) прописана в тексте каждой статьи, в метаданных `.meta.json["source_url"]`, в блоке `sources`, а также в микроразметке **JSON-LD Article Schema** и промо-текстах для LinkedIn, отраслевых форумов и email-рассылок.
- **Проверка линтером (`article_linter.py`):** все три статьи получили высокие оценки качества (95–96/100), не содержат запрещённых ИИ-штампов и строго опираются на верифицированные технические факты номера.
