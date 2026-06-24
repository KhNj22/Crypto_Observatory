# دليل التشغيل — Crypto Observatory V1
### خطوة بخطوة (Windows · PowerShell · VS Code · Python 3.12)

> هذا الدليل يأخذك من صفر إلى منصّة تعمل وتكتب لقطة سوق حقيقية في الأرشيف.
> الجزء المبني حالياً: **المرحلة 0 (الأرشيف) + المرحلة 1 (Market Observatory)**.
> كل أمر مكتوب لـ PowerShell. نفّذ الخطوات بالترتيب.

---

## نموذج التشغيل باختصار

المنصّة **مهمة مجدولة، لا خادم دائم**. تعمل بإيقاعين:
- **يومي (خفيف):** `run_market.py` — يحسب حالة السوق (regime) ويكتب لقطة `daily`، وينبّه تيليجرام **فقط عند تغيّر الحالة**.
- **أسبوعي (كامل):** يأتي في المراحل 2–5 (القطاعات + الترتيب + المحفظة).

التواريخ كلها UTC. الأرشيف SQLite في `data/observatory.db`.

---

## الخطوة 1 — التحقّق من المتطلّبات

افتح PowerShell ونفّذ:

```powershell
python --version      # يجب أن يظهر Python 3.12.x
code --version        # VS Code (اختياري لكن مفيد)
```

إن لم يظهر Python 3.12، ثبّته من python.org وفعّل خيار **"Add Python to PATH"**.

---

## الخطوة 2 — تجهيز مجلّد المشروع

ضع الملفّات الستة في مجلّد واحد، مثلاً `C:\projects\crypto_observatory\`:

```
crypto_observatory\
├── config.py
├── schema.sql
├── archive.py
├── data_sources.py
├── market_observatory.py
├── telegram_bot.py
├── run_market.py
├── init_archive.py
├── test_state_machine.py
└── requirements.txt
```

افتح المجلّد في VS Code:

```powershell
cd C:\projects\crypto_observatory
code .
```

---

## الخطوة 3 — إنشاء البيئة الافتراضية وتفعيلها

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

إذا ظهر خطأ **"running scripts is disabled"**، فعّل سياسة التنفيذ مرّة واحدة ثم أعد التفعيل:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

ستظهر `(.venv)` في بداية السطر = البيئة مفعّلة.

---

## الخطوة 4 — تثبيت المكتبات

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

تحقّق:

```powershell
python -c "import pandas, numpy, requests, pyarrow; print('كل المكتبات جاهزة ✓')"
```

---

## الخطوة 5 — تهيئة الأرشيف (المرحلة 0)

ينشئ قاعدة البيانات + الجداول الـ13 + يزرع الأذرع العشرة، ويُجري فحصاً ذاتياً للسلامة:

```powershell
python init_archive.py
```

**النتيجة المتوقّعة** (مختصرة): إصدار المخطط = 1، عدد الأذرع = 10، ثم خمسة فحوص تنتهي بـ:

```
جميع الفحوص نجحت ✓
الأساس سليم: ذرّي + idempotent + يرفض اللقطات الناقصة + ورقي بحت.
```

> هذا يثبت أن الكتابة الذرّية والـ idempotency والـ rollback تعمل. قاعدة الإنتاج `data\observatory.db` تُنشأ تلقائياً.

---

## الخطوة 6 — فحص منطق محرّك الحالة (اختياري لكن موصى به)

يختبر الـ hysteresis والتأكيد ببيانات تركيبية (بلا إنترنت):

```powershell
python test_state_machine.py
```

يجب أن ينتهي بـ: `جميع اختبارات محرّك الحالة نجحت ✓`.

---

## الخطوة 7 — أول تشغيل حقيقي لـ Market Observatory

هذا أول اتصال بالإنترنت (Binance + DefiLlama + alternative.me):

```powershell
python run_market.py
```

**النتيجة المتوقّعة** (مثال):

```
*Crypto Observatory* — 2026-06-23 (UTC)
🟢 *Regime:* risk_on
BTC: 64,200  |  SMA200: 58,100  (+10.5%)
F&G: 72 (Greed)
Stablecoin 30d: +1.8%

