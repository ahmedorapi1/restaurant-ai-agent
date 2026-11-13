# تقرير مراجعة Restaurant AI Agent

اتعملت تحسينات صغيرة على نفس Python / LangChain / LangGraph / psycopg / PostgreSQL structure. مفيش FastAPI أو frontend أو authentication أو deployment infrastructure اتضافوا. مفيش تغيير في restaurant database schema، ومفيش اتصال بقاعدة بيانات المطعم الحقيقية أثناء الاختبارات.

## 1. كل الملفات اللي اتغيرت أو اتضافت

| الملف | نوع التغيير | السبب |
|---|---|---|
| `app/database/order_repository.py` | تعديل | UUID validation، فحص بيانات الطلب، حماية قراءة سعر وتوفر الصنف جوه transaction، وتوضيح فشل الإلغاء. |
| `app/agent/tools.py` | تعديل | شكل نتائج موحد، معالجة database errors، وفحص tool arguments من غير crash. |
| `app/agent/checkpointer.py` | تعليق توضيحي بس | توثيق إعدادات PostgresSaver الصحيحة الموجودة بالفعل؛ implementation متغيرش. |
| `app/agent/runner.py` | تعديل صغير | رفض conversation id الفاضي قبل استدعاء agent. |
| `app/main.py` | تعديل صغير | conversation id مستقل لكل تشغيل، وإمكانية استكمال history بـ `--conversation-id`، والتعامل مع empty input وEOF وCtrl+C أثناء الإدخال. |
| `tests/__init__.py` | جديد | تعطيل external tracing واستخدام OpenAI key وهمية جوه الاختبارات. |
| `tests/test_tools.py` | جديد | 5 unit tests بـ mocks للـ validation والـ tools والأخطاء. |
| `tests/test_database.py` | جديد | 7 integration tests على PostgreSQL معزولة؛ بيرفض database مش محلية أو مش فاضية أو اسمها مش المخصص للاختبار. |
| `tests/persistence_worker.py` | جديد | اختبار restart بين عمليتين Python منفصلتين باستخدام real LangChain agent وreal PostgresSaver مع offline model. |
| `REVIEW_REPORT.md` | جديد | التقرير التعليمي ده، وطريقة إعادة الاختبارات. |

`menu_repository.py` و`init_db.py` و`prompts.py` و`config.py` متغيروش. ملف `.env` متغيرش ومحتواه متعرضش. أسماء الـ four tools اتسابوا زي ما هم، بما فيهم `cancel_1order`.

## 2. الـ functions اللي اتغيرت وبتشتغل إزاي

### `app/database/order_repository.py`

- **`validate_order_id()` — جديدة:** بتستخدم `uuid.UUID` لفحص الـ id وتوحيد شكله. الـ id الغلط بيرجع `ValueError` مفهومة قبل فتح database connection.
- **`create_order()`:** بتشيل المسافات الزيادة من اسم العميل، وبترفض الاسم الفاضي أو الأطول من 100 character. بتفحص إن كل item فيه name صالح وquantity من نوع `int` موجب وفي حدود PostgreSQL `INTEGER`. بالتالي `True` و`1.5` و`"2"` مش quantities مقبولة. بتفضل تستخدم `Decimal` للحساب. كمان بترفض total أكبر من سعة `NUMERIC(10,2)` الحالية قبل أي insert.
- **`create_order()` والـ transaction:** نفس `with psycopg.connect(...)` الموجودة اتفضلت مستخدمة. النجاح بيعمل commit؛ أي exception جوه الـ block بيعمل rollback. الـ parent order وكل order items بيتكتبوا في transaction واحدة. معالجة database errors موجودة بره الـ repository في الـ tool، عشان متمنعش الـ rollback.
- **`cancel_order()`:** بتفحص UUID، وبتفضل تلغي `pending` بس عن طريق conditional UPDATE واحدة. النجاح بيرجع dict فيها `order_id` و`status`. لو مفيش row اتعدلت، بتفرق بين `Order not found` و`Only pending orders can be cancelled` باستخدام SELECT إضافية. الرسالة دي للتوضيح؛ أمان الإلغاء نفسه مصدره شرط الـ UPDATE.
- **`get_order_status()`:** بتفحص UUID، وبترجع `order_id` بالشكل الموحد مع البيانات. الطلب المفقود بيطلع `ValueError` بدل ما يرجع string مختلفة عن شكل النجاح.

