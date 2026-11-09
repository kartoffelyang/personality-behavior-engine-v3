(() => {
  'use strict';
  const root = document.getElementById('behavior-flow');
  const dataElement = document.getElementById('behavior-flow-data');
  if (!root || !dataElement) return;
  const data = JSON.parse(dataElement.textContent);
  const persona = data.persona;
  root.dataset.persona = persona;
  const domains = [
    ['climate', '空调'], ['seat', '座椅'], ['navigation', '导航'],
    ['ecosystem', '生态服务'], ['notification_mode', '通知'],
    ['entertainment', '影音娱乐'], ['ambient_light', '氛围灯'],
  ];
  const names = new Map(domains);
  const modes = {auto_generate: '自动生成', auto_execute: '自动执行', conditional_auto: '条件执行',
    confirm_execute: '确认后执行', choose_execute: '选择后执行', suggest_only: '仅建议'};
  const burdens = {low: '低', medium: '中', high: '高'};
  const actions = (data.actions || []).map((a, index) => ({...a, index}))
    .filter(a => Number.isInteger(a.wave) && a.wave > 0 && typeof a.capability === 'string')
    .sort((a, b) => a.wave - b.wave || (a.order || a.index + 1) - (b.order || b.index + 1));
  actions.forEach(a => {
    if (!names.has(a.capability)) {
      names.set(a.capability, a.display_name || a.capability);
      domains.push([a.capability, names.get(a.capability)]);
    }
  });
  const waves = [...new Set(actions.map(a => a.wave))].sort((a, b) => a - b);
  const waveIndex = new Map(waves.map((wave, index) => [wave, index]));
  const active = new Set(actions.map(a => a.capability));
  const canvas = root.querySelector('[data-flow-canvas]');
  const viewport = root.querySelector('.flow-viewport');
  const detail = root.querySelector('[data-flow-detail]');
  let scope = 'active';
  let selected = null;

  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function influenced(action) {
    const text = String(action.persona_influence || '').trim();
    const normalized = text.replace(/[\s。；，.!！]/g, '');
    return Boolean(persona && persona !== 'NO_MBTI' && text)
      && !/^(?:(?:人格)?(?:未|没有|无)(?:明显|直接|额外|实质|显著)?(?:人格)?(?:影响|改变|差异)(?:此动作|该动作|本动作)?|无|不适用|None|none)$/.test(normalized)
      && !/不使用MBTI|不确定|无法判断|待核对|尚不明确/.test(text);
  }
  function selectAction(action) {
    selected = action.index;
    root.querySelectorAll('.flow-node').forEach(button => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.actionIndex) === selected));
    });
    detail.replaceChildren();
    const heading = element('div', 'flow-detail-heading');
    heading.append(element('span', 'flow-detail-number', String(action.order || action.index + 1).padStart(2, '0')),
      element('span', 'flow-detail-title', action.name || '未命名动作'));
    const meta = [names.get(action.capability), `波次 ${action.wave}`, action.priority,
      action.burden ? `负担${burdens[action.burden] || action.burden}` : '',
      modes[action.execution_mode] || action.execution_mode].filter(Boolean).join(' · ');
    detail.append(heading, element('div', 'flow-detail-meta', meta),
      element('p', 'flow-detail-description', action.details || '暂无执行内容'));
    detail.append(element('div', influenced(action) ? 'flow-detail-influence' : '',
      `人格影响：${action.persona_influence || '未记录'}`));
    if (Array.isArray(action.preconditions) && action.preconditions.length) {
      const conditions = element('details');
      conditions.append(element('summary', '', `前置条件 · ${action.preconditions.length} 项`));
      const list = element('ul');
      action.preconditions.forEach(value => list.append(element('li', '', String(value))));
      conditions.append(list);
      detail.append(conditions);
    }
  }
  function svgPath(svg, d, className) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    path.setAttribute('class', className);
    path.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.append(path);
  }
  function updateScrollHint() {
    root.querySelector('[data-flow-scroll-hint]').textContent = viewport.scrollWidth > viewport.clientWidth + 2
      ? '← 横向滚动查看全部波次 →' : '';
  }
  function render() {
    canvas.replaceChildren();
    root.querySelector('[data-flow-summary]').textContent = `${actions.length} 个动作  /  ${waves.length} 个波次  /  ${active.size} 个能力域`;
    if (!actions.length) {
      canvas.append(element('div', 'flow-empty', data.actions?.length ? '当前动作缺少波次信息，请查看上方行为序列。' : '本次没有工具调用。'));
      return;
    }
    const labelWidth = parseFloat(getComputedStyle(root).getPropertyValue('--flow-label-width'));
    const columnWidth = Math.max(110, (viewport.clientWidth - labelWidth) / waves.length);
    canvas.style.width = `${labelWidth + columnWidth * waves.length}px`;
    canvas.style.setProperty('--flow-waves', waves.length);
    canvas.append(element('div', 'flow-corner', '能力域 / 波次'));
    const axis = element('div', 'flow-axis');
    waves.forEach(wave => {
      const label = element('div', 'flow-wave', '波次');
      label.append(element('b', '', String(wave).padStart(2, '0')));
      axis.append(label);
    });
    canvas.append(axis);
    domains.filter(([id]) => scope === 'all' || active.has(id)).forEach(([id, name]) => {
      const group = actions.filter(a => a.capability === id);
      const label = element('div', `flow-domain${group.length ? '' : ' idle'}`);
      label.append(element('span', 'flow-domain-name', name), element('span', 'flow-domain-count', group.length ? `${group.length} 个动作` : '未调用'));
      const track = element('div', 'flow-track');
      const counts = new Map();
      const positioned = group.map(action => {
        const slot = counts.get(action.wave) || 0;
        counts.set(action.wave, slot + 1);
        return {action, x: (waveIndex.get(action.wave) + .5) * columnWidth, y: slot * 104 + 26};
      });
      const height = group.length ? Math.max(...counts.values()) * 104 + 10 : 62;
      track.style.height = `${height}px`;
      const grid = element('div', 'flow-track-grid');
      waves.forEach(() => grid.append(element('span')));
      track.append(grid);
      if (positioned.length) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${columnWidth * waves.length} ${height}`);
        svg.setAttribute('aria-hidden', 'true');
        svgPath(svg, `M 0 26 H ${columnWidth * waves.length}`, 'flow-rail-guide');
        positioned.slice(1).forEach((point, index) => {
          const prev = positioned[index];
          const mid = (prev.x + point.x) / 2;
          svgPath(svg, `M ${prev.x} ${prev.y} C ${mid} ${prev.y}, ${mid} ${point.y}, ${point.x} ${point.y}`, 'flow-rail');
        });
        track.append(svg);
        positioned.forEach(({action, x, y}) => {
          const button = element('button', `flow-node${influenced(action) ? ' influenced' : ''}`);
          button.type = 'button';
          button.style.left = `${x}px`;
          button.style.top = `${y - 14}px`;
          button.dataset.actionIndex = action.index;
          button.dataset.wave = action.wave;
          button.dataset.capability = action.capability;
          button.setAttribute('aria-pressed', String(selected === action.index));
          button.setAttribute('aria-label', `动作 ${action.order || action.index + 1}：${action.name}，${name}，波次 ${action.wave}`);
          button.setAttribute('aria-controls', 'flow-action-detail');
          button.title = action.name || '';
          button.append(element('span', 'flow-dot', String(action.order || action.index + 1).padStart(2, '0')),
            element('span', 'flow-node-label', action.name || '未命名动作'));
          button.addEventListener('click', () => selectAction(action));
          track.append(button);
        });
      } else track.append(element('div', 'flow-idle-line'));
      canvas.append(label, track);
    });
    updateScrollHint();
  }
  detail.id = 'flow-action-detail';
  root.querySelectorAll('[data-flow-scope]').forEach(button => {
    button.addEventListener('click', () => {
      scope = button.dataset.flowScope;
      root.querySelectorAll('[data-flow-scope]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      render();
    });
  });
  render();
  let lastWidth = viewport.clientWidth;
  new ResizeObserver(() => {
    if (viewport.clientWidth !== lastWidth) {
      lastWidth = viewport.clientWidth;
      render();
    }
  }).observe(viewport);
})();
