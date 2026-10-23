/** Business rendering. All source/model strings stay text nodes, never HTML. */
(function(window) {
  'use strict';
  function h(tag, attrs = {}, children = []) {
    const e = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'className') e.className = value;
      else if (key === 'dataset') Object.assign(e.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') e.addEventListener(key.slice(2).toLowerCase(), value);
      else if (typeof value === 'boolean') { if (value) e.setAttribute(key, ''); }
      else if (key === 'href') {
        try { const u = new URL(value, window.location.origin); if (['http:', 'https:'].includes(u.protocol)) e.setAttribute(key, u.href); } catch (_) { /* invalid data link */ }
      } else if (value !== null && value !== undefined) e.setAttribute(key, value);
    }
    for (const child of Array.isArray(children) ? children : [children]) {
      if (typeof child === 'string' || typeof child === 'number') e.appendChild(document.createTextNode(String(child)));
      else if (child instanceof Node) e.appendChild(child);
    }
    return e;
  }
  const FIELDS = {
    CL_PRODUCT_LOA_MARK:'Кредитные продукты', CL_PRODUCT_LEA_MARK:'Лизинговые продукты',
    CL_PRODUCT_DEP_MARK:'Депозитные продукты', MAINOKVED_CODE:'Основной ОКВЭД', MAINOKVED_NAME:'Основной вид деятельности',
    FTS_FLAG:'Сведения в базе ФТС', REVENUE_COMPANY:'Выручка за 2025 год',
    contract_rate_type:'тип ставки в договоре', repricing_date:'срок пересмотра ставки', refinancing_plans:'планы новых заимствований и рефинансирования',
    loan_balance:'остаток задолженности', event_available_at:'момент доступности события', independent_page_review:'сверка фрагментов банка человеком',
    financing_exposure:'чувствительность финансирования к ставке', fuel_cost_share:'доля топлива в расходах', fuel_contract_terms:'условия закупок и индексации тарифов',
    fleet_or_transport_purchases:'фактическое потребление топлива или закупки перевозок', transport_exposure:'связь деятельности с транспортными расходами',
    supported_economic_channel:'поддерживаемый механизм влияния', event_evidence_missing:'доступные на выбранную дату фрагменты события',
    source_conflict:'непротиворечивые источники', bank_evidence:'подходящий банковский тезис', contract_statement:'Условие договора', contract_rate:'Условие ставки'
  };
  const GAPS = {
    bank_sources_excluded_by_time:'Материалы банка исключены временным режимом: дата их доступности неизвестна либо публикация позже выбранной даты.',
    bank_search_not_run:'Поиск банка не выполнялся: сначала нужны допустимые по времени доказательства события.',
    bank_context_adapter_missing:'Для этой сохранённой версии нет подробной диагностики банковского контекста.',
    bank_availability_unknown:'Дата исторической доступности обзора неизвестна. Это снимок, а не проверка знаний в прошлом.',
    bank_view_dated_not_current_confirmation:'Указанная позиция относится к дате обзора; её актуальность на дату новой новости не подтверждена.',
    bank_ocr_unverified:'Найден исходный прогноз или мнение банка. Распознанный фрагмент требует ручной сверки и не принят за применимый тезис.',
    bank_source_conflict:'Источники противоречивы: банковская позиция не подтверждена.',
    bank_retrieval_no_typed_relevant_thesis:'Поиск не вернул подходящий типизированный прогноз или мнение по этому механизму. Сходство текста не доказывает позицию банка.'
  };
  const fieldLabel = x => FIELDS[x] || x;
  function humanText(text) {
    let result = String(text || '');
    for (const [code,label] of Object.entries(FIELDS)) result = result.split(code).join(label);
    return result.replaceAll('User_declared','Сообщено пользователем').replaceAll('user_declared','сообщено пользователем');
  }
  function formatDate(value, dayOnly = false) {
    if (!value) return 'дата неизвестна';
    const d = new Date(value); if (!Number.isFinite(d.getTime())) return 'дата не распознана';
    return d.toLocaleString('ru-RU',{timeZone:'Europe/Moscow',year:'numeric',month:'long',day:'numeric',...(dayOnly?{}:{hour:'2-digit',minute:'2-digit'})});
  }
  const badge = (text, cls='badge-origin') => h('span',{className:'badge '+cls},text);
  function diagnostics(title, lines) {
    return h('details',{className:'technical-details'},[h('summary',{},title),...lines.map(x=>h('div',{className:'technical-line'},String(x)))]);
  }
  function sourceLinks(ev) {
    return h('div',{className:'source-actions'},[
      h('a',{href:'/evidence/'+encodeURIComponent(ev.id),target:'_blank',rel:'noopener noreferrer'},'Сохранённый фрагмент ↗'),
      ...(ev.page?[h('a',{href:'/api/v1/sources/'+encodeURIComponent(ev.document_version_id)+'/asset#page='+ev.page,target:'_blank',rel:'noopener noreferrer'},`Исходный PDF · страница ${ev.page} ↗`)]:[])
    ]);
  }
  function renderClaim(c, openEvidence, openFeature=()=>{}) {
    const origin = {model:'Гипотеза AI',rule:'Связь по наблюдаемым данным',bank_analysis:'Материал банка',external_source:'Внешний источник',user_declared:'Сообщено вами',synthetic:'Учебные данные'}[c.origin] || 'Происхождение не определено';
    const kind = c.kind==='fact'?'Факт из источника':c.kind==='bank_view'?'Позиция банка':'Условный вывод';
    const row = h('article',{className:'claim-item',dataset:{claimId:c.id}},[
      h('div',{className:'claim-badges'},[badge(kind),badge(origin),badge({conditional:'При указанных условиях',supported:'С опорой на данные',unsupported:'Не подтверждено',conflicting:'Противоречие'}[c.check]||'Требует проверки')]),
      h('p',{className:'claim-text'},humanText(c.text)),
      ...(c.conditions||[]).map(t=>h('p',{className:'claim-conditions'},'Условие: '+humanText(t))),
      ...(c.alternatives||[]).map(t=>h('p',{className:'claim-alternatives'},'Иной сценарий: '+humanText(t)))
    ]);
    if (c.evidence_ids?.length) row.appendChild(h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>openEvidence(c)},'Источники и основания'));
    for (const fid of c.feature_ids||[]) row.appendChild(h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>openFeature(fid)},'Какие данные компании использованы'));
    return row;
  }
  function renderSection(title, claims, openEvidence, openFeature) {
    return h('section',{className:'section-box'},[h('h3',{className:'section-box-title'},title),...claims.map(c=>renderClaim(c,openEvidence,openFeature))]);
  }
  function renderAnalysisCard(a, openEvidence, selectQuestion, docs={}, openFeature=()=>{}) {
    const delivery=a.delivery||{}, mode=delivery.original_mode||delivery.mode;
    const card=h('div',{className:'analysis-card'});
    const header=h('div',{className:'analysis-header'},[
      h('h2',{className:'analysis-title'},a.event_title||'Сохранённый анализ события'),
      h('div',{className:'claim-badges'},[
        badge(a.domain==='case'?'Архивный материал':a.domain==='synthetic'?'Учебный сценарий':'Внешняя новость'),
        badge(a.status==='ready'?'Подготовлен':a.status==='partial'?'Анализ с ограничениями':'Недостаточно оснований'),
        ...(delivery.stale?[badge('Источник изменился — требуется пересмотр')]:[])
      ])
    ]);
    if (mode==='controlled') header.appendChild(h('p',{className:'notice-box warning'},'Проверочный режим: ответ AI имитируется. Настоящая платная модель не вызывалась, качество её рассуждений не проверено.'));
    else if(mode==='evidence_only') header.appendChild(h('p',{className:'notice-box warning'},'Показаны доступные источники и пробелы. Персонализированный ответ модели не сформирован.'));
    if(delivery.stale) header.appendChild(h('p',{className:'notice-box warning'},'После подготовки результата изменился источник. Повторно проверьте основания.'));
    const role=a.context?.role;
    header.appendChild(h('p',{},role==='ceo'?'Акцент генерального директора: решения и устойчивость бизнеса.':role==='cfo'?'Акцент финансового директора: условия договоров, бюджет и финансирование.':'Роль не указана: показаны общие вопросы для уточнения.'));
    if(a.missing_fields?.length) header.appendChild(h('p',{className:'notice-box info'},'Для вывода не хватает: '+a.missing_fields.map(fieldLabel).join('; ')+'.'));
    for(const n of a.notices||[]) if(n.id!=='controlled') header.appendChild(h('p',{className:n.kind==='ai_attribution'?'notice-box warning':'notice-box info'},humanText(n.text)));
    const temporal=a.temporal||{};
    header.appendChild(diagnostics('Параметры и трассируемость',[`Компания: ${a.company_id}`,`Анализ: ${a.id}`,`Событие: ${a.event_id}`,`Режим: ${delivery.mode}; исходный: ${mode}; usage: ${a.usage_status}`,`Дата среза: ${temporal.as_of}; режим: ${temporal.temporal_mode}`,`Предупреждения: ${(temporal.warnings||[]).join(', ')}`,`Версия правил: ${a.policy_version}`]));
    card.appendChild(header);
    const byId=new Map((a.claims||[]).map(c=>[c.id,c]));
    const section=name=>(a.sections?.[name]||[]).map(id=>byId.get(id)).filter(Boolean);
    const grid=h('div',{className:'analysis-sections-grid'});
    for(const [name,title] of [['happened','Что произошло'],['relevance','Почему это важно для компании']]) if(section(name).length) grid.appendChild(renderSection(title,section(name),openEvidence,openFeature));
    const consequences=section('consequences');
    for(const impact of role==='ceo'?['opportunity','risk']:['risk','opportunity']) {
      const rows=consequences.filter(c=>c.impact_kind===impact);
      if(rows.length) grid.appendChild(renderSection(impact==='risk'?'Возможные риски':'Возможности при выполнении условий',rows,openEvidence,openFeature));
    }
    const neutral=consequences.filter(c=>!c.impact_kind||c.impact_kind==='neutral');
    if(neutral.length) grid.appendChild(renderSection('Возможные последствия',neutral,openEvidence,openFeature));
    if(section('actions').length) grid.appendChild(renderSection('Что проверить перед решением',section('actions'),openEvidence,openFeature));
    const bank=h('section',{className:'section-box bank-context'},[h('h3',{className:'section-box-title'},'Позиция Альфа-Банка и её границы')]);
    for(const c of section('bank_view')) bank.appendChild(renderClaim(c,openEvidence,openFeature));
    for(const thesis of a.bank_context?.theses||[]) {
      const ev=(a.evidence||[]).find(e=>thesis.evidence_ids.includes(e.id));
      const doc=ev?docs[ev.document_version_id]:null;
      bank.appendChild(h('article',{className:'bank-excerpt'},[
        h('strong',{},doc?.title||'Аналитический обзор банка'),
        h('p',{},'Обзор от '+formatDate(thesis.issued_at,true)+(ev?.page?' · страница '+ev.page:'')),
        badge(thesis.status==='unverified'?'Цитата OCR — нужна сверка':thesis.status==='conflicting'?'Противоречивый фрагмент':'Позиция из источника'),
        h('blockquote',{},thesis.text),
        h('p',{},'Это датированный материал банка, а не подтверждённая рекомендация для вашей компании.'),
        h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>openEvidence({id:thesis.id,text:thesis.text,evidence_ids:thesis.evidence_ids})},'Проверить цитату в источнике')
      ]));
    }
    const gaps=a.bank_context?.gaps||[];
    if(gaps.length) for(const gap of gaps) bank.appendChild(h('p',{className:'source-gap'},GAPS[gap]||'Требуется дополнительная проверка материала банка.'));
    else if(!section('bank_view').length&&!a.bank_context?.theses?.length) bank.appendChild(h('p',{},a.bank_evidence_status==='background_only'?'Найдены только фоновые фрагменты. Применимый тезис банка не подтверждён.':'Подходящий тезис банка не найден; позиция банка не достраивается.'));
    grid.appendChild(bank);
    const assumptions=(a.assumptions||[]);
    if(assumptions.length) grid.appendChild(h('section',{className:'section-box'},[h('h3',{},'Сообщено вами — не подтверждено документом'),...assumptions.map(x=>h('p',{},`${fieldLabel(x.name)}: ${x.value}`))]));
    card.appendChild(grid);
    if(a.questions?.length) card.appendChild(h('section',{className:'questions-box'},[h('h3',{},'Следующие вопросы'),...a.questions.map(q=>h('button',{className:'btn btn-secondary',type:'button',onClick:()=>selectQuestion(q)},q))]));
    if(a.evidence?.length) card.appendChild(h('details',{className:'technical-details'},[h('summary',{},'Все сохранённые источники и фоновые кандидаты'),...a.evidence.map(ev=>h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>openEvidence({id:ev.id,text:'Фрагмент источника; применимость проверяется отдельно',evidence_ids:[ev.id]})},`${ev.page?'Страница '+ev.page:'Новостной фрагмент'} · ${ev.quality==='verified'?'проверка по данным':'требует сверки'}`))]));
    return card;
  }
  function renderEvidenceDrawerContent(claim, evidence, fetchDoc, docs={}) {
    const root=h('div',{},[h('p',{className:'claim-text'},humanText(claim.text))]);
    if(!evidence?.length) root.appendChild(h('p',{},'Прямые доказательства для этого вывода не привязаны.'));
    for(const ev of evidence||[]) {
      const doc=docs[ev.document_version_id];
      const box=h('article',{className:'evidence-item-card'},[
        h('h4',{},doc?.title||'Сохранённый источник'),
        h('p',{},`${doc?.publisher||'Издатель указан в источнике'} · ${formatDate(doc?.published_at,true)}${ev.page?' · страница '+ev.page:''}`),
        h('p',{className:'notice-box warning'},ev.quality==='verified'?'Фрагмент проверен по данным; применимость вывода оценивается отдельно.':'Фрагмент не проверен человеком. Сверьте числа, единицы, период и оговорки с оригиналом.'),
        h('blockquote',{className:'evidence-quote-box'},ev.quote),sourceLinks(ev),
        diagnostics('Техническое происхождение',[`Evidence: ${ev.id}`,`Документ: ${ev.document_version_id}`,`Версия источника: ${ev.source_version_id}`,`Диапазон: ${ev.span}`,`Тип: ${ev.value_kind}; качество: ${ev.quality}`,`Доступность: ${ev.available_at||'неизвестна'}`])
      ]);
      box.appendChild(h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>fetchDoc(ev.document_version_id,box)},'Показать полный сохранённый текст'));
      if(doc?.url) box.appendChild(h('a',{href:doc.url,target:'_blank',rel:'noopener noreferrer'},'Открыть сайт источника ↗'));
      root.appendChild(box);
    }
    return root;
  }
  function renderFeatureDrawer(f) {
    const value=f.value===true?'Есть':f.value===false?'Нет':f.value===null?'Неизвестно':String(f.value);
    return h('article',{className:'evidence-item-card'},[
      h('h3',{},fieldLabel(f.name)),h('p',{className:'feature-value'},value),
      h('p',{},'Источник — предоставленный обезличенный набор данных. Наличие продукта не раскрывает параметры договора, задолженность или фактическое использование.'),
      ...(f.provenance||[]).map(ref=>h('section',{},[h('p',{},`${ref.original_filename}${ref.row?' · строка '+ref.row:''}`),h('p',{},'Поле: '+fieldLabel(ref.field||f.name)),diagnostics('Проверить происхождение',[`Feature: ${f.id}`,`Source version: ${ref.source_version_id}`,`SHA256: ${ref.sha256}`,`Исходное значение: ${f.raw_value??'пусто'}`,`Нормализация: ${ref.rule_version||'не указана'}`,`Качество: ${f.quality}`,`Период: ${f.period?JSON.stringify(f.period):'не указан'}`,`Дата доступности: ${f.available_at||'неизвестна'}`])]))
    ]);
  }
  function renderFeed(items, analyze, openFeature=()=>{}) {
    if(!items?.length) return h('p',{className:'notice-box info'},'На выбранную дату нет событий с подтверждённой связью с доступными признаками компании. В строгом режиме данные с неизвестной датой доступности исключаются. Все сохранённые новости можно прочитать в соседней вкладке.');
    return h('div',{className:'feed-grid'},items.map((item,index)=>{
      const ev=item.event, rel=item.relevance;
      return h('article',{className:'feed-item-card'},[
        badge(ev.domain==='case'?'Архивный материал':ev.domain==='synthetic'?'Учебный сценарий':'Внешняя новость'),
        h('h3',{className:'feed-item-title'},ev.title),h('p',{className:'feed-item-meta'},formatDate(ev.event_at,true)),
        ...(rel?rel.reasons.map(reason=>h('div',{className:'relevance-reason'},[h('p',{},reason.text),h('small',{},reason.caveat),...reason.feature_ids.map(fid=>h('button',{className:'btn btn-secondary btn-sm',type:'button',onClick:()=>openFeature(fid)},'Посмотреть признак компании'))])):[h('p',{},'Отдельный учебный сценарий с синтетическими данными.')]),
        ...(rel?[diagnostics('Почему событие на этом месте',[`Место: ${index+1}; сумма весов: ${rel.score}`,rel.order_rule,`Правила: ${rel.policy_version}`,...rel.reasons.map(r=>`${r.text}: +${r.weight}`)])]:[]),
        h('button',{className:'btn btn-primary',type:'button',disabled:ev.domain==='synthetic'&&ev.id!=='rate_hold',onClick:()=>analyze(ev)},'Разобрать влияние на компанию')
      ]);
    }));
  }
  function renderNewsList(items, view, analyze=()=>{}) {
    return h('div',{className:'news-grid'},(items||[]).map(ev=>h('article',{className:'news-card'},[
      h('h3',{},ev.title),h('p',{},formatDate(ev.event_at,true)),
      h('button',{className:'btn btn-secondary',type:'button',onClick:()=>view(ev)},'Прочитать источник'),
      h('button',{className:'btn btn-secondary',type:'button',onClick:()=>analyze(ev)},'Проверить связь с компанией')
    ])));
  }
  function renderNewsSourcesTable(items) {
    return h('div',{className:'source-statuses'},(items||[]).map(s=>h('article',{className:'news-card'},[
      h('h3',{},s.id==='rosstat'?'Росстат':s.id==='cbr-press'?'Пресс-релизы Банка России':'События Банка России'),
      h('p',{},({ok:'Последняя загрузка успешна',partial:'Загрузка с ограничениями',error:'Источник недоступен; сохранённые новости остаются доступными',never:'Загрузка ещё не выполнялась'})[s.status]||'Состояние неизвестно'),
      h('p',{},'Последняя успешная загрузка: '+formatDate(s.last_success_at)),
      diagnostics('Подробности источника',[s.url,`Новые / повтор / отклонено: ${s.imported||0} / ${s.unchanged||0} / ${s.rejected||0}`,s.error||'Ошибок в последней загрузке нет'])
    ])));
  }
  window.AlfaUI={h,formatDate,fieldLabel,humanText,diagnostics,renderClaim,renderSection,renderAnalysisCard,renderEvidenceDrawerContent,renderFeatureDrawer,renderFeed,renderNewsList,renderNewsSourcesTable};
})(window);
