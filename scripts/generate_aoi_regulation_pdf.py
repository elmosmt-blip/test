#!/usr/bin/env python3
"""Generate the professionally typeset Russian AOI implementation regulation."""
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, PageBreak, NextPageTemplate,
    Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable,
    Preformatted, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from pathlib import Path
import os

OUT = Path("docs/AI_Second_Opinion_Koh_Young_3D_AOI.pdf")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Embedded Unicode fonts keep Cyrillic portable.
pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Mono", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Mono-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"))

NAVY = HexColor("#102A43")
BLUE = HexColor("#137CBD")
CYAN = HexColor("#00A6A6")
PALE = HexColor("#EAF5FA")
PALE2 = HexColor("#F3F7FA")
INK = HexColor("#243B53")
MUTED = HexColor("#627D98")
GREEN = HexColor("#138A72")
AMBER = HexColor("#D9822B")
RED = HexColor("#C23030")
LINE = HexColor("#D9E2EC")
WHITE = colors.white

styles = getSampleStyleSheet()
def ps(name, **kw):
    return ParagraphStyle(name, **kw)

BODY = ps("BodyRU", fontName="DejaVu", fontSize=9.2, leading=14, textColor=INK,
          spaceAfter=6, allowWidows=0, allowOrphans=0)
SMALL = ps("Small", parent=BODY, fontSize=7.7, leading=11, textColor=MUTED)
H1 = ps("H1RU", fontName="DejaVu-Bold", fontSize=18, leading=22, textColor=NAVY,
        spaceBefore=8, spaceAfter=11, keepWithNext=True)
H2 = ps("H2RU", fontName="DejaVu-Bold", fontSize=12.5, leading=16, textColor=BLUE,
        spaceBefore=11, spaceAfter=7, keepWithNext=True)
H3 = ps("H3RU", fontName="DejaVu-Bold", fontSize=10.2, leading=14, textColor=NAVY,
        spaceBefore=8, spaceAfter=5, keepWithNext=True)
BULLET = ps("BulletRU", parent=BODY, leftIndent=12, firstLineIndent=-8, bulletIndent=2,
            spaceAfter=3)
CHECK = ps("CheckRU", parent=BODY, leftIndent=14, firstLineIndent=-10, bulletIndent=2,
           spaceAfter=4)
CODE = ps("CodeRU", fontName="DejaVu-Mono", fontSize=6.7, leading=9.5,
          textColor=HexColor("#D9E2EC"), backColor=NAVY, leftIndent=7, rightIndent=7,
          borderPadding=8, spaceBefore=4, spaceAfter=8)
CAPTION = ps("Caption", fontName="DejaVu", fontSize=7.4, leading=10, textColor=MUTED,
             alignment=TA_CENTER, spaceAfter=8)
CALLOUT = ps("Callout", parent=BODY, fontSize=8.5, leading=13, textColor=NAVY)
TOC_H = ps("TOCH", fontName="DejaVu-Bold", fontSize=10, leading=14, leftIndent=0,
           firstLineIndent=0, textColor=NAVY, spaceBefore=4)
TOC_S = ps("TOCS", fontName="DejaVu", fontSize=8.5, leading=12, leftIndent=15,
           firstLineIndent=0, textColor=INK)


