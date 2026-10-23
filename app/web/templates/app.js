/**
 * Main Application Controller for Alfa-Bank News Analytics Prototype.
 * Handles user interactions, polling lifecycle, state transitions, and UI events.
 */
(function(window) {
  'use strict';

  const { AlfaApi, AlfaFixtures, AlfaUI } = window;
  const { h } = AlfaUI;

  // Global State
  const state = {
    isLoggedIn: false,
    actor: 'alice',
    context: null,
    companies: [],
    currentCompanyId: null,
    currentRole: 'unknown',
    temporalMode: 'snapshot_demo',
    asOf: '2026-10-22T12:00:00Z',
    feed: [],
    news: [],
    newsSources: [],
    activeAnalysis: null,
    activeJob: null,
    conversation: [],
    isFixtureMode: false,
    selectedFixtureScenario: 'ready',
    activePollingToken: 0,
    evidenceDrawerOpen: false,
    evidenceRequestToken: 0,
    activeTab: 'feed',
    screen: 'home',
    documents: {}
  };

  // DOM Elements cache
  const dom = {};

  function initDom() {
    dom.homeArea = document.getElementById('home-area');
    dom.homeSummary = document.getElementById('home-summary');
    dom.homeButton = document.getElementById('btn-home');
    dom.analyticsButton = document.getElementById('btn-analytics');
    dom.analyticsEntry = document.getElementById('btn-analytics-entry');
    dom.roleSource = document.getElementById('role-source');
    dom.shellNav = document.getElementById('shell-nav');
    dom.savedId = document.getElementById('saved-analysis-id');
    dom.openSaved = document.getElementById('btn-open-saved');
    dom.authArea = document.getElementById('auth-area');
    dom.appArea = document.getElementById('app-area');
    dom.actorSelect = document.getElementById('actor-select');
    dom.btnLogin = document.getElementById('btn-login');
    dom.btnLogout = document.getElementById('btn-logout');

    dom.userCapsule = document.getElementById('user-capsule');
    dom.userName = document.getElementById('user-name');
    dom.userCompany = document.getElementById('user-company');

    dom.contextRibbon = document.getElementById('context-ribbon');
    dom.companySelect = document.getElementById('company-select');
    dom.roleSelect = document.getElementById('role-select');
    dom.temporalModeSelect = document.getElementById('temporal-mode-select');
    dom.asOfInput = document.getElementById('asof-input');
    dom.btnApplyContext = document.getElementById('btn-apply-context');
    dom.contextVersionBadge = document.getElementById('context-version-badge');

    dom.fixtureBanner = document.getElementById('fixture-mode-banner');
    dom.btnToggleFixtures = document.getElementById('btn-toggle-fixtures');
    dom.fixtureScenarioSelect = document.getElementById('fixture-scenario-select');

    dom.appTabs = document.getElementById('app-tabs');
    dom.tabBtns = document.querySelectorAll('.tab-btn');
    dom.tabFeed = document.getElementById('tab-feed-content');
    dom.tabNews = document.getElementById('tab-news-content');
    dom.tabStatus = document.getElementById('tab-status-content');

    dom.feedContainer = document.getElementById('feed-container');
    dom.jobProgressContainer = document.getElementById('job-progress-container');
    dom.analysisContainer = document.getElementById('analysis-container');
    dom.followupContainer = document.getElementById('followup-container');
    dom.followupTextarea = document.getElementById('followup-text');
    dom.followupCharCount = document.getElementById('followup-char-count');
    dom.btnFollowupSubmit = document.getElementById('btn-followup-submit');
    dom.assumptionToggle = document.getElementById('assumption-toggle');
    dom.assumptionFields = document.getElementById('assumption-fields');
    dom.assumptionName = document.getElementById('assumption-name');
    dom.assumptionValue = document.getElementById('assumption-value');
    dom.conversationThread = document.getElementById('conversation-thread');
    dom.followupErrorBox = document.getElementById('followup-error-box');

    dom.newsContainer = document.getElementById('news-container');
    dom.newsSourceSelect = document.getElementById('news-source-filter');
    dom.btnRefreshNews = document.getElementById('btn-refresh-news');

    dom.statusSourcesContainer = document.getElementById('status-sources-container');

    dom.drawerBackdrop = document.getElementById('evidence-drawer-backdrop');
    dom.drawerCloseBtn = document.getElementById('btn-close-drawer');
    dom.drawerBody = document.getElementById('drawer-body');
    dom.drawerTitle = document.getElementById('drawer-title');

    dom.globalErrorAlert = document.getElementById('global-error-alert');
  }

  function showGlobalError(message, code = null) {
    if (!message) {
      dom.globalErrorAlert.classList.add('hidden');
      dom.globalErrorAlert.textContent = '';
      return;
    }
    dom.globalErrorAlert.classList.remove('hidden');
    dom.globalErrorAlert.replaceChildren(h('p', {}, message));
    if (code) dom.globalErrorAlert.appendChild(h('details', {className:'technical-details'}, [h('summary', {}, 'Диагностика'), h('p', {}, code)]));
  }

  function clearGlobalError() {
    showGlobalError(null);
  }

  // Polling Lifecycle Controller
  function stopActivePolling() {
    state.activePollingToken++;
    dom.jobProgressContainer.classList.add('hidden');
    dom.jobProgressContainer.innerHTML = '';
  }

  async function pollJob(job, token) {
    const pollUrl = job.poll_url;
    const jobId = job.id;
    let attempts = 0;
    // Allow the bounded 300 s live job to finish provider accounting; cancellation
    // and context tokens still stop stale results immediately. No POST is retried.
    const maxAttempts = 550;

    dom.jobProgressContainer.classList.remove('hidden');
    renderJobProgress(job, attempts);

    while (attempts < maxAttempts) {
      if (token !== state.activePollingToken) {
        return; // Polling cancelled or switched context
      }

      await new Promise(r => setTimeout(r, 600));
      attempts++;

      if (token !== state.activePollingToken) return;

      try {
        let currentJob;
        if (state.isFixtureMode) {
          // In fixture mode, resolve quickly
          currentJob = {
            id: jobId,
            status: 'succeeded',
            result_id: AlfaFixtures.SCENARIOS[state.selectedFixtureScenario].analysis.id,
            poll_url: pollUrl
          };
        } else {
          currentJob = await AlfaApi.getJob(jobId);
        }

        if (token !== state.activePollingToken) return;
        renderJobProgress(currentJob, attempts);

        if (['succeeded', 'partial'].includes(currentJob.status) && currentJob.result_id) {
          dom.jobProgressContainer.classList.add('hidden');
          const resultId = currentJob.result_id || jobId;
          let analysis;
          if (state.isFixtureMode) {
            analysis = AlfaFixtures.SCENARIOS[state.selectedFixtureScenario].analysis;
          } else {
            analysis = await AlfaApi.getAnalysis(resultId);
          }
          if (token === state.activePollingToken) {
            setAnalysisResult(analysis);
          }
          return;
        }

        if (currentJob.status === 'failed') {
          dom.jobProgressContainer.classList.add('hidden');
          const err = currentJob.error ? (currentJob.error.error || currentJob.error) : { code: 'job_failed', message: 'Обработка задания завершилась ошибкой.' };
          showGlobalError(err.message, err.code);
          return;
        }

        if (currentJob.status === 'cancelled') {
          dom.jobProgressContainer.classList.add('hidden');
          showGlobalError('Задание было отменено.', 'job_cancelled');
          return;
        }
      } catch (err) {
        if (token !== state.activePollingToken) return;
        if (err.status && err.status < 500) {
          stopActivePolling(); clearAnalysis(); closeEvidenceDrawer();
          showGlobalError(err.message, err.code); return;
        }
        // Continue polling on transient network error up to attempts
        if (attempts >= maxAttempts) {
          dom.jobProgressContainer.classList.add('hidden');
          showGlobalError(`Превышено время ожидания ответа воркера (${jobId}).`, 'timeout');
          return;
        }
      }
    }

    if (token === state.activePollingToken) {
      dom.jobProgressContainer.classList.add('hidden');
      showGlobalError('Превышено максимальное время ожидания задания.', 'timeout');
    }
  }

  function renderJobProgress(job, attempts) {
    dom.jobProgressContainer.replaceChildren(
      h('div', { className: 'job-banner' }, [
        h('div', { className: 'job-banner-info' }, [
          h('div', { className: 'spinner' }),
          h('div', {}, [
            h('strong', {}, 'Подготовка анализа: '+({queued:'в очереди',running:'обрабатывается',partial:'с ограничениями',succeeded:'завершена',failed:'ошибка'}[job.status] || 'обрабатывается')),
            h('div', { style: 'font-size: 0.8rem; color: var(--text-secondary);' },
              'Проверяем данные и источники. Результат будет сохранён.'
            )
          ])
        ]),
        h('button', {
          className: 'btn btn-secondary btn-sm',
          type: 'button',
          onClick: () => {
            stopActivePolling();
            showGlobalError('Опрос задания остановлен пользователем.', 'cancelled');
          }
        }, 'Отменить опрос')
      ])
    );
  }

  function setAnalysisResult(analysis) {
    if (!state.isFixtureMode && analysis.company_id !== state.currentCompanyId) {
      showGlobalError('Сохранённый анализ относится к другой компании. Выберите соответствующую компанию и повторите открытие.', 'context_mismatch');
      return;
    }
    state.activeAnalysis = analysis;
    dom.savedId.value = analysis.id;
    renderActiveAnalysis();
    loadAnalysisDocuments(analysis);
  }

  function clearAnalysis() {
    state.activeAnalysis = null;
    state.documents = {};
    dom.followupTextarea.value = '';
    dom.assumptionToggle.checked = false;
    dom.assumptionName.value = '';
    dom.assumptionValue.value = '';
    dom.assumptionFields.classList.add('hidden');
    closeEvidenceDrawer();
    dom.drawerBody.replaceChildren();
    dom.analysisContainer.innerHTML = '';
    dom.followupContainer.classList.add('hidden');
    dom.conversationThread.innerHTML = '';
    state.conversation = [];
  }

  function renderActiveAnalysis(resetInput = true) {
    if (!state.activeAnalysis) {
      dom.analysisContainer.innerHTML = '';
      dom.followupContainer.classList.add('hidden');
      return;
    }

    dom.analysisContainer.replaceChildren(
      AlfaUI.renderAnalysisCard(
        state.activeAnalysis,
        openEvidenceDrawer,
        selectRecommendedQuestion,
        state.documents,
        openFeatureDrawer
      )
    );

    // Show follow-up section
    dom.followupContainer.classList.remove('hidden');
    if (resetInput) {
      dom.followupTextarea.value = '';
      dom.followupCharCount.textContent = '0 / 2000';
      dom.followupErrorBox.classList.add('hidden');
    }
  }

  async function loadAnalysisDocuments(analysis) {
    const token = state.activePollingToken;
    const ids = new Set((analysis.bank_context?.theses || []).flatMap(t=>t.evidence_ids));
    const dids = [...new Set((analysis.evidence||[]).filter(e=>ids.has(e.id)).map(e=>e.document_version_id))];
    if(!dids.length) return;
    try {
      const docs = await Promise.all(dids.map(id=>AlfaApi.getSource(id)));
      if(token!==state.activePollingToken || state.activeAnalysis?.id!==analysis.id) return;
      state.documents=Object.fromEntries(docs.map(d=>[d.id,d]));
      renderActiveAnalysis(false);
    } catch (_) { /* Persisted source quotes stay visible; opening a source reports errors. */ }
  }

  async function openFeatureDrawer(id) {
    const token=state.activePollingToken, drawerToken=++state.evidenceRequestToken;
    state.evidenceDrawerOpen=true;
    dom.drawerTitle.textContent='Данные компании и их происхождение';
    dom.drawerBody.replaceChildren(h('p',{},'Загрузка признака...'));
    dom.drawerBackdrop.classList.add('open');
    try {
      const feature=await AlfaApi.getFeature(id);
      if(token!==state.activePollingToken || drawerToken!==state.evidenceRequestToken || !state.evidenceDrawerOpen) return;
      dom.drawerBody.replaceChildren(AlfaUI.renderFeatureDrawer(feature));
    } catch (err) {
      if(token===state.activePollingToken && drawerToken===state.evidenceRequestToken) dom.drawerBody.replaceChildren(h('p',{className:'notice-box warning'},'Данные недоступны в текущем контексте. '+err.message));
    }
  }

  function setScreen(screen) {
    state.screen=screen;
    dom.homeArea.classList.toggle('hidden',!state.isLoggedIn || screen!=='home');
    dom.appArea.classList.toggle('hidden',!state.isLoggedIn || screen!=='analytics');
    dom.appTabs.classList.toggle('hidden',!state.isLoggedIn || screen!=='analytics');
    dom.shellNav.classList.toggle('hidden',!state.isLoggedIn);
    closeEvidenceDrawer();
  }

  function renderHome() {
    const p=state.companies.find(c=>c.company_id===state.currentCompanyId);
    dom.homeSummary.replaceChildren(h('p',{},p ? `Профиль: ${p.industry ? 'ОКВЭД '+p.industry : 'вид деятельности не указан'}. Доступных событий с объяснённой связью: ${state.feed.length}.` : 'Выберите разрешённую компанию.'));
  }

  function selectRecommendedQuestion(questionText) {
    dom.followupTextarea.value = questionText;
    dom.followupCharCount.textContent = `${questionText.length} / 2000`;
    dom.followupTextarea.focus();
    dom.followupContainer.scrollIntoView({ behavior: 'smooth' });
  }

  // Evidence Drawer Controller
  async function openEvidenceDrawer(claim) {
    const drawerToken = ++state.evidenceRequestToken;
    const token = state.activePollingToken;
    state.evidenceDrawerOpen = true;
    dom.drawerTitle.textContent = 'Источники и основания вывода';
    dom.drawerBody.replaceChildren(h('p', {}, 'Загрузка оснований...'));
    dom.drawerBackdrop.classList.add('open');

    // Find all evidence items for this claim
    let evList = [];
    if (state.activeAnalysis && state.activeAnalysis.evidence) {
      const idsSet = new Set(claim.evidence_ids || []);
      evList = state.activeAnalysis.evidence.filter(e => idsSet.has(e.id));
    }

    let documents = {};
    if (!state.isFixtureMode) {
      try { evList = await Promise.all((claim.evidence_ids || []).map(id => AlfaApi.getEvidence(id))); }
      catch (err) { if (token === state.activePollingToken && drawerToken === state.evidenceRequestToken) dom.drawerBody.replaceChildren(h('p',{className:'notice-box warning'},'Источник недоступен в текущем контексте. '+err.message)); return; }
    }
    if (!state.isFixtureMode) {
      const ids=[...new Set(evList.map(e=>e.document_version_id).filter(Boolean))];
      try { documents=Object.fromEntries((await Promise.all(ids.map(id=>AlfaApi.getSource(id)))).map(d=>[d.id,d])); }
      catch (_) { /* A fragment can still be shown with its own verified address and quality. */ }
    }
    if (token !== state.activePollingToken || drawerToken !== state.evidenceRequestToken || !state.evidenceDrawerOpen) return;
    dom.drawerBody.replaceChildren(
      AlfaUI.renderEvidenceDrawerContent(claim, evList, fetchAndDisplayFullDocument, documents)
    );

    dom.drawerBackdrop.classList.add('open');
  }

  function closeEvidenceDrawer() {
    state.evidenceRequestToken++;
    state.evidenceDrawerOpen = false;
    dom.drawerBackdrop.classList.remove('open');
  }

  async function fetchAndDisplayFullDocument(docId, targetCard) {
    const drawerToken = state.evidenceRequestToken;
    const token = state.activePollingToken;
    const existing = targetCard.querySelector('.doc-text-container');
    if (existing) {
      existing.remove();
      return;
    }

    const loadingNode = h('div', { style: 'font-size: 0.8rem; color: var(--text-muted); margin-top: 0.5rem;' }, 'Загрузка текста документа...');
    targetCard.appendChild(loadingNode);

    try {
      let doc;
      if (state.isFixtureMode) {
        doc = AlfaFixtures.DOCUMENT;
      } else {
        doc = await AlfaApi.getSource(docId);
      }
      if (token !== state.activePollingToken || drawerToken !== state.evidenceRequestToken || !state.evidenceDrawerOpen) return;
      loadingNode.remove();

      const docContainer = h('div', { className: 'doc-text-container', style: 'margin-top: 0.5rem;' });
      docContainer.appendChild(h('div', { style: 'font-weight: 700; margin-bottom: 0.3rem;' }, doc.title));
      docContainer.appendChild(h('div', { style: 'font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.5rem;' },
        `Издатель: ${doc.publisher || '—'} | Опубликовано: ${AlfaUI.formatDate(doc.published_at)}`
      ));
      if (doc.url) {
        docContainer.appendChild(h('div', { style: 'margin-bottom: 0.5rem;' }, [
          h('a', { href: doc.url, target: '_blank', rel: 'noopener noreferrer', style: 'color: var(--alfa-red); font-size: 0.8rem;' }, 'Открыть первоисточник ↗')
        ]));
      }
      docContainer.appendChild(h('div', {}, doc.text));
      targetCard.appendChild(docContainer);
    } catch (err) {
      loadingNode.textContent = `Ошибка загрузки документа: ${err.message}`;
      loadingNode.style.color = 'var(--status-error)';
    }
  }

  // User Authentication & Session
  async function handleLogin() {
    clearGlobalError();
    const actor = dom.actorSelect.value;
    dom.btnLogin.disabled = true;
    dom.btnLogin.textContent = 'Вход...';

    try {
      if (state.isFixtureMode) {
        state.context = structuredClone(AlfaFixtures.USER_CONTEXT);
        state.companies = structuredClone(AlfaFixtures.COMPANIES);
      } else {
        const session = await AlfaApi.login(actor);
        state.context = session.context;
        state.companies = await AlfaApi.getCompanies();
      }

      state.isLoggedIn = true;
      state.actor = actor;
      state.currentCompanyId = state.context.company_id;
      state.currentRole = state.context.role || 'unknown';

      updateUserUI();
      await loadFeed();
      await loadNews();
      await loadNewsSources();
    } catch (err) {
      showGlobalError(err.message, err.code);
    } finally {
      dom.btnLogin.disabled = false;
      dom.btnLogin.textContent = 'Войти';
    }
  }

  function handleLogout() {
    stopActivePolling();
    closeEvidenceDrawer();
    state.isLoggedIn = false;
    state.context = null;
    state.companies = [];
    state.activeAnalysis = null;
    clearAnalysis();
    updateUserUI();
  }

  function updateUserUI() {
    if (state.isLoggedIn) {
      dom.roleSource.textContent = state.context.role_source==='user_selected' ? 'Роль выбрана вами; не извлечена из данных физических лиц' : 'Смоделированный контекст роли; можно выбрать свою роль';
      dom.userCapsule.style.display = 'flex';
      dom.authArea.classList.add('hidden');
      dom.appArea.classList.remove('hidden');
      dom.contextRibbon.classList.remove('hidden');
      dom.appTabs.classList.remove('hidden');

      dom.userName.textContent = state.actor.toUpperCase();
      dom.contextVersionBadge.textContent = `v${state.context.version}`;

      // Populate companies
      dom.companySelect.replaceChildren(
        ...state.companies.map(c => {
          const label=c.name===c.company_id ? 'Компания '+c.company_id.replace('U_','')+(c.industry?' · ОКВЭД '+c.industry:'') : c.name;
          const opt = new Option(label, c.company_id);
          opt.selected = (c.company_id === state.currentCompanyId);
          return opt;
        })
      );
      dom.userCompany.textContent = state.currentCompanyId;
      dom.roleSelect.value = state.currentRole;
      dom.temporalModeSelect.value = state.temporalMode;
      dom.asOfInput.value = state.asOf;
      renderHome();
      setScreen(state.screen);
    } else {
      dom.homeArea.classList.add('hidden');
      dom.shellNav.classList.add('hidden');
      dom.userCapsule.style.display = 'none';
      dom.authArea.classList.remove('hidden');
      dom.appArea.classList.add('hidden');
      dom.contextRibbon.classList.add('hidden');
      dom.appTabs.classList.add('hidden');
    }
  }

  // Context Switcher
  async function handleApplyContext() {
    clearGlobalError();
    dom.btnApplyContext.disabled = true;
    dom.btnApplyContext.textContent = 'Сохранение...';

    const newCompanyId = dom.companySelect.value;
    const newRole = dom.roleSelect.value;
    const newTemporalMode = dom.temporalModeSelect.value;
    const newAsOf = dom.asOfInput.value;


    {
      // Any context change invalidates pending requests, including role/date changes.
      // Abort in-flight polling and clear previous results immediately!
      stopActivePolling();
      clearAnalysis();
    }

    try {
      if (state.isFixtureMode) {
        state.context.version++;
        state.context.company_id = newCompanyId;
        state.context.role = newRole;
      } else {
        const patch = {
          expected_version: state.context.version,
          company_id: newCompanyId,
          role: newRole
        };
        state.context = await AlfaApi.patchContext(patch);
      }

      state.currentCompanyId = newCompanyId;
      state.currentRole = newRole;
      state.temporalMode = newTemporalMode;
      state.asOf = newAsOf;

      updateUserUI();

      {
        await loadFeed();
        await loadNews();
      }
    } catch (err) {
      showGlobalError(err.message, err.code);
    } finally {
      dom.btnApplyContext.disabled = false;
      dom.btnApplyContext.textContent = 'Применить выбор';
    }
  }

  // Feed & Analysis Execution
  async function loadFeed() {
    const token = state.activePollingToken;
    try {
      if (state.isFixtureMode) {
        state.feed = AlfaFixtures.FEED;
      } else {
        state.feed = await AlfaApi.getFeed(state.asOf, state.temporalMode);
      }
      if (token !== state.activePollingToken) return;
      dom.feedContainer.replaceChildren(
        AlfaUI.renderFeed(state.feed, handleTriggerAnalysis, openFeatureDrawer)
      );
      renderHome();
    } catch (err) {
      dom.feedContainer.replaceChildren(
        h('div', { className: 'notice-box warning' }, `Ошибка загрузки ленты: ${err.message}`)
      );
    }
  }

  async function handleTriggerAnalysis(event) {
    setScreen('analytics');
    setActiveTab('feed');
    clearGlobalError();
    stopActivePolling();
    clearAnalysis();

    const analysisReq = {
      event_id: event.id,
      context_version: state.context.version,
      temporal_mode: state.temporalMode,
      as_of: state.asOf,
      scenario_id: 'rate_hold'
    };

    const token = ++state.activePollingToken;

    try {
      let job;
      if (state.isFixtureMode) {
        const scenario = AlfaFixtures.SCENARIOS[state.selectedFixtureScenario];
        if (scenario.isError) {
          showGlobalError(scenario.message, scenario.code);
          return;
        }
        job = {
          id: 'job_fixture_' + AlfaApi.generateUUID().slice(0, 8),
          kind: 'analysis',
          status: 'queued',
          poll_url: '/api/v1/jobs/fixture'
        };
      } else {
        const res = await AlfaApi.createAnalysis(analysisReq);
        job = res.job;
      }

      if (token !== state.activePollingToken) return;
      state.activeJob = job;
      await pollJob(job, token);
    } catch (err) {
      if (token === state.activePollingToken) showGlobalError(err.message, err.code);
    }
  }

  // Follow-up Submission
  async function handleFollowupSubmit() {
    clearGlobalError();
    dom.followupErrorBox.classList.add('hidden');
    dom.followupErrorBox.textContent = '';

    const text = dom.followupTextarea.value.trim();
    if (!text) {
      dom.followupErrorBox.textContent = 'Введите текст уточняющего вопроса.';
      dom.followupErrorBox.classList.remove('hidden');
      return;
    }

    if (!state.activeAnalysis) {
      dom.followupErrorBox.textContent = 'Сначала необходимо сформировать анализ.';
      dom.followupErrorBox.classList.remove('hidden');
      return;
    }

    // Assumptions
    const assumptions = [];
    if (dom.assumptionToggle.checked) {
      const aName = dom.assumptionName.value.trim() || 'contract_statement';
      const aVal = dom.assumptionValue.value.trim();
      if (aName && aVal) {
        assumptions.push({
          schema_version: '1.1.0',
          name: aName,
          value: aVal,
          scope: 'conversation',
          source: 'user_declared'
        });
      }
    }

    const followupReq = {
      analysis_id: state.activeAnalysis.id,
      conversation_id: state.activeAnalysis.conversation_id,
      context_version: state.activeAnalysis.context.version, // PINNED CONTEXT VERSION!
      text: text,
      assumptions: assumptions,
      scenario_id: 'fixture_only'
    };

    dom.btnFollowupSubmit.disabled = true;
    dom.btnFollowupSubmit.textContent = 'Отправка вопроса...';

    const token = ++state.activePollingToken;

    try {
      let job;
      if (state.isFixtureMode) {
        job = {
          id: 'job_followup_' + AlfaApi.generateUUID().slice(0, 8),
          kind: 'followup',
          status: 'queued',
          poll_url: '/api/v1/jobs/fixture_followup'
        };
      } else {
        const res = await AlfaApi.followup(followupReq);
        job = res.job;
      }

      if (token !== state.activePollingToken) return;
      // Record in conversation thread
      state.conversation.push({ question: text, assumptions });
      renderConversationThread();

      await pollJob(job, token);
      if (token !== state.activePollingToken) return;
      dom.followupTextarea.value = '';
      dom.followupCharCount.textContent = '0 / 2000';
    } catch (err) {
      if (token !== state.activePollingToken) return;
      // Preserve entered question on error!
      dom.followupErrorBox.textContent = `Уточнение не отправлено. ${err.message}`;
      dom.followupErrorBox.classList.remove('hidden');
    } finally {
      dom.btnFollowupSubmit.disabled = false;
      dom.btnFollowupSubmit.textContent = 'Отправить уточнение';
    }
  }

  function renderConversationThread() {
    dom.conversationThread.replaceChildren(
      ...state.conversation.map(item => {
        const node = h('div', { className: 'thread-item' }, [
          h('div', { className: 'thread-item-q' }, `Вопрос: ${item.question}`)
        ]);
        if (item.assumptions && item.assumptions.length > 0) {
          const assmText = item.assumptions.map(a => `${window.AlfaUI.fieldLabel(a.name)}: ${a.value}`).join(', ');
          node.appendChild(h('div', { style: 'font-size: 0.8rem; color: var(--status-warning); margin-bottom: 0.4rem;' },
            `Заявленное условие: ${assmText}`
          ));
        }
        node.appendChild(h('div', { className: 'thread-item-a', style: 'font-size: 0.85rem; color: var(--text-secondary);' },
          'Запрос отправлен; результат и его ограничения показаны в карточке после завершения задания.'
        ));
        return node;
      })
    );
  }

  // News Tab
  async function loadNews() {
    const token = state.activePollingToken;
    try {
      const source = dom.newsSourceSelect.value;
      if (state.isFixtureMode) {
        state.news = [
          {
            id: 'cbr_article_sample',
            source_version_id: 'cbr_rss_v1',
            title: 'Пресс-релиз: О динамике потребительских цен и кредитования',
            event_at: '2026-10-21T12:00:00Z',
            topics: ['inflation', 'rates'],
            evidence_ids: ['demo_a_evidence']
          }
        ];
      } else {
        state.news = await AlfaApi.getNews(20, source);
      }
      if (token !== state.activePollingToken) return;
      dom.newsContainer.replaceChildren(
        AlfaUI.renderNewsList(state.news, openNewsDocument, handleTriggerAnalysis)
      );
    } catch (err) {
      dom.newsContainer.replaceChildren(
        h('div', { className: 'notice-box warning' }, `Ошибка загрузки новостей: ${err.message}`)
      );
    }
  }

  function openNewsDocument(newsItem) {
    openEvidenceDrawer({
      id: newsItem.id,
      text: newsItem.title,
      evidence_ids: newsItem.evidence_ids || []
    });
  }

  async function loadNewsSources() {
    const token = state.activePollingToken;
    try {
      if (state.isFixtureMode) {
        state.newsSources = [
          {
            id: 'cbr-press',
            url: 'https://www.cbr.ru/rss/RssPress',
            status: 'ok',
            last_success_at: '2026-10-21T14:00:00Z',
            imported: 12,
            error: null
          },
          {
            id: 'rosstat',
            url: 'https://rosstat.gov.ru/rss',
            status: 'ok',
            last_success_at: '2026-10-21T14:05:00Z',
            imported: 5,
            error: null
          }
        ];
      } else {
        state.newsSources = await AlfaApi.getNewsSources();
      }
      if (token !== state.activePollingToken) return;
      dom.statusSourcesContainer.replaceChildren(
        AlfaUI.renderNewsSourcesTable(state.newsSources)
      );
    } catch (err) {
      dom.statusSourcesContainer.replaceChildren(
        h('div', { className: 'notice-box warning' }, `Ошибка загрузки статусов источников: ${err.message}`)
      );
    }
  }

  // UI Fixtures Mode Controller
  function toggleFixtureMode(enable) {
    stopActivePolling(); clearAnalysis();
    state.isFixtureMode = enable;
    dom.btnToggleFixtures.setAttribute('aria-pressed', String(enable));
    if (state.isFixtureMode) {
      state.screen='analytics';
      dom.fixtureBanner.classList.remove('hidden');
      dom.btnToggleFixtures.textContent = 'Выключить UI-fixtures';
      dom.btnToggleFixtures.className = 'btn btn-outline-danger btn-sm';
      {
        state.isLoggedIn = true;
        state.actor = 'alice';
        state.context = structuredClone(AlfaFixtures.USER_CONTEXT);
        state.companies = structuredClone(AlfaFixtures.COMPANIES);
        state.currentCompanyId = 'demo_a';
        state.currentRole = 'ceo';
        updateUserUI();
      }
      loadFeed();
      loadNews();
      loadNewsSources();
      // Load current fixture scenario
      loadFixtureScenario(dom.fixtureScenarioSelect.value);
    } else {
      dom.fixtureBanner.classList.add('hidden');
      dom.btnToggleFixtures.textContent = 'Включить UI-fixtures';
      dom.btnToggleFixtures.className = 'btn btn-secondary btn-sm';
      // Require a real session/context; never reuse a fixture identity for API requests.
      handleLogout();
    }
  }

  function loadFixtureScenario(scenarioKey) {
    state.selectedFixtureScenario = scenarioKey;
    dom.fixtureScenarioSelect.value = scenarioKey;
    const scenario = AlfaFixtures.SCENARIOS[scenarioKey];
    if (!scenario) return;

    clearGlobalError();
    if (scenario.isError) {
      clearAnalysis();
      showGlobalError(scenario.message, scenario.code);
    } else {
      setAnalysisResult(scenario.analysis);
    }
  }

  // Tabs Controller
  function setActiveTab(tabName) {
    state.activeTab = tabName;
    dom.tabBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
      btn.setAttribute('aria-selected', String(btn.dataset.tab === tabName));
    });
    dom.tabFeed.classList.toggle('hidden', tabName !== 'feed');
    dom.tabNews.classList.toggle('hidden', tabName !== 'news');
    dom.tabStatus.classList.toggle('hidden', tabName !== 'status');

    if (tabName === 'news') {
      loadNews();
    } else if (tabName === 'status') {
      loadNewsSources();
    }
  }

  // Event Listeners Binding
  function bindEvents() {
    dom.homeButton.addEventListener('click',()=>setScreen('home'));
    dom.analyticsButton.addEventListener('click',()=>setScreen('analytics'));
    dom.analyticsEntry.addEventListener('click',()=>setScreen('analytics'));
    dom.btnLogin.addEventListener('click', handleLogin);
    dom.btnLogout.addEventListener('click', handleLogout);
    dom.btnApplyContext.addEventListener('click', handleApplyContext);

    dom.btnToggleFixtures.addEventListener('click', () => {
      toggleFixtureMode(!state.isFixtureMode);
    });

    dom.fixtureScenarioSelect.addEventListener('change', (e) => {
      loadFixtureScenario(e.target.value);
    });

    dom.tabBtns.forEach(btn => {
      btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
    });

    dom.drawerCloseBtn.addEventListener('click', closeEvidenceDrawer);
    dom.drawerBackdrop.addEventListener('click', (e) => {
      if (e.target === dom.drawerBackdrop) closeEvidenceDrawer();
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state.evidenceDrawerOpen) {
        closeEvidenceDrawer();
      }
    });

    dom.followupTextarea.addEventListener('input', (e) => {
      const len = e.target.value.length;
      dom.followupCharCount.textContent = `${len} / 2000`;
    });

    dom.btnFollowupSubmit.addEventListener('click', handleFollowupSubmit);

    dom.assumptionToggle.addEventListener('change', (e) => {
      dom.assumptionFields.classList.toggle('hidden', !e.target.checked);
    });

    dom.btnRefreshNews.addEventListener('click', loadNews);
    dom.newsSourceSelect.addEventListener('change', loadNews);

    // Populate fixture scenario options
    dom.fixtureScenarioSelect.replaceChildren(
      ...Object.entries(AlfaFixtures.SCENARIOS).map(([key, item]) => {
        const opt = new Option(item.label, key);
        return opt;
      })
    );

    dom.savedId.value = new URLSearchParams(window.location.search).get('analysis') || '';
    dom.openSaved.addEventListener('click', async () => {
      stopActivePolling(); clearAnalysis(); clearGlobalError();
      const token = state.activePollingToken;
      try {
        const analysis = await AlfaApi.getAnalysis(dom.savedId.value.trim());
        if (token === state.activePollingToken) setAnalysisResult(analysis);
      } catch (err) { if (token === state.activePollingToken) showGlobalError(err.message, err.code); }
    });
    // Fixtures require an explicit user action on the labelled toggle, never a URL hash.
  }

  // Initialize
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initDom();
      bindEvents();
    });
  } else {
    initDom();
    bindEvents();
  }

})(window);
