(() => {
  'use strict';

  const form = document.getElementById('auto-compare-form');
  if (!form) return;
  // Existing pages may retain a cached template while this script is updated.
  form.querySelectorAll('.compare-personas, .auto-examples, .auto-settings, .auto-help').forEach(node => node.remove());
  const brand = document.querySelector('.auto-brand');
  if (brand) brand.textContent = '人格导游';
  if (!document.getElementById('compare-progress-summary')) {
    const task = document.getElementById('compare-task');
    const summary = document.createElement('section');
    summary.id = 'compare-progress-summary';
    summary.className = 'compare-progress-summary';
    summary.setAttribute('aria-label', '双人格生成进度');
    const line = document.createElement('div');
    line.className = 'compare-progress-line';
    const bar = document.createElement('progress');
    bar.id = 'compare-progress';
    bar.max = 16;
    bar.value = 0;
    bar.setAttribute('aria-label', 'ESTJ 与 INFP 共十六步完成进度');
    const message = document.createElement('p');
    message.id = 'compare-progress-message';
    message.setAttribute('role', 'status');
    message.setAttribute('aria-live', 'polite');
    message.setAttribute('aria-atomic', 'true');
    message.textContent = '正在启动…';
    line.append(bar, message);
    summary.append(line);
    const connection = document.getElementById('compare-connection');
    if (connection) summary.append(connection);
    task.querySelectorAll('[data-progress-persona]').forEach(card => {
      const persona = card.dataset.progressPersona;
      const error = card.querySelector('[data-error-box]');
      if (error) {
        error.dataset.errorPersona = persona;
        error.setAttribute('aria-label', `${persona} 生成错误`);
        error.querySelector('[data-error]')?.setAttribute('role', 'alert');
        summary.append(error);
      }
      const record = card.querySelector('[data-record]');
      const story = [...task.querySelectorAll('[data-story-persona]')].find(item => item.dataset.storyPersona === persona);
      if (record && story) {
        record.textContent = '完整记录 ↗';
        story.append(record);
      }
    });
    task.querySelector('.compare-task-heading')?.remove();
    task.querySelector('.compare-progress-grid')?.remove();
    task.removeAttribute('aria-labelledby');
    task.setAttribute('aria-label', '双人格对比结果');
    task.prepend(summary);
  }
  const config = JSON.parse(document.getElementById('auto-compare-config').textContent);
  const personas = ['ESTJ', 'INFP'];
  const stepTitles = ['输入人格与情境', '理解情境', '匹配人格主导功能', '匹配基本行动策略', '匹配场景行动策略', '确定交互方式', '生成行为序列', '生成场景故事'];
  const activeStatuses = new Set(['queued', 'running', 'retrying']);
  const resultStatuses = new Set(['completed', 'needs_clarification', 'no_tool_needed']);
  const elements = {
    fields: document.getElementById('compare-input-fields'),
    utterance: document.getElementById('compare-utterance'),
    generate: document.getElementById('compare-generate'),
    error: document.getElementById('compare-form-error'),
    task: document.getElementById('compare-task'),
    progressSummary: document.getElementById('compare-progress-summary'),
    progress: document.getElementById('compare-progress'),
    message: document.getElementById('compare-progress-message'),
    connection: document.getElementById('compare-connection'),
  };
  let backend = ['openai', 'carsona'].includes(config.defaultBackend) ? config.defaultBackend : 'openai';
  let jobId = config.initialJobId || null;
  let currentJob = null;
  let restoring = Boolean(jobId);
  let submitting = false;
  let polling = false;
  let pollTimer = null;
  let mutationVersion = 0;
  let connectionFailures = 0;
  let pageHidden = false;
  const pendingRetries = new Set();

  function setText(node, value) {
    const next = String(value ?? '');
    if (node.textContent !== next) node.textContent = next;
  }

  function textNode(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = String(text ?? '');
    if (className) node.className = className;
    return node;
  }

  const columns = Object.fromEntries(personas.map(persona => {
    const errorBox = document.querySelector(`[data-error-persona="${persona}"]`);
    const story = document.querySelector(`[data-story-persona="${persona}"]`);
    const graph = document.querySelector(`[data-graph-persona="${persona}"]`);
    return [persona, {
      persona, errorBox,
      error: errorBox.querySelector('[data-error]'),
      retry: errorBox.querySelector('[data-retry]'),
      record: story.querySelector('[data-record]'),
      storyStatus: story.querySelector('[data-story-status]'),
      storyPlaceholder: story.querySelector('[data-story-placeholder]'),
      storyContent: story.querySelector('[data-story-content]'),
      graphPlaceholder: graph.querySelector('[data-graph-placeholder]'),
      graphLoading: graph.querySelector('[data-graph-loading]'),
      graphError: graph.querySelector('[data-graph-error]'),
      graphReload: graph.querySelector('[data-graph-reload]'),
      graphLink: graph.querySelector('[data-graph-link]'),
      frame: graph.querySelector('[data-graph-frame]'),
      branchId: null, storySignature: null, graphId: null,
      frameTimer: null, observer: null, frameCleanup: null,
    }];
  }));

  function getBranch(persona) {
    return currentJob?.branches?.find(branch => branch.persona === persona);
  }

  function hasActiveBranches(job = currentJob) {
    return (job?.branches || []).some(branch => activeStatuses.has(branch.status)) || activeStatuses.has(job?.status);
  }

  function controlsLocked() {
    return submitting || restoring || pendingRetries.size > 0 || hasActiveBranches();
  }

  function updateControls() {
    const locked = Boolean(controlsLocked());
    elements.fields.disabled = locked;
    elements.generate.disabled = locked;
    setText(elements.generate, submitting ? '正在启动两条流程…' : restoring ? '正在恢复任务…' : locked ? '正在生成对比…' : currentJob ? '重新生成两个人格' : '同时生成两个人格');
    for (const persona of personas) {
      const column = columns[persona];
      column.retry.disabled = submitting || restoring || pendingRetries.has(persona) || getBranch(persona)?.status !== 'failed';
      setText(column.retry, pendingRetries.has(persona) ? '正在请求重试…' : `重试 ${persona} 失败步骤`);
    }
    elements.progressSummary.setAttribute('aria-busy', String(locked));
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
        const error = new Error(data.error?.message || (typeof data.error === 'string' ? data.error : '') || data.message || `请求未成功（${response.status}）。`);
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

  function disconnectFrame(column) {
    window.clearTimeout(column.frameTimer);
    column.observer?.disconnect();
    column.observer = null;
    column.frameCleanup?.();
    column.frameCleanup = null;
  }

  function resizeFrame(column) {
    try {
      const wrap = column.frame.contentDocument?.querySelector('.auto-result-wrap');
      if (wrap) column.frame.style.height = `${Math.max(280, Math.ceil(wrap.getBoundingClientRect().height) + 4)}px`;
    } catch (_) { /* A failed frame is handled by its load/error state. */ }
  }

  function frameFailed(column) {
    disconnectFrame(column);
    column.graphLoading.hidden = true;
    column.graphError.hidden = false;
    column.frame.hidden = true;
  }

  function loadGraph(column) {
    if (!column.graphId) return;
    disconnectFrame(column);
    const path = `/auto_run/${encodeURIComponent(column.graphId)}/graph`;
    column.graphPlaceholder.hidden = true;
    column.graphLoading.hidden = false;
    column.graphError.hidden = true;
    column.graphLink.href = path;
    column.graphLink.hidden = false;
    column.frame.hidden = false;
    column.frame.style.visibility = 'hidden';
    column.frame.src = path;
    column.frameTimer = window.setTimeout(() => frameFailed(column), 25000);
  }

  function attachFrame(column) {
    if (!column.graphId || column.frame.hidden) return;
    try {
      const doc = column.frame.contentDocument;
      const wrap = doc?.querySelector('.auto-result-wrap');
      if (!doc?.body?.hasAttribute('data-auto-graph') || !wrap) { frameFailed(column); return; }
      disconnectFrame(column);
      doc.body.classList.add('is-embedded');
      column.graphLoading.hidden = true;
      column.graphError.hidden = true;
      column.frame.hidden = false;
      column.frame.style.visibility = 'visible';
      resizeFrame(column);
      const onChange = () => window.requestAnimationFrame(() => resizeFrame(column));
      if (typeof ResizeObserver !== 'undefined') {
        column.observer = new ResizeObserver(onChange);
        column.observer.observe(wrap);
      }
      doc.addEventListener('toggle', onChange, true);
      doc.addEventListener('click', onChange);
      column.frameCleanup = () => {
        doc.removeEventListener('toggle', onChange, true);
        doc.removeEventListener('click', onChange);
      };
    } catch (_) { frameFailed(column); }
  }

  function resetColumn(column, branchId) {
    disconnectFrame(column);
    column.branchId = branchId;
    column.graphId = null;
    column.storySignature = null;
    column.storyContent.replaceChildren();
    column.storyContent.hidden = true;
    column.storyPlaceholder.hidden = false;
    column.graphPlaceholder.hidden = false;
    column.graphLoading.hidden = true;
    column.graphError.hidden = true;
    column.graphLink.hidden = true;
    column.frame.hidden = true;
    column.frame.removeAttribute('src');
    column.frame.style.height = '';
  }

  function branchPlaceholder(branch) {
    if (branch?.status === 'failed') return '本侧生成暂未完成。已保存进度，可点击上方按钮重试失败步骤。';
    if (branch?.status === 'needs_clarification') return '需要补充情境信息，详见本侧说明。';
    if (branch?.status === 'no_tool_needed') return '本次情境无需调用车载能力。';
    if (branch?.status === 'retrying') return '正在自动重试失败阶段，已完成的步骤会保留。';
    return '正在完成八步流程，生成后会自动显示，无需逐步点击。';
  }

  function renderStory(column, branch) {
    const result = branch?.result;
    const story = result?.story_display;
    const paragraphs = Array.isArray(story?.paragraphs) ? story.paragraphs : [];
    const hasStory = paragraphs.some(paragraph => Array.isArray(paragraph.segments) && paragraph.segments.length);
    const isTerminal = resultStatuses.has(branch?.status);
    const labels = {completed: '已生成', failed: '等待重试', retrying: '自动重试中', needs_clarification: '需要补充信息', no_tool_needed: '无需调用能力'};
    setText(column.storyStatus, labels[branch?.status] || '生成中');
    if (!result || (!hasStory && !isTerminal)) {
      column.storyPlaceholder.hidden = false;
      setText(column.storyPlaceholder, branchPlaceholder(branch));
      return;
    }
    const signature = JSON.stringify([branch.status, result]);
    if (column.storySignature === signature) return;
    column.storySignature = signature;
    const content = document.createDocumentFragment();
    const assumptions = Array.isArray(result.scenario_assumptions) ? result.scenario_assumptions : [];
    if (assumptions.length) content.append(textNode('p', `本次默认设定：${assumptions.join('；')}`, 'scenario-assumptions'));
    if (branch.status === 'needs_clarification') {
      const notice = textNode('div', '还需要补充以下信息：', 'auto-notice');
      const questions = Array.isArray(result.clarification_questions) ? result.clarification_questions : [];
      if (questions.length) {
        const list = document.createElement('ul');
        questions.forEach(question => list.append(textNode('li', question)));
        notice.append(list);
      } else notice.append(textNode('p', branch.message || '请补充情境中的关键条件。'));
      content.append(notice);
    }
    for (const warning of (Array.isArray(result.quality_warnings) ? result.quality_warnings : [])) {
      content.append(textNode('p', `内容待核对：${warning}`, 'auto-notice'));
    }
    if (hasStory) {
      const article = document.createElement('div');
      article.className = `auto-story persona-${column.persona}`;
      for (const paragraph of paragraphs) {
        const node = document.createElement('p');
        for (const segment of (Array.isArray(paragraph.segments) ? paragraph.segments : [])) {
          node.append(textNode(segment.change_id ? 'strong' : 'span', segment.text));
        }
        article.append(node);
      }
      if (Array.isArray(story.core_changes) && story.core_changes.length) {
        const changes = document.createElement('div');
        changes.className = 'core-changes';
        changes.append(textNode('span', '人格带来的改变', 'muted'));
        const tags = document.createElement('div');
        tags.className = 'change-tags';
        story.core_changes.forEach(change => tags.append(textNode('span', change.label)));
        changes.append(tags);
        article.append(changes);
      }
      content.append(article);
    } else {
      content.append(textNode('p', branch.status === 'completed' ? '本次场景故事暂不可用，可通过完整记录查看生成详情。' : branch.message || branchPlaceholder(branch), 'compare-placeholder'));
    }
    column.storyContent.replaceChildren(content);
    column.storyContent.hidden = false;
    column.storyPlaceholder.hidden = true;
  }

  function renderBranch(persona, branch) {
    const column = columns[persona];
    if (column.branchId !== (branch?.id || null)) resetColumn(column, branch?.id || null);
    column.errorBox.hidden = branch?.status !== 'failed';
    if (!pendingRetries.has(persona)) setText(column.error, branch?.error?.message || '多次尝试后仍未完成，可从失败步骤继续重试。');
    column.record.hidden = !branch?.id;
    if (branch?.id) column.record.href = `/auto_run/${encodeURIComponent(branch.id)}`;
    renderStory(column, branch);
    const actions = branch?.result?.validated_actions;
    if (resultStatuses.has(branch?.status) && Array.isArray(actions) && actions.length) {
      if (column.graphId !== branch.id) {
        column.graphId = branch.id;
        loadGraph(column);
      }
    } else if (!column.graphId) {
      setText(column.graphPlaceholder, resultStatuses.has(branch?.status) ? '本次没有可绘制的动作。' : branchPlaceholder(branch));
    }
  }

  function branchProgressLabel(persona, branch) {
    const labels = {queued: '等待中', running: '生成中', retrying: '重试中', completed: '已完成', failed: '待重试', needs_clarification: '需补充信息', no_tool_needed: '已完成'};
    const stepNumber = Number(branch?.current_step);
    const stepTitle = Number.isInteger(stepNumber) ? stepTitles[stepNumber - 1] : null;
    if (stepTitle && (activeStatuses.has(branch?.status) || branch?.status === 'failed')) {
      const state = branch.status === 'running' ? '' : `（${labels[branch.status]}）`;
      return `${persona} 第 ${stepNumber}/8 步：${stepTitle}${state}`;
    }
    return `${persona} ${labels[branch?.status] || '等待中'}`;
  }

  function renderJob(job) {
    currentJob = job;
    jobId = job.id;
    elements.task.hidden = false;
    const branches = personas.map(persona => (job.branches || []).find(branch => branch.persona === persona));
    elements.progress.value = branches.reduce((total, branch) => total + (Array.isArray(branch?.steps) ? branch.steps : []).filter(step => ['completed', 'skipped'].includes(step.status)).length, 0);
    elements.progressSummary.hidden = branches.every(branch => resultStatuses.has(branch?.status));
    setText(elements.message, branches.map((branch, index) => branchProgressLabel(personas[index], branch)).join(' · '));
    personas.forEach(persona => renderBranch(persona, (job.branches || []).find(branch => branch.persona === persona)));
    updateControls();
  }

  function schedulePoll(delay = 2000) {
    window.clearTimeout(pollTimer);
    if (!pageHidden) pollTimer = window.setTimeout(poll, delay);
  }

  async function poll() {
    if (!jobId || pageHidden) return;
    if (polling || submitting || pendingRetries.size) { schedulePoll(500); return; }
    polling = true;
    const requestedId = jobId;
    const version = mutationVersion;
    try {
      const job = await request(`/api/auto-compare/${encodeURIComponent(requestedId)}`);
      if (requestedId !== jobId || version !== mutationVersion) return;
      connectionFailures = 0;
      elements.connection.hidden = true;
      if (restoring) {
        elements.utterance.value = job.user_utterance || '';
        if (['openai', 'carsona'].includes(job.llm_backend)) backend = job.llm_backend;
        restoring = false;
      }
      renderJob(job);
      if (hasActiveBranches(job)) schedulePoll();
    } catch (error) {
      if (requestedId !== jobId || version !== mutationVersion) return;
      if (error.status === 404 || error.status === 410) {
        restoring = false;
        currentJob = null;
        jobId = null;
        elements.progressSummary.hidden = false;
        setText(elements.message, '未找到任务');
        setText(elements.connection, '任务可能已移除。可重新填写情境并生成，本页不会自动创建新任务。');
        elements.connection.hidden = false;
        updateControls();
      } else {
        connectionFailures++;
        setText(elements.connection, `暂时无法读取进度，正在自动重新连接。${error.message || ''} 无需重复提交任务。`);
        elements.connection.hidden = false;
        schedulePoll(Math.min(10000, 2000 * connectionFailures));
      }
    } finally {
      polling = false;
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (controlsLocked() || !form.reportValidity()) return;
    const utterance = elements.utterance.value.trim();
    if (!utterance) {
      setText(elements.error, '请先填写本次情境。');
      elements.error.hidden = false;
      elements.utterance.focus();
      return;
    }
    submitting = true;
    mutationVersion++;
    window.clearTimeout(pollTimer);
    elements.error.hidden = true;
    updateControls();
    try {
      const job = await request('/api/auto-compare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({user_utterance: utterance, llm_backend: backend})});
      connectionFailures = 0;
      elements.connection.hidden = true;
      try { window.history.replaceState({autoCompareId: job.id}, '', `/auto_run/compare/${encodeURIComponent(job.id)}`); } catch (_) { /* History is optional; generation can continue. */ }
      renderJob(job);
      if (hasActiveBranches(job)) schedulePoll();
    } catch (error) {
      setText(elements.error, `未能确认任务是否已启动：${error.message || '请检查网络后重试。'}`);
      elements.error.hidden = false;
    } finally {
      submitting = false;
      updateControls();
    }
  });

  for (const persona of personas) {
    const column = columns[persona];
    column.frame.addEventListener('load', () => attachFrame(column));
    column.frame.addEventListener('error', () => { if (column.graphId) frameFailed(column); });
    column.graphReload.addEventListener('click', () => loadGraph(column));
    column.retry.addEventListener('click', async () => {
      if (!jobId || submitting || restoring || pendingRetries.has(persona) || getBranch(persona)?.status !== 'failed') return;
      pendingRetries.add(persona);
      const requestedId = jobId;
      const version = ++mutationVersion;
      updateControls();
      try {
        const job = await request(`/api/auto-compare/${encodeURIComponent(requestedId)}/retry`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({persona})});
        if (requestedId === jobId && version === mutationVersion) renderJob(job);
        connectionFailures = 0;
        elements.connection.hidden = true;
      } catch (error) {
        setText(column.error, `重试请求尚未确认：${error.message || '请检查网络。'} 正在重新读取任务状态。`);
      } finally {
        pendingRetries.delete(persona);
        updateControls();
        schedulePoll(0);
      }
    });
  }

  window.addEventListener('resize', () => personas.forEach(persona => resizeFrame(columns[persona])));
  window.addEventListener('online', () => { if (jobId && connectionFailures) schedulePoll(0); });
  window.addEventListener('pageshow', event => {
    pageHidden = false;
    if (!event.persisted) return;
    if (jobId && (restoring || hasActiveBranches() || connectionFailures)) schedulePoll(0);
    personas.forEach(persona => {
      const column = columns[persona];
      if (column.graphId) {
        if (column.graphLoading.hidden && !column.frame.hidden) attachFrame(column);
        else if (!column.graphLoading.hidden) loadGraph(column);
      }
    });
  });
  window.addEventListener('pagehide', () => {
    pageHidden = true;
    window.clearTimeout(pollTimer);
    personas.forEach(persona => disconnectFrame(columns[persona]));
  });
  if (jobId) {
    elements.task.hidden = false;
    setText(elements.message, '正在恢复进度…');
    updateControls();
    poll();
  }
})();