def safe(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def P(text, style=BODY):
    return Paragraph(text, style)

def bullets(items, check=False):
    st = CHECK if check else BULLET
    mark = "□" if check else "•"
    return [Paragraph(f"{mark}  {x}", st) for x in items]

def code(txt):
    return Preformatted(txt.strip("\n"), CODE, maxLineLength=105)

def heading(txt, level=1):
    return Paragraph(txt, [H1, H2, H3][level-1])

def table(data, widths=None, header=True, aligns=None):
    cooked=[]
    for r, row in enumerate(data):
        cooked.append([c if isinstance(c, Flowable) else P(str(c), SMALL if r else ps("th", parent=SMALL, fontName="DejaVu-Bold", textColor=WHITE)) for c in row])
    t=Table(cooked, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    cmds=[("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),6),
          ("RIGHTPADDING",(0,0),(-1,-1),6), ("TOPPADDING",(0,0),(-1,-1),6),
          ("BOTTOMPADDING",(0,0),(-1,-1),6), ("GRID",(0,0),(-1,-1),0.35,LINE)]
    if header: cmds += [("BACKGROUND",(0,0),(-1,0),NAVY)]
    for r in range(1 if header else 0,len(data)):
        if r%2==0: cmds.append(("BACKGROUND",(0,r),(-1,r),PALE2))
    if aligns:
        for col, a in enumerate(aligns): cmds.append(("ALIGN",(col,1),(col,-1),a))
    t.setStyle(TableStyle(cmds)); return t

class StepBadge(Flowable):
    def __init__(self, number, title, subtitle=""):
        Flowable.__init__(self); self.number=str(number); self.title=title; self.subtitle=subtitle
        self.height=21*mm
    def wrap(self, availWidth, availHeight): self.width=availWidth; return availWidth,self.height
    def draw(self):
        c=self.canv; c.saveState(); c.setFillColor(PALE); c.roundRect(0,0,self.width,self.height,3*mm,fill=1,stroke=0)
        c.setFillColor(BLUE); c.circle(10*mm,self.height/2,6*mm,fill=1,stroke=0)
        c.setFillColor(WHITE); c.setFont("DejaVu-Bold",10); c.drawCentredString(10*mm,self.height/2-3.3,self.number)
        c.setFillColor(NAVY); c.setFont("DejaVu-Bold",11); c.drawString(20*mm,self.height-8*mm,self.title)
        if self.subtitle:
            c.setFillColor(MUTED); c.setFont("DejaVu",7.2); c.drawString(20*mm,5*mm,self.subtitle)
        c.restoreState()

class Pipeline(Flowable):
    def __init__(self): Flowable.__init__(self); self.height=52*mm
    def wrap(self,a,b): self.width=a; return a,self.height
    def draw(self):
        c=self.canv; y=self.height/2; labels=[("Koh Young\nCase",NAVY),("Tier 1\nXGBoost",BLUE),("Tier 2\nVLM",CYAN),("Decision\nEngine",GREEN)]
        gap=7*mm; w=(self.width-gap*3)/4; h=20*mm
        for i,(lab,col) in enumerate(labels):
            x=i*(w+gap); c.setFillColor(col); c.roundRect(x,y-h/2,w,h,3*mm,fill=1,stroke=0)
            c.setFillColor(WHITE); c.setFont("DejaVu-Bold",8)
            for j,line in enumerate(lab.split("\n")): c.drawCentredString(x+w/2,y+2-j*9,line)
            if i<3:
                c.setStrokeColor(MUTED); c.setLineWidth(1.2); c.line(x+w,y,x+w+gap-2,y)
                c.setFillColor(MUTED); c.wedge(x+w+gap-4,y-2,x+w+gap,y+2,270,180,fill=1,stroke=0)
        c.setFillColor(MUTED); c.setFont("DejaVu",7)
        c.drawCentredString(self.width/2,7*mm,"Численные признаки → визуальное подтверждение → калиброванное действие")

class Doc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(filename,pagesize=A4,rightMargin=17*mm,leftMargin=17*mm,topMargin=19*mm,bottomMargin=17*mm,
                         title="AI Second Opinion для Koh Young 3D AOI", author="Технический регламент внедрения")
        frame=Frame(self.leftMargin,self.bottomMargin,self.width,self.height,id="body")
        self.addPageTemplates([PageTemplate(id="content",frames=frame,onPage=self.header_footer)])
    def header_footer(self,c,doc):
        if doc.page==1: return
        c.saveState(); c.setStrokeColor(LINE); c.line(17*mm,A4[1]-12*mm,A4[0]-17*mm,A4[1]-12*mm)
        c.setFont("DejaVu",6.8); c.setFillColor(MUTED)
        c.drawString(17*mm,A4[1]-9*mm,"AI SECOND OPINION · KOH YOUNG 3D AOI")
        c.drawRightString(A4[0]-17*mm,A4[1]-9*mm,"ПРОМЫШЛЕННЫЙ РЕГЛАМЕНТ")
        c.line(17*mm,11*mm,A4[0]-17*mm,11*mm)
        c.drawString(17*mm,7*mm,"Версия 1.0 · 03.08.2026")
        c.drawRightString(A4[0]-17*mm,7*mm,f"{doc.page:02d}")
        c.restoreState()
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("H1RU","H2RU"):
            level=0 if flowable.style.name=="H1RU" else 1
            text=flowable.getPlainText(); key="h%d-%s"%(level,abs(hash(text)))
            self.canv.bookmarkPage(key); self.canv.addOutlineEntry(text,key,level=level,closed=False)
            self.notify("TOCEntry",(level,text,self.page,key))

story=[]
# Cover
story += [Spacer(1,12*mm), P("ПРОМЫШЛЕННЫЙ РЕГЛАМЕНТ", ps("eyebrow",fontName="DejaVu-Bold",fontSize=9,leading=12,textColor=CYAN,tracking=1.8)),
          Spacer(1,7*mm), P("AI Second Opinion<br/>для Koh Young 3D AOI", ps("cover",fontName="DejaVu-Bold",fontSize=28,leading=34,textColor=NAVY)),
          Spacer(1,5*mm), P("Пошаговая инструкция по внедрению каскадной системы арбитража дефектов", ps("subtitle",fontName="DejaVu",fontSize=13,leading=19,textColor=MUTED)),
          Spacer(1,12*mm), Pipeline(), Spacer(1,10*mm)]
cover_data=[
 [P("МОДЕЛЬ", ps("k",fontName="DejaVu-Bold",fontSize=7,textColor=MUTED)), P("Gemma 4 26B-A4B-it · NVFP4", ps("v",fontName="DejaVu-Bold",fontSize=10,textColor=NAVY))],
 [P("ПЛАТФОРМА", ps("k2",fontName="DejaVu-Bold",fontSize=7,textColor=MUTED)), P("NVIDIA DGX Spark · GB10 · 128 GB Unified Memory", ps("v2",fontName="DejaVu-Bold",fontSize=10,textColor=NAVY))],
 [P("НАЗНАЧЕНИЕ", ps("k3",fontName="DejaVu-Bold",fontSize=7,textColor=MUTED)), P("Контролируемая автоматизация разбора срабатываний AOI", ps("v3",fontName="DejaVu-Bold",fontSize=10,textColor=NAVY))],]
t=Table(cover_data,colWidths=[34*mm,125*mm]); t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),PALE2),("BOX",(0,0),(-1,-1),0.6,LINE),("INNERGRID",(0,0),(-1,-1),0.4,LINE),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)])); story.append(t)
story += [Spacer(1,14*mm), P("Версия 1.0", ps("date",fontName="DejaVu-Bold",fontSize=9,textColor=NAVY)), P("03 августа 2026", SMALL), Spacer(1,5*mm),
          P("Статус: проект регламента для валидации на производственной площадке", ps("status",fontName="DejaVu",fontSize=7.5,textColor=AMBER)), PageBreak()]

