# سُفرة | Restaurant AI Agent

واجهة chat بالمصري للمشروع الحالي. React وTypeScript وTailwind للواجهة، وFastAPI للـ API. نفس LangChain agent وأدوات الطلبات وPostgreSQL repositories متوصلين بالويب وبالـ CLI.

## تشغيل الـ demo محليًا

المطلوب Docker Desktop شغّال، وPython 3.12، وNode.js 22.12 أو أحدث. الردود الحية بتحتاج مفتاح OpenAI صالح. نفّذ الأوامر من فولدر المشروع في PowerShell.

### أول مرة على نسخة جديدة

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Push-Location frontend
npm.cmd ci
Pop-Location
```

لو `.venv312` والـ dependencies جاهزين، تخطّى تثبيتهم. على نسخة جديدة، حط كلمة مرور محلية عشوائية في `SOFRA_DB_PASSWORD` واستخدم نفس القيمة في جزء كلمة المرور من `DATABASE_URL`. استخدم user باسم `sofra` وقاعدة `restaurant_demo` على `127.0.0.1`. لو `5432` مش متاح، حط `55432` في `SOFRA_DB_PORT` وفي `DATABASE_URL`. حط `OPENAI_API_KEY` في `.env` أو في environment بتاعة السيرفر. خليك مستخدم نفس إعدادات PostgreSQL بعد إنشاء الـ volume لأن تغيير قيم Compose مش بيغير كلمة مرور الدور الموجود.

### التشغيل اليومي

```powershell
Set-Location -LiteralPath 'D:\agintic ai'
.\start-local.ps1
```

السكربت بيبدأ PostgreSQL 17 من Compose ويستنى الـ healthcheck. بعد كده بيشغّل الـ schema والـ seed الأصلية والـ checkpoint setup، وبيبدأ FastAPI على `8000` والواجهة على `5173`. تقدر تعيد تشغيله، والـ seed بتتخطى الأصناف الموجودة. لوجات الخدمات اللي السكربت شغّلها في `.local/`.

افتح [سُفرة على 5173](http://127.0.0.1:5173/) أو [FastAPI على 8000](http://127.0.0.1:8000/docs). أول زيارة بتنشئ demo identity للمتصفح. Refresh بيرجع لنفس المحادثة، و«محادثة جديدة» بتبدأ thread جديدة لنفس العميل.

### لو حابب تشغّل الخدمات منفصلة

```powershell
docker compose up -d --wait postgres
.\.venv312\Scripts\python.exe -m app.database.bootstrap_local
.\.venv312\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

وفي PowerShell تانية من فولدر المشروع:

```powershell
Set-Location frontend
npm.cmd run dev
```

لو الـ virtual environment عندك باسم `.venv`، استخدم نفس الأوامر بالمسار ده. `.\start-local.ps1 -DatabaseOnly` بتشغّل PostgreSQL وتجهّز الجداول فقط.

الـ container اسمه `restaurant-postgres`، وسياسة إعادة تشغيله `unless-stopped`. الـ named volume هو `sofra-restaurant_restaurant_postgres_data`. لإيقاف PostgreSQL مع حفظ البيانات استخدم `docker compose stop postgres`. أوقف FastAPI قبل PostgreSQL، وبعدها شغّل `.\start-local.ps1` لما ترجع تشتغل عشان الـ checkpointer يفتح اتصال جديد.

### لو ظهر خطأ اتصال

اختبر `Invoke-WebRequest http://127.0.0.1:8000/health` و`docker compose ps`. FastAPI بتراجع اتصال PostgreSQL والـ web schema قبل بدء التشغيل.