[أرشيف] ok  run_id=1
```

ماذا حدث؟ جلب 400 شمعة يومية لـ BTC، حسب SMA200 + الحالة مع الـ hysteresis والتأكيد، وكتب لقطة `daily` في الأرشيف، وطبع التقرير.

> **مهم:** الحساب يتطلّب ≥ 200 شمعة يومية لـ SMA200. نجلب 400 تلقائياً، فلا مشكلة. إن ظهر خطأ "بيانات غير كافية"، فالسبب غالباً تعذّر الوصول إلى Binance (راجع الخطوة 12).

---

## الخطوة 8 — فحص الأرشيف (تأكّد أن البيانات كُتبت)

استعلام سريع من PowerShell:

```powershell
python -c "import archive as A; c=A.connect(); print('آخر تشغيل:', dict(A.latest_run(c))); print('عدد الصفوف:', A.table_counts(c))"
```

أو افتح `data\observatory.db` بأداة رسومية مجانية: **DB Browser for SQLite** (sqlitebrowser.org) — لتصفّح الجداول بصرياً.

---

## الخطوة 9 — ربط تيليجرام (لاستقبال التنبيهات)

**أ. أنشئ البوت:** في تيليجرام، راسِل `@BotFather` → `/newbot` → اتبع التعليمات → احفظ الـ **TOKEN**.

**ب. احصل على chat_id:** راسِل بوتك أي رسالة، ثم افتح في المتصفّح (ضع التوكن مكان `<TOKEN>`):

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

ابحث عن `"chat":{"id":123456789` — هذا الرقم هو **chat_id**. (بديل: راسِل `@my_id_bot`.)

**ج. اضبط المتغيّرات بشكل دائم** (تبقى بعد إغلاق النافذة):

```powershell
setx TELEGRAM_BOT_TOKEN "ضع_التوكن_هنا"
setx TELEGRAM_CHAT_ID  "ضع_chat_id_هنا"
```

> `setx` يحفظ دائماً لكنه **لا يطبّق على النافذة الحالية**. أغلق PowerShell وافتحه من جديد (وأعد تفعيل `.venv`).

للنافذة الحالية فقط (اختبار سريع):

```powershell
$env:TELEGRAM_BOT_TOKEN = "..."
$env:TELEGRAM_CHAT_ID = "..."
```

**تنبيه أمان:** لا تضع التوكن داخل الكود أبداً. المتغيّرات البيئية هي المكان الصحيح.

---

## الخطوة 10 — تشغيل مع تيليجرام

```powershell
python run_market.py
```

التصميم **صامت**: يرسل رسالة فقط عند **تغيّر الحالة** (Risk-On ↔ Risk-Off). للاختبار الفوري، يمكنك إرسال رسالة تجريبية:

```powershell
python -c "import telegram_bot as T; print('أُرسلت' if T.tg_send('اختبار من المرصد ✓') else 'فشل')"
```

---

## الخطوة 11 — الجدولة (التشغيل التلقائي)

### الخيار أ — Windows Task Scheduler (محلي، يناسب بيئتك)

أنشئ مهمّة يومية تعمل 01:05 UTC تقريباً (عدّل الوقت لمنطقتك). استبدل المسارات بمساراتك الفعلية:

```powershell
schtasks /Create /TN "CryptoObservatory-Daily" `
  /TR "C:\projects\crypto_observatory\.venv\Scripts\python.exe C:\projects\crypto_observatory\run_market.py" `
  /SC DAILY /ST 04:05 /F
```

- لا يحتاج "Start in" لأن الكود يحلّ مساراته من موقع الملف ذاته.
- **شرط:** يجب أن يكون الجهاز يعمل وقت التنفيذ.

للتحقّق / التشغيل اليدوي / الحذف:

```powershell
schtasks /Query /TN "CryptoObservatory-Daily"
schtasks /Run   /TN "CryptoObservatory-Daily"
schtasks /Delete /TN "CryptoObservatory-Daily" /F
```

### الخيار ب — GitHub Actions (سحابي مجاني، يعمل دون جهازك)

أنشئ `.github/workflows/daily.yml`:

```yaml
name: market-observatory-daily
on:
  schedule:
    - cron: "5 4 * * *"      # 04:05 UTC يومياً
  workflow_dispatch:           # تشغيل يدوي عند الحاجة
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: python run_market.py
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      - name: حفظ لقطة الأرشيف (versioned)
        run: |
          git config user.name "observatory-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data/ && git commit -m "snapshot $(date -u +%F)" || echo "لا تغيير"
          git push || echo "تخطّي الدفع"
```

أضف التوكن وchat_id في **Settings → Secrets and variables → Actions**. الميزة: أرشيف نقطة-زمنية مُؤرّخ تلقائياً في المستودع.

> للتجربة الجادّة (12 شهراً) الخيار **ب** أكثر موثوقية لأنه لا يفوّت فحص الحالة عند إغلاق جهازك.

---

## الخطوة 12 — حلّ المشكلات

| المشكلة | السبب | الحل |
|---|---|---|
| خطأ اتصال بـ Binance / "بيانات غير كافية" | قيد وصول جغرافي لـ Binance من موقعك | ثبّت `ccxt` وبدّل البورصة (Bybit/OKX/KuCoin) — انظر أسفل |
| `running scripts is disabled` | سياسة تنفيذ PowerShell | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError` | البيئة غير مفعّلة أو المكتبات غير مثبّتة | فعّل `.venv` ثم `pip install -r requirements.txt` |
| تيليجرام لا يرسل | التوكن/chat_id غير مضبوط في الجلسة | أعد فتح PowerShell بعد `setx`، أو استخدم `$env:` |
| `pyarrow` يفشل بالتثبيت | إصدار قديم من pip | `python -m pip install --upgrade pip` ثم أعد المحاولة |

**بديل Binance عبر ccxt** (إن لزم): ثبّت `pip install ccxt`، ثم في `data_sources.py` استبدل دالة `fetch_binance_klines` بنداء ccxt لبورصة بديلة — البنية مصمّمة لتقبل التبديل بسطر واحد. أو حمّل dumps تاريخية مجاناً من `data.binance.vision`.

---

## الخطوة 13 — ما الذي يعمل الآن وما التالي

**يعمل الآن:**
- الأرشيف الكامل (13 جدولاً) + الكتابة الذرّية والـ idempotency.
- محرّك الحالة (SMA200 + hysteresis + تأكيد) يكتب لقطة `daily` حقيقية.
- تنبيه تيليجرام عند تغيّر الحالة.
- الجدولة (محلي أو سحابي).

**التالي (المراحل 2–5):**
1. **Sector Observatory** — جدول القطاعات + Sector RS (يضيف Breadth الحقيقي لمحرّك الحالة).
2. **Coin Ranking** — العوامل الخمسة + Entry/Exit Buffer → Top20.
3. **Paper Portfolio** — تشغيل الأذرع العشرة + الرسوم + slippage، يكتب `weekly`.
4. **التقرير الأسبوعي** — Top20 + القطاعات + أداء الأذرع مقابل Golden Core.

السؤال الذي تنتظر إجابته بعد 12 شهراً: هل البقاء في أقوى المشاريع وقت Risk-On (والخروج وقت Risk-Off) يتفوّق — معدّلاً بالمخاطرة — على النواة الذهبية؟

---

*كل خطوة هنا اختُبرت منطقياً. جلب البيانات الحي يعمل على جهازك حيث نطاقات Binance/DefiLlama/alternative.me متاحة.*
