# جهازك: التقديم الأوتوماتيك (Antigravity + مراجعة)

كل 30 دقيقة جهازك بيعمل الآتي:
1. يقرا **Wuzzuf + Bayt**. الاتنين بيرفضوا سيرفرات GitHub (403)، لكن جهازك لأ.
2. **يقدّم** على الوظايف اللي في الطابور، وهما نوعين:
   - 🤖 أوتوماتيك: أي وظيفة درجتها ⭐ 8 أو أكتر.
   - 🚀 الوظايف اللي انت دوست عليها في Telegram.
3. يبعتلك على Telegram: **سكرين الإثبات + الأسئلة اللي اتسألت + إجاباته + نتيجة المراجعة**.

```
Claude Sonnet (cv_maker.py) بيعمل CV الوظيفة الجاية ── في نفس الوقت ── agy بيقدّم على الحالية
        │  (لو فشل: agy يختار CV جاهز من cv_menu.md)
agy (يملأ ويقف قبل Submit) ── answers.json
        │
audit.py: قواعد candidate_facts (ببلاش، فوري)
        │  اللي القواعد مش متأكدة منه (مرتب، نص حر، سؤال جديد)
claude -p: مراجعة على candidate_facts.md
        │  أي غلطة = ⛔ مايتبعتش، ويوصلك السبب
agy (Submit + سكرين) ── result.json ── Telegram
```

## الإعداد (مرة واحدة)

1. **صلاحيات Antigravity:**
   - انسخ `agy-cli-settings.json` لـ `C:\Users\Elsou\.gemini\antigravity-cli\settings.json`.
   - الملف ده بيسمح بحاجتين بس: المتصفح (Playwright)، والكتابة جوا `D:\MY\cv\cloude_CV`.
   - وبيمنع: قراية مفتاح التشفير، والكتابة في `candidate_facts.md`.
2. **Telegram للجهاز:** `.venv\Scripts\python pc\setup_pc.py`. هيطلب الـ token مخفي، وبعدين ابعت للبوت أي رسالة.
3. **Playwright:** شغّل `D:\MY\cv\cloude_CV\start_playwright_mcp.bat` وسيب Edge مفتوح ومسجّل دخول.
   علشان رفع الـ CV يشتغل: `edge://extensions` ← Playwright MCP Bridge ← Details ← فعّل **Allow access to file URLs**.
4. **مكتبة الـ CV:** `.venv\Scripts\python -m pip install pypdf` (علشان `build_cv.py`).
5. **الجدولة:** من PowerShell شغّل `pc\install_task.ps1`، فبيتشغل كل 30 دقيقة.

## الملفات
- `pc_runner.py`: البرنامج نفسه.
- `audit.py`: المراقب.
- `review.py`: مراجعة Claude.
- `cv_maker.py`: Sonnet بيكتب العنوان والـ profile وترتيب المهارات والمشاريع من `candidate_facts.md` بس، و `build_cv.py` يطلّع الـ PDF في `cloude_CV\auto_cvs\`.
- `prompts.py`: التعليمات اللي بتروح لـ Antigravity.
- `facts_rules.toml`: **ملف خاص بيك ومبيترفعش.** فيه الإجابات المقبولة من `candidate_facts.md`، ولو عدّلت `candidate_facts` لازم تعدّله معاه.
- `receipts/<id>/` على جهازك فيه:
  - `answers.json` و `audit.json` و `result.json` و `confirmation.png`.
  - سجلات `agy`.
- `receipts/index.jsonl`: سطر لكل وظيفة. ده اللي أراجعه لما تقولي "راجع تقديمات النهارده".

## إعدادات (`config.toml` ← `[apply]`)
- `auto_min_score = 8`: أقل درجة تتقدم أوتوماتيك.
- `per_cycle = 4`: أقصى عدد تقديمات في كل 30 دقيقة (يعني 8 في الساعة).
- `claude_review = true`: Claude يراجع الإجابات اللي القواعد مش متأكدة منها.

## مخاطر
- **LinkedIn** ممكن يقفل الحساب لو التقديم بقى أوتوماتيك. الحد 8 في الساعة بيقلل الخطر، بس مش بيلغيه.
- **أي CAPTCHA أو تسجيل دخول أو سؤال مش موجود في الملف = ✋ محتاجك.** مفيش أي محاولة لتخطيه.