# Document control & TOC
story += [heading("Управление документом"),
 table([["Параметр","Значение"],["Класс документа","Технический регламент внедрения"],["Область применения","SMT-линии с Koh Young 3D AOI"],["Владелец процесса","Руководитель качества SMT / Process Owner"],["Критичность","Высокая: решение влияет на выпуск и ручной контроль"],["Период пересмотра","Ежеквартально и после каждого изменения модели, порогов или IPC-контекста"]],[42*mm,117*mm]),
 Spacer(1,5*mm),
 P("ВАЖНО", ps("alerthead",fontName="DejaVu-Bold",fontSize=8,textColor=RED)),
 P("Документ задаёт инженерную схему внедрения, но не заменяет квалификацию процесса, требования системы менеджмента качества, договорные обязательства и официальную интерпретацию IPC-A-610. Все версии модели, контейнеров, CLI-параметры, показатели производительности и лицензии должны быть подтверждены на целевой конфигурации до закупки и допуска в Production.", CALLOUT),
 Spacer(1,7*mm), heading("Содержание"),]
toc=TableOfContents(); toc.levelStyles=[TOC_H,TOC_S]; story += [toc,PageBreak()]

# 0
story += [StepBadge("0","Архитектурный обзор","Цель, границы и критерии готовности"),Spacer(1,5*mm),heading("0. Архитектурный обзор и предварительные требования"),
 P("Система реализует «второе мнение» для срабатываний Koh Young 3D AOI. Безопасная архитектура строится как каскад: быстрый фильтр численных признаков отсеивает только заведомо безопасные ложные срабатывания, мультимодальная модель рассматривает серую зону, а спорные случаи передаются инженеру."),
 heading("0.1 Чек-лист предварительных требований",2)]
