# Job Watch — وظايف مصر على Telegram

بيدور كل ساعة على وظايف AI / ML / Data جديدة (آخر 24 ساعة) في **مصر + الخليج + الدول العربية + أوروبا** (31 دولة، `[regions]` في `config.toml`)،
وأي وظيفة جديدة بتوصلك رسالة على Telegram فيها اللينك علطول. نفس الوظيفة على أكتر من موقع = رسالة واحدة.

**المصادر:**
- **مواقع وظايف:** LinkedIn · Indeed · Bayt · Workable · **Wuzzuf** (مباشر من صفحات الأقسام، كل ساعتين، وفيه عدد المتقدمين 👥)
- **Remote / أوروبا:** Remotive · Remote OK · Himalayas · Jobicy · Arbeitnow (ألمانيا)
- **مواقع الشركات نفسها (34 شركة)** عن طريق Greenhouse / Lever / Ashby / SmartRecruiters / Recruitee / Breezy / BambooHR / Pinpoint:
  - **مصر والمنطقة:** Careem, Tamara, Tabby, Ziina, Unifonic, Soum, Thndr, Bosta, Synapse Analytics, almentor,
    Brimore, Dsquares, Flat6Labs, inDrive, Chatbase, Mex Digital, Scale AI, Turing, Cognite, Specright
  - **أوروبا (من `applications_log.md`):** Mistral AI, OpenAI, Cohere, Air Apps, iwoca, Pigment, SumUp, Engelhart,
    Photoroom, Tebi, LabHouse, Happy Scribe, Imnoo, Helloprint

**الدول:** مصر في كل run. الخليج (6) والدول العربية (4) وأوروبا (20) بيلفّوا بالدور، 4 دول كل run
(كل دولة بتتغطى كل ~7 ساعات). ده علشان الـ run يفضل قصير و LinkedIn ما يحجبش. زوّد `per_run` لو عايز أسرع.
- **قنوات Telegram عامة:** the_developer_guide · cs_internships · remotejobss
- **إيميلات التنبيهات (Gmail):** Wuzzuf · LinkedIn Job Alerts (اختياري واحتياطي، شوف تحت)

**كل رسالة فيها:** ⭐ درجة مطابقة مع الـ CV من 10 (الأعلى بيتبعت الأول) + زرارين **✅ قدمت / ❌ مش مهتم**.
**كل يوم الساعة 9 الصبح:** ملخص بعدد الوظايف الجديدة واللي قدمت عليه وأحسن 5 لسه مقدمتش عليهم.

- `PLAN.md` — الخطة الكاملة والمصادر والمراحل.
- `scoring.py` — درجة المطابقة (الكلمات والأوزان في `[score.keywords]` في `config.toml`).
- `telegram_bot.py` — الإرسال، الأزرار، الملخص اليومي.
- `seen_jobs.json` — كل وظيفة اتبعتت + حالتها (new / applied / ignored) + الدرجة. **ده كمان الملف اللي `job-hunter` يقرا منه.**
- `state.json` — مكان القراية في Telegram + آخر يوم اتبعت فيه الملخص.
- `config.toml` — **كل الإعدادات**: الكلمات، الاستبعاد، المصادر، كل مصدر بيشتغل كل قد إيه.
- `job_watch.py` — بيجمع من المصادر ← يفلتر ← يشيل المكرر ← يبعت.
- `sources/` — ملف لكل مصدر (LinkedIn/Indeed/Bayt عن طريق [JobSpy](../references/JobSpy)).
- `.github/workflows/job_watch.yml` — الـ cron اللي بيشغله كل ساعة.
- `../references/` — الريبوهات اللي اتعلمنا منها (برا المشروع، مش بتترفع).

> Wuzzuf مش موجود دلوقتي: الموقع عليه حماية Cloudflare بتمنع السكربتات. البديل: فعّل Job Alerts من حسابك على Wuzzuf بالإيميل.

## التشغيل (مرة واحدة)