### `app/agent/tools.py`

- **`OrderItemInput`:** اتضافت حدود طول الاسم، و`strict=True` والحدود الرقمية للـ quantity. ده بيمنع Pydantic من تحويل `True` أو string لكمية مقبولة بالغلط.
- **`get_all_items_of_the_menu()`:** بترجع `{"success": true, "menu": "..."}` سواء المنيو فيها أصناف أو فاضية، وبتمسك `psycopg.Error`.
- **`create_new_order()`:** نفس إنشاء الطلب، مع معالجة database errors جنب business validation errors.
- **`cancel_1order()`:** بترجع success dict أو error dict بدل string غير ثابتة.
- **`check_order_status()`:** اتشال فرع مقارنة نتيجة الـ repository بـ string، لأن الـ repository دلوقتي بترجع بيانات أو بترفع `ValueError`.
- **`_database_error()` — جديدة:** بترجع رسالة عامة، وبتسجل نوع الخطأ بس. محتوى exception ممكن يبقى فيه connection details، فعشان كده مبنرجعوش للـ model ومبنسجلوش. الرسالة بتنبه إن نتيجة write ممكن تبقى غير مؤكدة لو الاتصال انقطع أثناء commit؛ مفيش automatic retry لعملية كتابة.
- **`_validation_error()` — جديدة:** متوصلة بـ `handle_validation_error` لكل tool عشان arguments الناقصة أو الغلط متعملش crash. LangChain بتستخدم text في المسار ده، فبنرجع JSON text فيها نفس `success` و`error` keys. الـ normal direct tool calls بترجع dict؛ الـ ToolMessage content في الحالتين بتبقى text يفهمها الـ model.

شكل النجاح فيه `success: true` ومعاه بيانات العملية. شكل الفشل فيه `success: false` و`error`. الأسعار بتفضل text لتجنب مشاكل JSON مع `Decimal`.

### الـ runner والـ CLI

- **`create_checkpointer()`:** مفيش behavioral change؛ اتضاف docstring بس. `PostgresSaver.from_conn_string()` في النسخة المثبتة بيضبط `autocommit=True` و`row_factory=dict_row` و`prepare_threshold=0` بالفعل.
- **`create_agent_runner()` / `run_agent_turn()`:** اتضاف guard للـ conversation id الفاضي. الـ checkpointer لسه مفتوح طول عمر الـ runner، و`setup()` بتتنده قبل الاستخدام، والـ id بيتبعت كـ `configurable.thread_id`.
- **`main()`:** بدل ما كل التشغيلات تدخل على `conv_1`، كل تشغيل جديد بياخد UUID للمحادثة وبيطبعه. عشان تكمل conversation قديمة لازم تستخدم نفس id. ده conversation identifier، مش authentication.

تشغيل محادثة جديدة من project root:

```powershell
.\.venv312\Scripts\python.exe -m app.main
```

استكمال نفس المحادثة بعد restart:

```powershell
.\.venv312\Scripts\python.exe -m app.main --conversation-id "YOUR-SAVED-CONVERSATION-ID"
```

لو عايز تكمل history القديمة اللي التطبيق كان بيحفظها تحت `conv_1`:

```powershell
.\.venv312\Scripts\python.exe -m app.main --conversation-id conv_1
```

## 3. الـ SQL بالظبط

الاستعلام الوحيد الموجود اللي اتعدل نصه هو menu lookup جوه `create_order()`؛ اتضاف `FOR SHARE`:

```sql
SELECT id, price, is_available
FROM menu_items
WHERE LOWER(name) = %s
FOR SHARE;
```

ده بيخلي تغيير أو حذف نفس menu row يستنى لحد ما transaction إنشاء الطلب تخلص. بالتالي السعر والتوفر اللي اتقريوا ميتغيروش أثناء تسجيل الطلب.

اتضاف الاستعلام ده جوه `cancel_order()` لو conditional UPDATE مرجعتش row:

```sql
SELECT status FROM orders WHERE id = %s;
```

الـ UPDATE الآمنة الموجودة بالفعل **نصها متغيرش**:

```sql
UPDATE orders
SET status = 'cancelled'
WHERE id = %s AND status = 'pending'
RETURNING id;
```

الـ two INSERT statements في `create_order()`، والـ SELECT الأساسية في `get_order_status()`، واستعلام menu retrieval متغيروش. كل user values لسه بتتبعت كـ parameters باستخدام `%s`، مش string interpolation.