story += bullets(["Доступ к NVIDIA DGX Spark (GB10) с административными правами sudo.","Аккаунт Hugging Face; приняты условия использования выбранной модели.","Исторический архив дефектов Koh Young AOI за 6–12 месяцев.","Не менее двух инженеров качества SMT для перекрёстной разметки.","Утверждён целевой предел False Negative Rate: FNR ≤ 0,05%.","Определены владелец риска, порядок остановки и ручной резервный процесс."],True)
story += [heading("0.2 Ключевые принципы безопасности",2), table([["Принцип","Практическая реализация"],["Human-in-the-loop","Низкая уверенность и несогласованные ответы — только Engineer Review."],["Fail-safe","При деградации метрик — возврат к 100% ручному контролю."],["Traceability","Версии модели, промпта, RAG-контекста и решение фиксируются в аудите."],["Temporal validation","Финальная проверка выполняется на более позднем временном холдауте."],["Least automation","Автоматизируются только классы и диапазоны, доказавшие безопасность."]],[45*mm,114*mm]),PageBreak()]

# 1
story += [StepBadge("1","Валидация DGX Spark","GB10 · SM121 · CUDA 13.0"),Spacer(1,5*mm),heading("1. Валидация платформы DGX Spark"),
 P("До развертывания зафиксируйте аппаратную конфигурацию, версии прошивки, драйвера, CUDA и контейнерного runtime. Результаты команд приложите к протоколу квалификации."),
 code("uname -m                  # ожидается: aarch64\nnvidia-smi                # GB10 и доступная unified memory\nnvcc --version            # целевая версия: CUDA 13.0"),
 heading("1.1 Контейнер NGC",2), code("docker pull nvcr.io/nvidia/pytorch:25.09-py3\n\ndocker run --gpus all -it --rm \\\n  --ipc=host \\\n  --ulimit memlock=-1 \\\n  -v ~/aoi_project:/workspace \\\n  nvcr.io/nvidia/pytorch:25.09-py3"),
 P("Контрольная точка: контейнер видит ускоритель, выполняет базовую CUDA-операцию, а каталог проекта доступен на запись. Тег контейнера перед эксплуатацией должен быть проверен в реестре NGC и закреплён digest-значением.",CALLOUT)]

# 2
story += [StepBadge("2","Двухуровневый каскад","Предфильтр → мультимодальный арбитраж"),Spacer(1,5*mm),heading("2. Принцип работы двухуровневого каскада"),Pipeline(),
 heading("2.1 Tier-1 — фильтр табличных метрик",2), P("XGBoost анализирует объём припоя, высоту, копланарность и другие численные признаки. Кейс закрывается как AUTO_DISMISS только при доказанной вероятности ложного срабатывания выше 99% и при соблюдении ограничений применимости."),
 heading("2.2 Tier-2 — мультимодальный арбитраж",2), P("Кейсы серой зоны поступают в VLM вместе с четырьмя изображениями, 3D-метриками, релевантными критериями IPC и проверенными прецедентами. Выход валидируется по JSON-схеме и проходит калибровку."),
 table([["Маршрут","Условие","Действие"],["AUTO_DISMISS","Tier-1: строго безопасная область","Закрыть с полным audit trail"],["AUTO_VERDICT","Tier-2: высокая calibrated confidence","Применить решение; включить в слепой аудит"],["ENGINEER_REVIEW","Низкая уверенность / конфликт / OOD","Передать инженеру"],["SAFE MODE","Ошибка сервиса или rollback-trigger","100% ручной контроль"]],[34*mm,65*mm,60*mm]),PageBreak()]