1. **اعمل بوت Telegram:** افتح [@BotFather](https://t.me/BotFather) ← `/newbot` ← خد الـ **token**.
2. **هات الـ chat id:** ابعت أي رسالة للبوت بتاعك، وافتح
   `https://api.telegram.org/bot<TOKEN>/getUpdates` ← هتلاقي `"chat":{"id": 123456789`.
3. **اعمل repo على GitHub** وارفع الفولدر ده.
   - **Public:** دقايق Actions مجانية من غير حد، وتقدر تشغله كل ساعة. بس أي حد هيقدر يشوف `seen_jobs.json`، يعني الوظايف اللي قدمت عليها.
   - **Private:** ليك 2000 دقيقة في الشهر، والـ run بياخد ~3 دقايق، فغيّر الـ cron لكل ساعتين (`7 */2 * * *`).
     ده هيستهلك حوالي 1200 دقيقة في الشهر.
4. في الـ repo: **Settings ← Secrets and variables ← Actions ← New repository secret**:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Actions ← Job Watch ← Run workflow** علشان تجربه أول مرة. بعد كده بيشتغل كل ساعة لوحده.

### (اختياري) Wuzzuf + LinkedIn عن طريق الإيميل

Wuzzuf بقى بيتقري مباشرة من صفحات الأقسام (`sources/wuzzuf.py`)، فالإيميل بقى **احتياطي**:
بيجيب الوظايف اللي Wuzzuf بيرشحهالك إنت شخصياً، وبيشتغل لو الصفحات وقفت أو رجّعت 429.
1. على Wuzzuf و LinkedIn: فعّل **Job Alerts** للبحث اللي عايزه (مثلاً AI Engineer, Egypt) يومياً.
2. في حساب Google: فعّل **2-Step Verification**، وبعدين اعمل **App Password** من
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (16 حرف).
3. ضيف في GitHub Secrets: `GMAIL_ADDRESS` (الإيميل) و `GMAIL_APP_PASSWORD` (الـ 16 حرف).

> ⚠️ الـ App Password بيفتح الإيميل كله (البوت بيفتحه **read-only** ومش بيعلّم حاجة كمقروءة).
> **الأأمن:** اعمل Gmail جديد للبوت بس، وفي إيميلك الأساسي اعمل Filter يعمل Forward لإيميلات
> `wuzzuf.net` و `jobalerts-noreply@linkedin.com` على الإيميل الجديد، وحط الـ App Password بتاع الجديد.

## تجربة على جهازك

```bash
pip install -r requirements.txt
python test_job_watch.py   # اختبار سريع
python job_watch.py        # من غير token = بيطبع الوظايف بس (dry run)
```

## الأزرار والملخص و job-hunter

- **✅ / ❌:** دوس على الزرار، والضغطة بتتسجل في أول run بعدها (خلال ساعة)، لأن مفيش سيرفر شغال طول الوقت.
  الـ spinner ممكن يفضل يلف شوية، وده عادي. بعد التسجيل الزرار بيتغير لـ "✅ قدمت · تراجع ↩️".
- **الملخص اليومي:** `digest_hour_utc` في `config.toml` (7 UTC = 9 الصبح بتوقيت القاهرة في الشتا).
- **job-hunter:** اعمل `git pull`، وقول للسكيل:
  "قدّم على الوظايف اللي `status = new` و `score` ≥ 7 في `مشروع/job-catcher/seen_jobs.json`".

## تعديل البحث

كله في `config.toml`:
- `[filter] include / exclude` — الكلمات اللي لازم تكون (أو متكونش) في عنوان الوظيفة.
- `terms` في كل مصدر — كلمات البحث.
- `enabled = false` — تقفل مصدر.
- `every_hours = 6` — المصدر يشتغل كل 6 ساعات بس (Remotive طالب كده).
- `hours_old` — الوظايف الأحدث من كام ساعة.
- `[score.keywords]` — وزن كل كلمة (موجب = بتقرّب من الـ CV، سالب = بتبعّد). `min_score` = أقل درجة تتبعت.
- `[regions]` — **تضيف/تشيل دولة:** حطها في `gulf` أو `arab` أو `europe` (بالاسم الإنجليزي)، ولو عايزها كل run حطها في `always`.
  مدن جديدة أو كلمات زي "EMEA" تتحط في `place_words`.
- `[ats]` — **تضيف شركة:** افتح صفحة الوظايف بتاعتها، لو اللينك `jobs.lever.co/XXX` أو `jobs.ashbyhq.com/XXX`
  أو `boards.greenhouse.io/XXX` أو `XXX.recruitee.com` ... حط `XXX` في القائمة المناسبة. `locations` = الأماكن المقبولة.
- `[wuzzuf] pages` — **تضيف قسم:** افتح القسم على Wuzzuf وانسخ اللينك اللي شكله `wuzzuf.net/a/XXX-Jobs-in-Egypt`
  (من غير `?` في الآخر، لأن اللينكات اللي فيها `?` ممنوعة في robots.txt). كل صفحة زيادة = 3 ثواني زيادة في الـ run.
- `[telegram_channels]` — **تضيف قناة:** لازم تكون عامة. جرب `https://t.me/s/اسم_القناة` في المتصفح، لو ظهرت البوستات تمام.