SQL جوه `tests/test_database.py` بس: فحص `pg_tables` عشان نرفض database مستخدمة، counts قبل وبعد rollback، وUPDATE لحالات orders الاختبارية. الـ UPDATE دي بتستخدم ids اتولدت جوه نفس test database. مفيش DELETE أو TRUNCATE أو DROP لبيانات orders. `init_db_and_seed_data()` الموجودة اتشغلت في test database الفاضية بس؛ و`PostgresSaver.setup()` أنشأت checkpoint tables الخاصة بالمكتبة فيها بالطريقة الموجودة بالفعل.

## 4. الاختبارات ونتايجها

استخدمت Python 3.12 من `.venv312` و`unittest` من standard library، من غير إضافة test dependency. الـ integration tests اتشغلت على PostgreSQL 17 في Docker container مؤقتة، مربوطة بـ loopback بس، وبياناتها مؤقتة ومنفصلة عن database المطعم.

النتيجة النهائية: **12 tests نجحوا في 24.150 ثانية** — 5 unit و7 integration. بعد الاختبار اتوقفت واتشالت container الاختبار المؤقتة وبياناتها. اتفحص كمان التشغيل من غير test database URL: الـ 5 unit tests نجحوا والـ 7 integration tests اتعملهم skip بأمان.

تفاصيل الاختبارات:

| الاختبار | اللي أثبته |
|---|---|
| `test_invalid_uuid_never_connects` | UUID غلط أو input شبه SQL injection بيرجع error من غير database connection. |
| `test_menu_and_empty_menu_use_same_envelope` | المنيو الفاضية والمليانة ليهم نفس success envelope. |
| `test_database_errors_are_safe_for_all_four_tools` | الـ four tools بتمسك database errors، ومحتوى exception مش بيتسرب للرد أو logging. |
| `test_invalid_tool_arguments_do_not_raise` | quantities الغلط والحقول الناقصة بترجع structured error. |
| `test_bad_repository_input_never_connects` | repository بترفض الطلبات الغلط قبل الاتصال. |
| `test_creation_menu_and_status` | إنشاء order فعلي، total صحيحة، UUID، pending status، وقراءة status من الـ tool. |
| `test_missing_unavailable_and_overflow_reject_without_orders` | صنف مفقود أو unavailable أو total أكبر من الحدود مبيعملش order. |
| `test_cancellation_all_existing_status_rules` | الإلغاء متاح لـ pending بس، ومرفوض لكل الحالات التانية الموجودة في schema. |
| `test_unknown_uuid_returns_not_found` | UUID صحيحة بس مش موجودة بترجع error مفهومة في status والإلغاء. |
| `test_insertion_failure_rolls_back_parent_and_items` | فشل متعمد عند ثاني child insert بيرجع parent والـ first child كمان؛ counts قبل وبعد متساوية. |
| `test_concurrent_cancellation_succeeds_only_once` | محاولتين إلغاء متزامنتين: واحدة بس بتنجح. |
| `test_conversation_survives_process_restart` | process أولى بتكتب history وتقفل؛ process جديدة بتسترجع الرسالة القديمة بنفس thread id. thread مختلفة ليها history منفصلة، والـ id الفاضي مرفوض. |

اختبار restart بيستخدم real `create_agent()` وreal `PostgresSaver` وPostgreSQL فعلية؛ الـ chat model بس هي اللي offline ومبدلة بـ mock. مفيش OpenAI requests اتبعتت. ده إثبات لتخزين واسترجاع الـ messages، مش اختبار لجودة إجابات LLM أو التزامها بالتأكيد على الطلب.

اتعمل كمان Python syntax check بـ `compileall`، وفحص `python -m app.main --help`.

### إعادة الاختبارات

Unit tests فقط، من غير database:

```powershell
.\.venv312\Scripts\python.exe -m unittest tests.test_tools -v
```

كل الاختبارات باستخدام **database اختبار محلية فاضية ومؤقتة فقط**:

```powershell
$env:RESTAURANT_TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:TEST_PORT/restaurant_agent_test'
.\.venv312\Scripts\python.exe -m unittest discover -s tests -t . -v
```