# 3
story += [StepBadge("3","Данные и Ground Truth","Экспорт, разметка, согласованность"),Spacer(1,5*mm),heading("3. Сбор, структурирование и разметка данных"),heading("3.1 Пакет инспекционного кейса",2)]
story += bullets(["2d_color.png — цветной вид сверху.","3d_heightmap.png — карта высот в условных цветах.","slice_a.png и slice_b.png — поперечный и боковой срезы.","metadata.json — идентификаторы, измерения AOI и технологический контекст.","ground_truth.json — проверенный вердикт, код дефекта, комментарий и provenance разметки."])
story += [code('''{
  "case_id": "KY_20260803_001042",
  "component_type": "QFN-48",
  "part_number": "PN-100429-B",
  "line_id": "SMT_LINE_02",
  "defect_code": "INSUFFICIENT_SOLDER",
  "koh_young_confidence": 0.82,
  "measured_3d_features": {
    "solder_volume_percentage": 38.5,
    "solder_height_um": 32.1,
    "coplanarity_error_um": 12.4,
    "ipc_spec_min_height_um": 50.0
  },
  "timestamp": "2026-08-03T14:22:10Z"
}'''),heading("3.2 Ground Truth и когерентность",2)]
story += bullets(["Инженер назначает verdict: REAL_DEFECT или FALSE_POSITIVE, уточняет defect_code и добавляет обоснование.","Для 15% выборки проводится независимая двойная разметка.","Рассчитывается κ Коэна; при κ < 0,70 проводится калибровочная сессия.","Спорные случаи получают hard_case: true и не используются для безусловной автоматизации до adjudication.","Фиксируются идентификатор разметчика, версия стандарта, дата и итог согласования."])

# 4
story += [StepBadge("4","Структура датасета","Временное разделение и баланс"),Spacer(1,5*mm),heading("4. Структура датасета и разделение выборок"),
 code("dataset/\n├── train/2026-01/case_000001/\n│   ├── 2d_color.png\n│   ├── 3d_heightmap.png\n│   ├── slice_a.png\n│   ├── slice_b.png\n│   ├── metadata.json\n│   └── ground_truth.json\n├── val/2026-02/\n└── test_holdout/2026-06/    # не участвует в обучении"),
 table([["Выборка","Минимум","Целевой объём","Назначение"],["Train","2 000","10 000+","Обучение / настройка"],["Validation","500","1 500","Калибровка и пороги"],["Test Holdout","500","1 000","Финальная независимая оценка"]],[38*mm,28*mm,35*mm,58*mm],aligns=["LEFT","RIGHT","RIGHT","LEFT"]),
 P("Для обучения допускается баланс 50/50 REAL_DEFECT и FALSE_POSITIVE. Для оценки производственных KPI дополнительно используйте выборку с естественной распространённостью классов; иначе FNR, precision и нагрузка на инженеров будут искажены.",CALLOUT),PageBreak()]

# 5-6
story += [StepBadge("5","Рабочая среда","Каталоги, venv, зависимости"),Spacer(1,5*mm),heading("5. Настройка рабочей среды"),code("mkdir -p ~/aoi_project/{models,dataset,calibration,results,audit,monitoring,logs,prompts,scripts,training,vector_db}\ncd ~/aoi_project\n\npython3 -m venv venv\nsource venv/bin/activate\npip install -U pip\npip install torch transformers vllm openai numpy pandas scikit-learn \\\n  opencv-python pillow qdrant-client sentence-transformers\npip install -U 'huggingface_hub[cli]'"),
 P("В Production зависимости фиксируются lock-файлом и хешами; образы проходят сканирование уязвимостей. Секреты Hugging Face и сервисов не помещаются в репозиторий или audit-log."),
 StepBadge("6","Модель и vLLM","NVFP4 · Marlin · FP8 KV cache"),Spacer(1,5*mm),heading("6. Загрузка модели и развертывание vLLM"),heading("6.1 Загрузка весов",2),
 code("huggingface-cli login\n\nhuggingface-cli download nvidia/Gemma-4-26B-A4B-NVFP4 \\\n  --local-dir models/gemma4-26b-nvfp4"),
 heading("6.2 Скрипт запуска scripts/start_gemma.sh",2),code("#!/bin/bash\nset -euo pipefail\nexport PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True\n\nvllm serve models/gemma4-26b-nvfp4 \\\n  --served-model-name gemma-4-26b \\\n  --host 0.0.0.0 --port 8000 \\\n  --quantization modelopt \\\n  --kv-cache-dtype fp8 \\\n  --max-model-len 65536 \\\n  --gpu-memory-utilization 0.85 \\\n  --moe-backend marlin \\\n  --reasoning-parser gemma4 \\\n  --enable-auto-tool-choice \\\n  --tool-call-parser pythonic \\\n  --max-num-batched-tokens 8192 \\\n  --limit-mm-per-prompt '{\"image\": 4}'"),
 P("Параметры CLI зависят от конкретной версии vLLM и реализации модели. Перед запуском проверьте их по официальной документации установленной версии и зафиксируйте успешный smoke-test.",CALLOUT),PageBreak(),
 heading("6.3 План памяти DGX Spark",2),
 table([["Потребитель","Оценка","Доля от 128 GB"],["Веса NVFP4","≈ 16,5 GB","12,9%"],["KV-cache FP8 / batching","≈ 82,0 GB","64,1%"],["ОС и CUDA Runtime","≈ 10,0 GB","7,8%"],["Свободный резерв","≈ 19,5 GB","15,2%"],["Итого","128,0 GB","100%"]],[75*mm,42*mm,42*mm],aligns=["LEFT","RIGHT","RIGHT"]),
 P("Оценка памяти является проектным бюджетом, а не гарантией. Фактическое потребление измеряется при целевой длине контекста, числе изображений, batch size и параллельной нагрузке.",CALLOUT)]