- **200 من `/health`** يعني إن الـ API وقاعدة البيانات متاحين.
- **503** يعني إن الـ API رد، لكن اتصال قاعدة البيانات فشل. راجع الـ database والـ server log.
- **502 عند إرسال رسالة** يعني إن المساعد فشل في تجهيز الرد. لو الـ log فيه `OpenAIAuthenticationError`، راجع `OPENAI_API_KEY` وبيانات المشروع محليًا وأعد تشغيل FastAPI بعد تحديثها. [OpenAI Docs](https://developers.openai.com/api/docs/guides/error-codes)

## صورة المنيو

- `MENU_IMAGE_PATH=assets/menu.png` بيعرض الملف الحالي من `/api/menu/image`. ممكن تغيّر المسار لملف صورة رفعته بنفسك.
- `MENU_IMAGE_URL=https://...` بياخد الأولوية لما يبقى public HTTPS URL لصورة المنيو، زي رابط Cloudflare R2 بعد ما ترفعه يدويًا. لو مفيش صورة صالحة، الـ tool بترجع رسالة إن الصورة مش متاحة.
- `get_menu_image` بترجع image reference من الإعدادات. الواجهة بتعرضها في الـ chat وبتفتحها بالحجم الكامل. الأسعار والتوفر وإنشاء الطلبات مصدرهم PostgreSQL.
- `app/database/menu_catalog.py` فيه تفريغ أسماء وأسعار الصورة الحالية. الأصناف اللي التوفر بتاعها مش مؤكد بتظهر في القائمة ومبتدخلش في الطلب. عشان توفّق قاعدة قديمة مع نفس الصورة، خُد backup لسجلات `public.menu_items` وبعدين استخدم `python -m app.database.sync_menu <backup.json>`. الـ sync بيتحقق من الملف والـ backup، وبيحتفظ بـ IDs للأصناف المطابقة ويخفي السجلات القديمة اللي مش في الصورة.

## تقرير التنفيذ

| الملفات | الدور |
| --- | --- |
| `app/api.py`, `app/web_chat.py` | FastAPI endpoints للـ demo identity والـ profile والـ chats والـ confirmations والصورة. بيرجعوا الرسائل اللي تظهر للعميل من LangGraph state. |
| `app/database/migrate_web.py`, `app/database/customer_repository.py` | الـ migration بتعيد استخدام `customers` الموجودة، وتضيف التفضيلات، و`conversations`، و`orders.customer_id` اختيارية. الـ repository بتربط كل thread بعميل وبتمنع تنفيذ دورين متزامنين لنفس المحادثة. |
| `app/agent/runner.py`, `app/agent/context.py`, `app/agent/tools.py`, `app/database/order_repository.py` | إعادة استخدام الـ agent والـ tools. الويب بيمرر الـ customer ID من السيرفر للـ tools، ويوقف order writes لحد موافقة العميل. استعلامات status والإلغاء للويب مقيدة بالمالك. |
| `app/agent/menu_image.py` | الـ tool الخاصة بالصورة الثابتة وإعدادات المسار والرابط. |
| `frontend/` | شاشة شات عربية RTL، رسائل، عرض صورة، تأكيد، ومحادثة جديدة. |
| `requirements.txt`, `.env.example`, `frontend/.env.example`, `.gitignore` | dependencies، إعدادات التشغيل، واستبعاد الملفات المحلية والأسرار. |
| `docker-compose.yml`, `start-local.ps1`, `app/database/bootstrap_local.py` | تشغيل PostgreSQL المحلية بالـ volume والـ healthcheck، وتشغيل الإعدادات الموجودة والخدمات بأمر واحد. |
| `tests/test_web.py`, `tests/web_model.py` | API وtool tests بتشغّل الـ graph والـ repositories الحقيقيين بـ offline model وقاعدة اختبار مؤقتة. |

الـ migration إضافة فقط وبتشتغل في transaction واحدة. شغّلها بشكل صريح على database المقصودة. الطلبات القديمة اللي ملهاش owner بتفضل متاحة في الـ CLI الإداري المحلي، وطلبات الويب بتتربط بمالكها عند الإنشاء.

### طبقتين للذاكرة

- **Conversation memory** — `PostgresSaver` بيخزن الـ messages والـ approvals لكل `thread_id`. زر «محادثة جديدة» بيعمل thread مختلفة. الـ threads والطلبات القديمة بتفضل محفوظة.
- **Customer memory** — الـ profile والتفضيلات المحفوظة بتتدار من الـ backend عبر `/api/customers/me` باستخدام نفس bearer token. عند كل agent turn بيتحمّل الاسم والتفضيلات من PostgreSQL، وبيتبعتوا كسياق للـ model. رقم الهاتف والعنوان مش بيتضافوا للـ model prompt.

الـ demo بينشئ رقم هاتف تجريبي محجوز لكل هوية. الوصول للـ profile والـ chats بيحتاج bearer token عشوائي خاص بالمتصفح، والـ database بتخزن الـ hash بتاعه. إدخال رقم الهاتف أو معرفة thread ID مبيعملش تسجيل دخول. استخدم بيانات وهمية في الـ demo، بما فيها الـ profile والرسائل.

### تأكيد الطلبات

الـ agent بتحفظ مسودة قابلة للتعديل في الـ checkpoint وتعرض الأصناف وإجمالي أسعارها. «أيوه» أو «كمّل الطلب» يبدأ جمع الاسم الكامل والموبايل وعنوان التوصيل. بعد اكتمال البيانات السيرفر بيعرض ملخص جديد ويطلب «أكد الطلب». كل ملخص له معرّف تأكيد فريد، والسيرفر بيراجع السعر والتوفر قبل الحفظ. رسوم التوصيل بتظهر كرسوم غير محددة. إلغاء الأوردر المسجل لسه بيحتاج موافقة وبيشترط حالة `pending`.

قاعدة موجودة تحتاج تشغيل `python -m app.database.migrate_web` من بيئة المشروع قبل إعادة تشغيل السيرفر. التحديث بيضيف `orders.delivery_phone` و`orders.delivery_address` لحفظ بيانات التوصيل المؤكدة مع كل أوردر.

## الاختبارات

Unit tests بدون database:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tools tests.test_web.MenuImageTests -v
```

للـ integration tests، جهّز **قاعدتين اختبار فاضيين ومنفصلين** باسم `restaurant_agent_test` و`restaurant_web_test` في PostgreSQL 17 اختبارية. اضبط روابط الاتصال في PowerShell:

```powershell
$env:RESTAURANT_TEST_DATABASE_URL = '<local URL for restaurant_agent_test>'
$env:RESTAURANT_WEB_TEST_DATABASE_URL = '<local URL for restaurant_web_test>'
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

الـ suites بترفض قواعد اختبار فيها جداول موجودة. إعادة الـ integration tests بتحتاج قواعد فاضية جديدة. الـ LLM requests متبدلة بـ offline model.

للـ frontend:

```powershell
cd frontend
npm.cmd run build
```

التحقق المحلي نجح في 26 اختبار، وبناء TypeScript/Vite نجح. الاختبارات بتغطي حفظ المحادثات، وعزل العملاء، وتطابق المنيو، والأسماء البديلة، وتعديل المسودة، وتغير السعر قبل التأكيد، ومنع تكرار الطلب.

## إعدادات الاستضافة المستقبلية

- **Cloudflare Pages** — project root `frontend`، build command `npm run build`، وoutput directory `dist`. اضبط `VITE_API_URL` على عنوان HTTPS للـ API. متغيرات `VITE_` عامة.
- **Render أو Railway** — ثبّت `requirements.txt`، واستخدم `uvicorn app.api:app --host 0.0.0.0 --port $PORT`. شغّل الـ migration على قاعدة الاختبار أو الـ staging قبل startup. استخدم environment variables للسيرفر، واضبط `FRONTEND_ORIGINS` على الـ Pages origins المحددة.
- **PostgreSQL managed وCloudflare R2** — خلي `DATABASE_URL` سر على السيرفر، واستخدم `MENU_IMAGE_URL` للرابط العام للصورة بعد رفعها يدويًا.

قبل production، استبدل demo identity بتسجيل دخول حقيقي وملكية موثقة، وحدد مدة صلاحية الجلسات وسياسة حفظ البيانات وrate limits. رمز الـ demo محفوظ في المتصفح، ففقدانه بيفقد الوصول للجلسة. لو التطبيق وقع بعد database commit وقبل تحديث الـ checkpoint، write retry ممكن يكرر الطلب. الـ demo بتمنع إعادة تأكيد خلص بالفعل وبتحتاج idempotency على مستوى الطلب عشان تغطي crash window.