بدل `TEST_PORT` ببورت database الاختبار. المثال ده مناسب لـ container اختبار محلية من غير password؛ مش production connection string. متستخدمش `DATABASE_URL` الحقيقية. إعادة integration suite محتاجة database اختبار فاضية جديدة؛ الاختبارات بترفض واحدة فيها tables بدل تنظيفها أو حذف بياناتها. من غير `RESTAURANT_TEST_DATABASE_URL` الـ integration tests بتتعملها skip.

## 5. مشاكل ومخاطر لسه موجودة قبل production

1. **Customer confirmation:** شرط التأكيد موجود في `SYSTEM_PROMPT` وفي tool description، لكن مش مفروض server-side. الـ model ممكن تغلط أو تتأثر بـ prompt injection. متعتبرش prompt ضمان business rule قطعي.
2. **Idempotency:** لو order اتسجلت وبعدها التطبيق وقف قبل حفظ checkpoint أو رجوع الرد، إعادة نفس الطلب ممكن تعمل duplicate. order transaction والـ checkpoint writes مش transaction واحدة. إضافة idempotency محتاجة تصميم مستقل، وممكن schema change؛ متعملتش ضمن المراجعة الصغيرة دي.
3. **Uncertain commits:** انقطاع الاتصال وقت commit ممكن يسيب العميل مش عارف العملية نجحت ولا لأ. لازم مراجعة الطلب الموجود قبل إعادة write، ولو create رجعت من غير order id ممكن تحتاج مراجعة تشغيلية مباشرة. مفيش blind automatic retries اتضافت.
4. **Conversation isolation:** UUID جديدة بتقلل الخلط العرضي اللي كان حاصل مع `conv_1`؛ لكنها مش ownership check. أي حد معاه conversation id ممكن يحاول يستخدمها في التطبيق الحالي، ونفس الكلام للـ order UUID. authentication وauthorization محتاجين يتصمموا لما يبقى ليهم scope واضح.
5. **Operations:** مفيش explicit database lock/statement/connect timeouts أو pooling أو سياسة retries مدروسة. مشاكل startup وOpenAI API لسه ممكن توقف الـ CLI؛ معالجة الخطأ جوه tools مش معناها حماية كل التطبيق. محتاج إدارة آمنة للأخطاء والـ logs قبل النشر.
6. **Checkpoint lifecycle:** الـ history فيها بيانات عملاء وممكن تكبر. محتاجة access controls وbackup وretention وخطة migrations. `setup()` الموجودة ممكن تنفذ migrations خاصة بالمكتبة وقت التشغيل؛ محتاجة حساب database بصلاحيات مناسبة، وبعدها فصل initialization عن runtime في مرحلة تجهيز النشر.
7. **Menu name matching:** قاعدة البيانات عندها `UNIQUE(name)` لكن البحث بـ `LOWER(name)`. أسماء تختلف في uppercase/lowercase بس ممكن تبقى ambiguous. متغيرتش schema عشان نعالج ده؛ راجع بيانات المنيو وسياسة تسمية الأصناف قبل النشر.
8. **Reproducibility and secrets:** مفيش dependency manifest ظاهر في المشروع؛ ثبت versions اللي هتعتمد عليها في خطوة مستقلة. خلي `.env` خارج version control، واستخدم secrets management عند النشر. الاختبارات مش security audit ومش load test.

## 6. تذاكر إيه بعد كده؟

1. **Python exceptions وcontext managers:** الفرق بين `raise` و`except`، وليه مكان الـ exception handler مهم للـ rollback.
2. **PostgreSQL transactions:** `COMMIT` و`ROLLBACK` وrow locks و`FOR SHARE`، وإزاي conditional UPDATE بتقلل race conditions.
3. **Pydantic validation:** `Field` و`strict=True` وحدود الأرقام، والفرق بين type conversion وvalidation.
4. **UUID وparameterized SQL:** صحة شكل الـ id غير وجوده في database، وUUID مش authorization.
5. **LangGraph persistence:** `thread_id` وcheckpoints و`PostgresSaver`، والفرق بين conversation history وbusiness data.
6. **Testing:** `unittest.mock.patch` وunit tests مقابل integration tests، وإزاي تعمل failure injection وتختبر rollback وrestart.
7. **Idempotency:** ليه استدعاء tool مرتين مش بالضرورة نفس النتيجة، وإزاي تمنع duplicate side effects قبل أي production launch.

مراجع أساسية: [Psycopg transaction management](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)، و[LangChain PostgresSaver reference](https://reference.langchain.com/python/langgraph.checkpoint.postgres).