# 7
story += [StepBadge("7","Промпт и RAG","Строгая схема вывода и управляемый контекст"),Spacer(1,5*mm),heading("7. Мультимодальный промпт-инжиниринг и RAG"),heading("7.1 Системный промпт",2),
 code('''You are an expert SMT Quality Control Arbiter adhering strictly to
IPC-A-610 standards. You receive measured 3D features, relevant
acceptance criteria, verified precedents, and exactly four images.

Analyze physical 3D metrics first, then cross-examine visual evidence.
Return STRICT JSON only:
{
  "verdict": "REAL_DEFECT" | "FALSE_POSITIVE",
  "confidence": 0.0-1.0,
  "defect_code": "...",
  "reasoning": "..."
}'''),
 heading("7.2 Локальный RAG-слой",2),
 table([["Индекс","Запрос","Выход","Контроль"],["Precedents DB","Описание + метаданные","3 похожих кейса","Только adjudicated GT"],["IPC / Manuals","defect_code + component_type","Релевантные чанки","Версия, раздел, лицензия"]],[40*mm,42*mm,40*mm,37*mm]),
 P("В Production рекомендуется отделить внутреннее рассуждение модели от краткого проверяемого обоснования. В журнал записываются наблюдаемые признаки, применённый критерий и ссылки на контекст — без скрытых chain-of-thought данных."),PageBreak()]

# 8
story += [StepBadge("8","Решения и калибровка","Пороги, self-consistency, isotonic regression"),Spacer(1,5*mm),heading("8. Логика принятия решений и калибровка"),
 heading("8.1 Матрица маршрутизации",2),
 table([["Условие","Маршрут","Обязательный контроль"],["Tier-1 FP confidence > 0,99","AUTO_DISMISS","Guardrails + OOD + аудит"],["calibrated confidence ≥ high_threshold","AUTO_VERDICT","Слепой аудит 15%"],["low ≤ confidence < high","5 сэмплов, T=0,7","Согласие ≥ 4/5; иначе review"],["confidence < low_threshold","ENGINEER_REVIEW","Ручной вердикт"],["Ошибка / drift / rollback","SAFE MODE","100% ручной контроль"]],[64*mm,46*mm,49*mm]),
 heading("8.2 Изотоническая калибровка",2),P("Калибратор преобразует сырой score S_raw в эмпирическую вероятность корректного ответа: P̂(Correct | S_raw) = f_iso(S_raw). Пороги выбираются на validation под ограничение FNR ≤ 0,05%, затем однократно подтверждаются на test holdout."),
 code('''import glob, json
from sklearn.isotonic import IsotonicRegression

raw_conf, labels = [], []
for filepath in glob.glob("results/val/*.json"):
    with open(filepath, encoding="utf-8") as fh:
        data = json.load(fh)
    raw_conf.append(data["raw_confidence"])
    labels.append(int(data["verdict"] == data["ground_truth"]))

iso_model = IsotonicRegression(out_of_bounds="clip")
iso_model.fit(raw_conf, labels)
# Далее: выбор порогов с доверительным интервалом для FNR.'''),
 P("Для крайне низкого целевого FNR одной точечной оценки недостаточно: публикуйте доверительный интервал, число реальных дефектов в holdout и верхнюю границу риска. Если данных недостаточно, автоматический допуск не выдается.",CALLOUT)]

