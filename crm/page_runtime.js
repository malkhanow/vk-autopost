/**
 * Мини-рантайм шаблонов вместо support.js.
 *
 * Понимает ровно то, что использует разметка карточки клиентов:
 *   {{ путь.к.значению }}   — в тексте и в значениях атрибутов
 *   <sc-for list="{{ … }}" as="имя">   — повтор детей по списку
 *   <sc-if value="{{ … }}">            — вывод детей по условию
 *   onClick / onChange                 — обработчики из renderVals
 *   style-hover                        — стиль при наведении
 *
 * Узлы не пересоздаются, а обновляются на месте — поэтому фокус и
 * каретка в поле ввода не сбрасываются на каждом нажатии клавиши.
 */
(function (global) {
  'use strict';

  /* ---- вычисление выражений ---- */

  function lookup(expr, scope, values) {
    var path = String(expr).trim();
    if (path === 'true') return true;
    if (path === 'false') return false;
    if (path === 'null' || path === '') return null;

    var parts = path.split('.');
    var cur = parts[0] in scope ? scope[parts[0]] : values[parts[0]];
    for (var i = 1; i < parts.length; i++) {
      if (cur === null || cur === undefined) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  var TOKEN = /\{\{([^}]*)\}\}/g;
  var ONLY_TOKEN = /^\s*\{\{([^}]*)\}\}\s*$/;

  /** Строка целиком из одного выражения возвращается как есть — функцией, массивом, числом. */
  function interpolate(text, scope, values) {
    var single = ONLY_TOKEN.exec(text);
    if (single) return lookup(single[1], scope, values);
    if (text.indexOf('{{') < 0) return text;
    return text.replace(TOKEN, function (_, expr) {
      var value = lookup(expr, scope, values);
      return value === null || value === undefined ? '' : String(value);
    });
  }

  var EVENTS = {
    click: 'click', change: 'input', input: 'input',
    // перетаскивание рубрик: порядок меняется мышью
    dragstart: 'dragstart', dragover: 'dragover', dragenter: 'dragenter',
    dragleave: 'dragleave', drop: 'drop', dragend: 'dragend',
    // ручка «⠿»: перетаскивание включается только с неё, чтобы
    // выделение текста в полях строки продолжало работать
    mousedown: 'mousedown'
  };

  /* ---- разметка -> дерево описаний ---- */

  function build(node, scope, values, out) {
    if (node.nodeType === 3) {
      var raw = node.nodeValue;
      var text = raw.indexOf('{{') < 0 ? raw : String(interpolate(raw, scope, values) || '');
      out.push({ text: text });
      return;
    }
    if (node.nodeType !== 1) return;

    var tag = node.tagName.toLowerCase();

    if (tag === 'sc-for') {
      var list = interpolate(node.getAttribute('list') || '', scope, values);
      if (!Array.isArray(list)) return;
      var alias = node.getAttribute('as') || 'item';
      for (var i = 0; i < list.length; i++) {
        var inner = Object.create(scope);
        inner[alias] = list[i];
        inner.$index = i;
        buildChildren(node, inner, values, out);
      }
      return;
    }

    if (tag === 'sc-if') {
      if (interpolate(node.getAttribute('value') || '', scope, values)) {
        buildChildren(node, scope, values, out);
      }
      return;
    }

    var vnode = { tag: tag, attrs: {}, events: {}, children: [] };
    for (var a = 0; a < node.attributes.length; a++) {
      var attr = node.attributes[a];
      var name = attr.name.toLowerCase();
      if (name.indexOf('hint-') === 0) continue;      // подсказки редактора холста
      var value = interpolate(attr.value, scope, values);
      if (name.indexOf('on') === 0 && EVENTS[name.slice(2)]) vnode.events[name.slice(2)] = value;
      else vnode.attrs[name] = value;
    }
    buildChildren(node, scope, values, vnode.children);
    out.push(vnode);
  }

  function buildChildren(node, scope, values, out) {
    for (var i = 0; i < node.childNodes.length; i++) {
      build(node.childNodes[i], scope, values, out);
    }
  }

  /* ---- дерево описаний -> DOM ---- */

  function hover(el) {
    if (el.__hoverBound) return;
    el.__hoverBound = true;
    el.addEventListener('mouseenter', function () {
      if (el.__hoverStyle) el.style.cssText = (el.__baseStyle || '') + ';' + el.__hoverStyle;
    });
    el.addEventListener('mouseleave', function () {
      el.style.cssText = el.__baseStyle || '';
    });
  }

  function applyAttrs(el, oldAttrs, attrs) {
    Object.keys(oldAttrs).forEach(function (name) {
      if (name in attrs) return;
      if (name === 'style') el.style.cssText = el.__baseStyle = '';
      else if (name === 'checked') el.checked = false;
      else el.removeAttribute(name);
    });

    Object.keys(attrs).forEach(function (name) {
      var value = attrs[name];

      if (name === 'style') {
        var css = value === null || value === undefined ? '' : String(value);
        if (el.__baseStyle !== css) {
          el.__baseStyle = css;
          el.style.cssText = css;
        }
        return;
      }
      if (name === 'style-hover') {
        el.__hoverStyle = value === null || value === undefined ? '' : String(value);
        hover(el);
        return;
      }
      if (name === 'value' && ('value' in el)) {
        var next = value === null || value === undefined ? '' : String(value);
        if (el !== document.activeElement) { el.value = next; }
        return;
      }
      if (name === 'checked') {
        el.checked = !!value;
        return;
      }
      if (value === false || value === null || value === undefined) {
        el.removeAttribute(name);
        return;
      }
      var str = String(value);
      if (el.getAttribute(name) !== str) el.setAttribute(name, str);
    });
  }

  function bindEvents(el, events) {
    el.__handlers = events;
    Object.keys(EVENTS).forEach(function (kind) {
      if (!events[kind] || el['__bound_' + kind]) return;
      el['__bound_' + kind] = true;
      el.addEventListener(EVENTS[kind], function (e) {
        var handler = el.__handlers && el.__handlers[kind];
        if (typeof handler === 'function') handler(e);
      });
    });
  }

  function create(vnode) {
    if (vnode.text !== undefined) {
      vnode.el = document.createTextNode(vnode.text);
      return vnode.el;
    }
    var el = document.createElement(vnode.tag);
    vnode.el = el;
    // дети раньше атрибутов: <select value="…"> должен видеть свои <option>
    vnode.children.forEach(function (child) { el.appendChild(create(child)); });
    applyAttrs(el, {}, vnode.attrs);
    bindEvents(el, vnode.events);
    return el;
  }

  function sameKind(a, b) {
    if (a.text !== undefined || b.text !== undefined) {
      return a.text !== undefined && b.text !== undefined;
    }
    return a.tag === b.tag;
  }

  function patchNode(oldVnode, vnode) {
    var el = vnode.el = oldVnode.el;
    if (vnode.text !== undefined) {
      if (el.nodeValue !== vnode.text) el.nodeValue = vnode.text;
      return;
    }
    applyAttrs(el, oldVnode.attrs, vnode.attrs);
    bindEvents(el, vnode.events);
    patchChildren(el, oldVnode.children, vnode.children);
  }

  function patchChildren(parent, oldList, list) {
    var count = Math.max(oldList.length, list.length);
    for (var i = 0; i < count; i++) {
      var was = oldList[i];
      var now = list[i];

      if (!now) {
        if (was && was.el && was.el.parentNode === parent) parent.removeChild(was.el);
      } else if (!was) {
        parent.appendChild(create(now));
      } else if (sameKind(was, now)) {
        patchNode(was, now);
      } else {
        parent.replaceChild(create(now), was.el);
      }
    }
  }

  /* ---- компонент ---- */

  function Logic() {
    this.props = {};
  }

  Logic.prototype.setState = function (updater) {
    var patch = typeof updater === 'function' ? updater(this.state) : updater;
    this.state = Object.assign({}, this.state, patch);
    if (this.__onChange) this.__onChange();
  };

  /**
   * mount(template, Component, props, root) — собирает страницу и держит её
   * в актуальном состоянии. Перерисовка откладывается в микрозадачу, чтобы
   * несколько setState подряд давали один проход.
   */
  function mount(template, Component, props, root) {
    var component = new Component();
    component.props = props || {};

    var vnodes = [];
    var scheduled = false;

    function draw() {
      scheduled = false;
      var values;
      try {
        values = component.renderVals();
      } catch (err) {
        console.error('Ошибка renderVals:', err);
        return;
      }
      var next = [];
      var scope = Object.create(null);
      for (var i = 0; i < template.childNodes.length; i++) {
        build(template.childNodes[i], scope, values, next);
      }
      try {
        patchChildren(root, vnodes, next);
        vnodes = next;
      } catch (err) {
        console.error('Ошибка отрисовки:', err);
      }
    }

    component.__onChange = function () {
      if (scheduled) return;
      scheduled = true;
      Promise.resolve().then(draw);
    };

    draw();
    return component;
  }

  global.DCLogic = Logic;
  global.mountPage = mount;
})(window);
