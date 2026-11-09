(() => {
  'use strict';

  // The result also works as a standalone page. The parent owns iframe sizing.
  if (document.body.hasAttribute('data-auto-result')) {
    if (window.self !== window.top) document.body.classList.add('is-embedded');
    const flowTitle = document.getElementById('behavior-flow-title');
    if (flowTitle) flowTitle.textContent = 'Git 图 · 行为调用';
    const flowKicker = document.querySelector('.flow-kicker');
    if (flowKicker) flowKicker.textContent = '能力域与调用顺序';
    return;
  }

  const form = document.getElementById('auto-run-form');
  if (!form) return;
  const config = JSON.parse(document.getElementById('auto-run-config').textContent);
  const elements = {
    fields: document.getElementById('input-fields'),
    persona: document.getElementById('persona'),
    utterance: document.getElementById('user-utterance'),
    backend: document.getElementById('llm-backend'),
    generate: document.getElementById('generate-button'),
    formError: document.getElementById('form-error'),
    progress: document.getElementById('progress-panel'),
    message: document.getElementById('progress-message'),
    bar: document.getElementById('run-progress'),
    connection: document.getElementById('connection-note'),
    runError: document.getElementById('run-error'),
    runErrorMessage: document.getElementById('run-error-message'),
    retry: document.getElementById('retry-button'),
    results: document.getElementById('result-region'),
    resultLink: document.getElementById('result-link'),
    frame: document.getElementById('result-frame'),
    resultLoading: document.getElementById('result-loading'),
    resultError: document.getElementById('result-error'),
    reloadResult: document.getElementById('reload-result'),
  };
  // Cached templates can adopt the compact layout without restarting active jobs.
  const brand = document.querySelector('.auto-brand');
  if (brand) brand.textContent = '人格导游';
  if (!elements.progress.querySelector('.compact-progress-row')) {
    const row = document.createElement('div');
    row.className = 'compact-progress-row';
    elements.message.className = 'compact-progress-message';
    row.append(elements.bar, elements.message);
    elements.progress.querySelectorAll('.progress-heading-row, #step-list, #progress-footnote').forEach(node => node.remove());
    elements.progress.prepend(row);
    elements.progress.classList.remove('auto-panel');
    elements.progress.classList.add('compact-progress');
    elements.progress.removeAttribute('aria-labelledby');
    elements.progress.setAttribute('aria-label', '生成进度');
  }
  const titles = ['输入人格与情境', '理解情境', '匹配人格主导功能', '匹配基本行动策略', '匹配场景行动策略', '确定交互方式', '生成行为序列', '生成场景故事'];
  const activeStatuses = new Set(['queued', 'running', 'retrying']);
  const resultStatuses = new Set(['completed', 'needs_clarification', 'no_tool_needed']);
  const examples = {
    welcome: '今天早上要去康源大厦见一位重要客户，我第一次去，十点前得赶到。外面挺冷，车里也有点凉，前挡看着有些发白。出门太急没吃早饭，路上最好能顺便买点，但别耽误太久。我现在有点着急。',
    comfort: '今天晚上刚从医院出来，陪妈妈做了检查，结果还得等两天。现在车里就我一个人，要回锦悦小区，明天还得上班。家人在手机上问情况，我不知道怎么回。外面下着小雨，出口还排着队，我心里堵得慌。',
  };
  let jobId = config.initialJobId || null;
  let currentJob = null;
  let restoringInputs = Boolean(jobId);
  let mutating = false;
  let pollTimer = null;
  let polling = false;
  let connectionFailures = 0;
  let displayedResultId = null;
  let frameTimer = null;
  let frameObserver = null;

  function setText(node, value) {
    const text = String(value || '');
    if (node.textContent !== text) node.textContent = text;
  }

  function showError(node, message) {
    setText(node, message);
    node.hidden = !message;
  }

  function controlsLocked() {
    return mutating || restoringInputs || (currentJob && activeStatuses.has(currentJob.status));
  }

  function updateControls() {
    const busy = Boolean(controlsLocked());
    elements.fields.disabled = busy;
    elements.generate.disabled = busy;
    elements.retry.disabled = busy;
    if (mutating) setText(elements.generate, currentJob?.status === 'failed' ? '正在请求重试…' : '正在启动…');
    else if (restoringInputs) setText(elements.generate, '正在恢复任务…');
    else if (busy) setText(elements.generate, '正在生成…');
    else setText(elements.generate, currentJob ? '重新生成' : '一键生成');
    elements.progress.setAttribute('aria-busy', String(busy));
  }

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {...options, signal: controller.signal, cache: 'no-store'});
      let data;
      try { data = await response.json(); } catch (_) {
        const error = new Error(`服务暂时未能返回结果（${response.status}）。`);
        error.status = response.status;
        throw error;
      }
      if (!response.ok || !data.ok || !data.job) {
        const detail = data.error?.message || (typeof data.error === 'string' ? data.error : '') || data.message;
        const error = new Error(detail || `请求未成功（${response.status}），请稍后重试。`);
        error.status = response.status;
        throw error;
      }
      return data.job;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('连接等待时间较长，请稍后再试。');
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function resizeFrame() {
    try {
      const content = elements.frame.contentDocument?.querySelector('.auto-result-wrap');
      if (content) {
        const height = Math.ceil(content.getBoundingClientRect().height) + 3;
        elements.frame.style.height = `${Math.max(height, 450)}px`;
      }
    } catch (_) { /* An unavailable document gets a visible load error below. */ }
  }

  function frameFailed() {
    window.clearTimeout(frameTimer);
    elements.resultLoading.hidden = true;
    elements.resultError.hidden = false;
    elements.frame.hidden = true;
  }

  function loadResult() {
    if (!jobId) return;
    if (frameObserver) frameObserver.disconnect();
    window.clearTimeout(frameTimer);
    const resultPath = `/auto_run/${encodeURIComponent(jobId)}/result`;
    elements.results.hidden = false;
    elements.resultLink.href = resultPath;
    elements.resultError.hidden = true;
    elements.resultLoading.hidden = false;
    elements.frame.hidden = false;
    elements.frame.style.visibility = 'hidden';
    elements.frame.src = resultPath;
    frameTimer = window.setTimeout(frameFailed, 25000);
  }

  elements.frame.addEventListener('load', () => {
    if (!displayedResultId || elements.results.hidden) return;
    try {
      const doc = elements.frame.contentDocument;
      if (!doc?.body?.hasAttribute('data-auto-result')) { frameFailed(); return; }
      window.clearTimeout(frameTimer);
      doc.body.classList.add('is-embedded');
      elements.resultLoading.hidden = true;
      elements.resultError.hidden = true;
      elements.frame.hidden = false;
      elements.frame.style.visibility = 'visible';
      resizeFrame();
      if (typeof ResizeObserver !== 'undefined') {
        frameObserver = new ResizeObserver(resizeFrame);
        frameObserver.observe(doc.querySelector('.auto-result-wrap'));
      }
      doc.addEventListener('toggle', resizeFrame, true);
      doc.addEventListener('click', () => window.requestAnimationFrame(resizeFrame));
    } catch (_) { frameFailed(); }
  });
  elements.frame.addEventListener('error', frameFailed);
  elements.reloadResult.addEventListener('click', loadResult);
  window.addEventListener('resize', resizeFrame);

  function renderJob(job) {
    currentJob = job;
    jobId = job.id;
    elements.progress.hidden = resultStatuses.has(job.status);
    const steps = Array.isArray(job.steps) ? job.steps : [];
    const completed = steps.filter(step => step.status === 'completed' || step.status === 'skipped').length;
    elements.bar.value = completed;
    const stepNumber = Number(job.current_step);
    const title = titles[stepNumber - 1];
    const headings = {
      queued: '等待开始',
      running: title ? `正在${title}…` : '生成中…',
      retrying: '自动重试中…',
      completed: '已完成',
      failed: '生成失败',
      needs_clarification: '待补充信息',
      no_tool_needed: '已完成',
    };
    setText(elements.message, headings[job.status] || '正在读取进度…');
    const failed = job.status === 'failed';
    elements.runError.hidden = !failed;
    setText(elements.runErrorMessage, failed ? (job.error?.message || '当前步骤在多次尝试后仍未完成。你可以从失败步骤继续重试。') : '');
    if (resultStatuses.has(job.status) && displayedResultId !== job.id) {
      displayedResultId = job.id;
      loadResult();
    }
    updateControls();
  }

  function schedulePoll(delay = 2000) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(poll, delay);
  }

  async function poll() {
    if (!jobId || polling || mutating) return;
    polling = true;
    const requestedId = jobId;
    try {
      const job = await request(`/api/auto-run/${encodeURIComponent(requestedId)}`);
      if (requestedId !== jobId) return;
      connectionFailures = 0;
      elements.connection.hidden = true;
      if (restoringInputs) {
        elements.persona.value = job.persona || 'INFP';
        elements.utterance.value = job.user_utterance || '';
        if (job.llm_backend && ['openai', 'carsona'].includes(job.llm_backend)) elements.backend.value = job.llm_backend;
        restoringInputs = false;
      }
      renderJob(job);
      if (activeStatuses.has(job.status)) schedulePoll();
    } catch (error) {
      if (requestedId !== jobId) return;
      if (error.status === 404 || error.status === 410) {
        restoringInputs = false;
        currentJob = null;
        jobId = null;
        setText(elements.message, '未找到这次任务，请重新生成。');
        updateControls();
      } else {
        connectionFailures++;
        elements.connection.hidden = false;
        setText(elements.connection, `暂时无法读取进度，正在自动重新连接。${error.message || ''} 无需重复提交任务。`);
        schedulePoll(Math.min(10000, 2000 * connectionFailures));
      }
    } finally {
      polling = false;
    }
  }

  function setJobLocation(id) {
    // Saving the address never starts another model request on refresh.
    try { window.history.replaceState({autoRunId: id}, '', `/auto_run/${encodeURIComponent(id)}`); } catch (_) { /* Generation can still proceed if history is unavailable. */ }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (controlsLocked() || !form.reportValidity()) return;
    const utterance = elements.utterance.value.trim();
    if (!utterance) { showError(elements.formError, '请先填写本次情境。'); elements.utterance.focus(); return; }
    mutating = true;
    window.clearTimeout(pollTimer);
    showError(elements.formError, '');
    updateControls();
    try {
      const job = await request('/api/auto-run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({persona: elements.persona.value, user_utterance: utterance, llm_backend: elements.backend.value}),
      });
      displayedResultId = null;
      elements.results.hidden = true;
      elements.connection.hidden = true;
      jobId = job.id;
      connectionFailures = 0;
      setJobLocation(job.id);
      renderJob(job);
      if (activeStatuses.has(job.status)) schedulePoll();
    } catch (error) {
      showError(elements.formError, `未能确认任务是否已启动：${error.message || '请检查网络后重试。'}`);
    } finally {
      mutating = false;
      updateControls();
    }
  });

  elements.retry.addEventListener('click', async () => {
    if (controlsLocked() || !jobId || currentJob?.status !== 'failed') return;
    mutating = true;
    updateControls();
    try {
      const job = await request(`/api/auto-run/${encodeURIComponent(jobId)}/retry`, {method: 'POST'});
      elements.connection.hidden = true;
      connectionFailures = 0;
      renderJob(job);
      if (activeStatuses.has(job.status)) schedulePoll();
    } catch (error) {
      setText(elements.runErrorMessage, `重试请求尚未确认：${error.message || '请检查网络。'} 正在重新读取任务状态。`);
      schedulePoll();
    } finally {
      mutating = false;
      updateControls();
    }
  });

  document.querySelectorAll('[data-example]').forEach(button => {
    button.addEventListener('click', () => {
      elements.utterance.value = examples[button.dataset.example] || '';
      elements.utterance.focus();
    });
  });
  document.getElementById('preset')?.addEventListener('change', event => {
    const selected = event.target.selectedOptions[0];
    if (selected?.dataset.utterance) elements.utterance.value = selected.dataset.utterance;
  });
  window.addEventListener('online', () => { if (jobId && connectionFailures && !polling && !mutating) schedulePoll(0); });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    // Returning via the browser's back/forward cache must resume the saved task.
    if (jobId && (restoringInputs || activeStatuses.has(currentJob?.status) || connectionFailures)) schedulePoll(0);
    if (displayedResultId && !elements.results.hidden) loadResult();
  });
  window.addEventListener('pagehide', () => {
    window.clearTimeout(pollTimer);
    window.clearTimeout(frameTimer);
    if (frameObserver) frameObserver.disconnect();
  });
  if (jobId) {
    elements.progress.hidden = false;
    setText(elements.message, '正在恢复进度…');
    updateControls();
    poll();
  }
})();