# 9-10
story += [StepBadge("9","Shadow и аудит","Наблюдение без воздействия на линию"),Spacer(1,5*mm),heading("9. Shadow-режим, мониторинг и слепой аудит"),
 table([["Фаза","Срок / охват","Критерий выхода"],["Shadow Execution","2–4 недели; 100% потока","Стабильность, latency, класс-метрики, no critical misses"],["Ограниченный Production","Только валидированные классы","Agreement > 98% и FNR в лимите"],["Слепой аудит","15% AUTO_VERDICT первые 3 месяца","Независимая проверка без показа AI-ответа"]],[43*mm,51*mm,65*mm]),
 heading("9.1 Минимальный набор метрик",2)]
story += bullets(["FNR и доверительный интервал — в целом и по defect_code / component_type.","Agreement rate, precision, recall, escalation rate и доля OOD.","Latency p50/p95/p99, throughput, ошибки JSON и таймауты.","Data drift по численным признакам, линиям, продуктам и источникам изображений.","Доля решений, изменённых инженером, и причины override."])
story += [PageBreak(),StepBadge("10","Аудит и rollback","Неизменяемый журнал и безопасный откат"),Spacer(1,5*mm),heading("10. Аудит-трейл и автоматический откат"),
 code('''{
  "timestamp": "2026-08-03T15:04:12Z",
  "case_id": "KY_20260803_001042",
  "pipeline_tier": "tier2_vlm",
  "model_version": "gemma-4-26b-a4b-nvfp4-v1",
  "raw_confidence": 0.96,
  "calibrated_confidence": 0.93,
  "final_action": "AUTO_FALSE_POSITIVE",
  "reasoning_summary": "Observed metrics and visual evidence...",
  "retrieved_context_ids": {
    "precedents": ["case_000412", "case_001899"],
    "ipc_section": "ipc_a_610_h_sec8_3_5"
  }
}'''),heading("10.1 Триггеры rollback",2)]
story += bullets(["FNR > 0,05% по слепому аудиту за скользящее окно 7 дней.","Engineer Review > 30% общего потока.","Latency p95 > 12 секунд.","Невалидный JSON, недоступный RAG или несоответствие версии модели утверждённой конфигурации.","Обнаруженный класс критического пропуска, даже если агрегированный FNR остаётся в лимите."])
story += [P("Действие по умолчанию: немедленный переход в 100% ручной контроль, фиксация инцидента, уведомление владельца процесса, анализ первопричины и повторная квалификация перед возвратом.",CALLOUT)]

# 11
story += [PageBreak(),StepBadge("11","Локальное дообучение","QLoRA в окне обслуживания"),Spacer(1,5*mm),heading("11. Локальное тонкое обучение"),
 P("После накопления не менее 1 000 новых сложных adjudicated-кейсов допускается обучение LoRA-адаптера. Обучающий набор отделяется от validation и temporal holdout; базовая модель, данные и параметры получают версии."),
 code("llamafactory-cli train \\\n  --stage sft --do_train \\\n  --model_name_or_path models/gemma4-26b-nvfp4 \\\n  --dataset koh_young_fine_tuning_data \\\n  --template gemma4 \\\n  --finetuning_type lora --lora_target all \\\n  --output_dir output/gemma4_aoi_lora_v2 \\\n  --per_device_train_batch_size 1 \\\n  --gradient_accumulation_steps 8 \\\n  --learning_rate 2e-4 --num_train_epochs 3 \\\n  --quantization_bit 4"),
 P("Проектная оценка памяти QLoRA: 35–40 GB. Она должна быть подтверждена экспериментом. Новая версия не заменяет действующую без полного regression-test, перекалибровки и повторного Shadow Execution."),PageBreak()]

