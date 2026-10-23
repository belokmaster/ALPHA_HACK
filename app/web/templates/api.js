/**
 * Client API Module for Alfa-Bank News Analytics Prototype
 * Strictly conforms to contracts/v1.py and openapi.json.
 */
(function(window) {
  'use strict';

  let currentCsrfToken = null;
  let lastFailedAnalysisKey = null;
  let lastFailedFollowupKey = null;
  const pendingPosts = new Map();

  function setCsrfToken(token) {
    currentCsrfToken = token;
  }

  function getCsrfToken() {
    return currentCsrfToken;
  }

  function generateUUID() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  async function apiCall(endpoint, options = {}) {
    const method = options.method || 'GET';
    const body = options.body;
    const isIdempotentPost = (method === 'POST' || method === 'PATCH');

    const headers = {
      'Accept': 'application/json'
    };

    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }

    if (currentCsrfToken) {
      headers['X-CSRF-Token'] = currentCsrfToken;
    }

    if (isIdempotentPost) {
      headers['Idempotency-Key'] = options.idempotencyKey || generateUUID();
    }

    const cleanEndpoint = endpoint.startsWith('/') ? endpoint.slice(1) : endpoint;
    const url = '/api/v1/' + cleanEndpoint;

    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: 'same-origin'
      });
    } catch (networkErr) {
      const err = new Error('Ошибка сети при обращении к серверу: ' + networkErr.message);
      err.isNetwork = true;
      err.retryKey = options.idempotencyKey;
      throw err;
    }

    let data = null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try {
        data = await response.json();
      } catch (parseErr) {
        data = null;
      }
    }

    if (!response.ok) {
      const errorEnvelope = data && data.error ? data.error : null;
      const message = errorEnvelope && errorEnvelope.message ? errorEnvelope.message : `HTTP ${response.status}: ${response.statusText}`;
      const code = errorEnvelope ? errorEnvelope.code : 'unknown_error';
      const readable = {
        context_stale: 'Контекст компании изменился. Обновите страницу и повторите действие в нужной компании.',
        not_found: 'Материал недоступен в текущем контексте или право доступа отозвано.',
        forbidden: 'Для этого действия недостаточно прав.',
        unauthenticated: 'Войдите в сервис заново.',
        validation: 'Проверьте заполнение полей.',
        backend_unavailable: 'Сервис временно недоступен. Сохранённые результаты можно открыть позже.',
        internal: 'Не удалось выполнить действие. Обратитесь к команде с кодом диагностики.',
        budget_exceeded: 'Достигнут лимит генерации. Автоматического повтора не будет.'
      };
      const apiErr = new Error(readable[code] || 'Действие остановлено сервером. Подробности доступны команде поддержки.');
      apiErr.technicalMessage = message;
      apiErr.status = response.status;
      apiErr.code = code;
      apiErr.details = errorEnvelope ? errorEnvelope.details : null;
      apiErr.requestId = errorEnvelope ? errorEnvelope.request_id : response.headers.get('X-Request-ID');
      apiErr.retryable = errorEnvelope ? errorEnvelope.retryable : false;
      apiErr.retryKey = options.idempotencyKey;
      throw apiErr;
    }

    return data;
  }

  window.AlfaApi = {
    setCsrfToken,
    getCsrfToken,
    generateUUID,

    async login(actor) {
      const res = await apiCall('demo/sessions', {
        method: 'POST',
        body: { actor }
      });
      if (res && res.csrf_token) {
        setCsrfToken(res.csrf_token);
      }
      return res;
    },

    async getContext() {
      return await apiCall('me/context');
    },

    async patchContext(patch) {
      return await apiCall('me/context', {
        method: 'PATCH',
        body: patch
      });
    },

    async getCompanies() {
      return await apiCall('companies');
    },

    async getCompanyProfile(companyId) {
      return await apiCall(`companies/${encodeURIComponent(companyId)}/profile`);
    },

    async getFeed(asOf=null, temporalMode='snapshot_demo') {
      return await apiCall('feed' + (asOf ? '?as_of=' + encodeURIComponent(asOf) + '&temporal_mode=' + encodeURIComponent(temporalMode) : ''));
    },

    async getNews(limit = 20, source = null) {
      let query = `news?limit=${encodeURIComponent(limit)}`;
      if (source && source !== 'all') {
        query += `&source=${encodeURIComponent(source)}`;
      }
      return await apiCall(query);
    },

    async getNewsSources() {
      return await apiCall('news/sources');
    },

    async createAnalysis(req, retryKey = null) {
      const fingerprint = 'analysis:' + JSON.stringify(req);
      const key = retryKey || pendingPosts.get(fingerprint) || generateUUID();
      pendingPosts.set(fingerprint, key);
      try {
        const job = await apiCall('analyses', {
          method: 'POST',
          body: req,
          idempotencyKey: key
        });
        pendingPosts.delete(fingerprint);
        lastFailedAnalysisKey = null;
        return { job, idempotencyKey: key };
      } catch (err) {
        if (!err.isNetwork) pendingPosts.delete(fingerprint);
        lastFailedAnalysisKey = key;
        err.idempotencyKey = key;
        throw err;
      }
    },

    getLastFailedAnalysisKey() {
      return lastFailedAnalysisKey;
    },

    async getJob(jobId) {
      return await apiCall(`jobs/${encodeURIComponent(jobId)}`);
    },

    async getAnalysis(analysisId) {
      return await apiCall(`analyses/${encodeURIComponent(analysisId)}`);
    },

    async getFeature(featureId) {
      return await apiCall(`features/${encodeURIComponent(featureId)}`);
    },

    async getEvidence(evidenceId) {
      return await apiCall(`evidence/${encodeURIComponent(evidenceId)}`);
    },

    async getSource(documentId) {
      return await apiCall(`sources/${encodeURIComponent(documentId)}`);
    },

    async followup(req, retryKey = null) {
      const fingerprint = 'followup:' + JSON.stringify(req);
      const key = retryKey || pendingPosts.get(fingerprint) || generateUUID();
      pendingPosts.set(fingerprint, key);
      try {
        const job = await apiCall('followups', {
          method: 'POST',
          body: req,
          idempotencyKey: key
        });
        pendingPosts.delete(fingerprint);
        lastFailedFollowupKey = null;
        return { job, idempotencyKey: key };
      } catch (err) {
        if (!err.isNetwork) pendingPosts.delete(fingerprint);
        lastFailedFollowupKey = key;
        err.idempotencyKey = key;
        throw err;
      }
    },

    getLastFailedFollowupKey() {
      return lastFailedFollowupKey;
    }
  };
})(window);