# 12
story += [StepBadge("12","Production readiness","Финальный допуск"),Spacer(1,5*mm),heading("12. Итоговый контрольный чек-лист"),]
story += bullets(["CUDA 13.0 и GB10 валидированы в закреплённом NGC-контейнере.","vLLM успешно запущен на порту 8000; backend и все параметры подтверждены на установленной версии.","Производительность одиночного инференса измерена; целевой ориентир 48–52 токена/с подтверждён или скорректирован протоколом.","Поток содержит структурированные 3D-метрики Koh Young и проходит schema validation.","RAG-индекс содержит актуальные разрешённые чанки IPC-A-610 и проверенные прецеденты.","Isotonic Regression выполнена; high_threshold и low_threshold утверждены и версионированы.","Shadow Execution пройдена 2–4 недели без критических сбоев.","Слепой аудит 15% автоматических решений активен.","Неизменяемый audit trail, алерты и Grafana-мониторинг работают 24/7.","Rollback проверен учением; ручной резервный процесс доступен.","Комитет качества подписал протокол допуска с ограничениями по классам и линиям."],True)
story += [Spacer(1,6*mm),heading("12.1 Матрица решения Go / No-Go",2),
 table([["Область","GO","NO-GO"],["Безопасность","FNR и верхняя граница CI в лимите","Недостаточно дефектов или critical miss"],["Качество данных","Полнота, provenance, κ ≥ 0,70","Утечки, пропуски, несогласованная разметка"],["Надёжность","SLA и rollback проверены","Нет safe mode / таймауты"],["Управление","Версии и владельцы зафиксированы","Нет владельца риска или аудита"]],[42*mm,59*mm,58*mm]),PageBreak()]

# Appendix
story += [heading("Приложение A. Роли и ответственность"),
 table([["Роль","Ответственность"],["Process Owner","Утверждает границы автоматизации и риск-аппетит."],["SMT Quality Engineer","Ground Truth, adjudication, слепой аудит, IPC-интерпретация."],["ML Engineer","Модель, калибровка, тесты, drift и воспроизводимость."],["Platform Engineer","DGX, контейнеры, SLA, резервирование, безопасность."],["MLOps / SRE","Релизы, мониторинг, audit trail, rollback."],["Quality Committee","Финальный Go / No-Go и периодический пересмотр."]],[50*mm,109*mm]),
 heading("Приложение B. Источники"),
 P("Ссылки ниже перенесены из исходного материала. Перед утверждением регламента необходимо проверить доступность, дату публикации, авторитетность и соответствие фактически используемым версиям ПО и оборудования."),]
refs=[
("1","Gemma 4 Day-1 Inference on NVIDIA DGX Spark — Preliminary Benchmarks","https://forums.developer.nvidia.com/t/gemma-4-day-1-inference-on-nvidia-dgx-spark-preliminary-benchmarks/365503"),
("2","[Benchmark] Gemma 4 on DGX Spark — Which Model Should You Pick? — ai-muninn","https://ai-muninn.com/en/blog/dgx-spark-gemma4-complete-guide"),
("3","Google/gemma-4-26B-A4B-it — vLLM Recipes","https://recipes.vllm.ai/Google/gemma-4-26B-A4B-it"),
("4","An Analytical Report on the NVIDIA DGX Spark — TWOWIN TECHNOLOGY","https://twowintech.com/an-analytical-report-on-the-nvidia-dgx-spark/"),
("5","Google Gemma 4: Smooth local inference on RTX PCs and DGX Spark","https://migovi.com/en/google-gemma-4-nvidia-rtx-pcs-dgx-spark/"),
("6","Fine-tuning LLMs with NVIDIA DGX Spark and Unsloth","https://unsloth.ai/docs/blog/fine-tuning-llms-with-nvidia-dgx-spark-and-unsloth"),]
for n,title,url in refs:
    story.append(P(f"<b>{n}. {safe(title)}</b><br/><font color='#627D98' size='7'>{safe(url)}</font>",SMALL))
story += [Spacer(1,8*mm),HRFlowable(width="100%",thickness=.5,color=LINE),Spacer(1,4*mm),
          P("Конец документа · AI Second Opinion для Koh Young 3D AOI · Версия 1.0", ps("end",fontName="DejaVu-Bold",fontSize=8,textColor=NAVY,alignment=TA_CENTER))]

Doc(str(OUT)).multiBuild(story)
print(OUT)
