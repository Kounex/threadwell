// A small, dependency-free Markdown subset for agent messages: paragraphs (line breaks kept), headings,
// nested lists, quotes, tables, rules, fenced code, inline code, emphasis and links.
// Every piece of source text is escaped. The only markup added is this fixed set of tags, and only
// absolute http(s) URLs become links; local file references stay readable and copyable as code.
(function (root) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const WEB = /^https?:\/\/[^\s<>"'`]+$/i;
  const LIST = /^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
  const FENCE = /^( *)(`{3,}|~{3,})[^`]*$/;
  const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
  const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const QUOTE = /^ {0,3}> ?(.*)$/;
  const DIVIDER = /^ *\|? *:?-{2,}:? *(\| *:?-{2,}:? *)*\|? *$/;
  const indent = line => line.match(/^ */)[0].length;
  const starts = line => FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || LIST.test(line);

  function anchor(href, label) {
    return '<a href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+label+'</a>';
  }

  function inline(text) {
    const kept = [], raw = [];
    const hold = (html, text = '') => { raw.push(text); return '\u0000'+(kept.push(html)-1)+'\u0000'; };
    const plain = value => value.replace(/\u0000(\d+)\u0000/g, (_, n) => raw[n]);
    let out = String(text).replace(/\u0000/g, '');
    out = out.replace(/(`+)(?!`)([\s\S]*?[^`])\1(?!`)/g, (_, ticks, code) => hold('<code>'+esc(code.replace(/^ (.*) $/, '$1'))+'</code>', code));
    out = out.replace(/\[([^\]\n]+)\]\( *<?([^()\s<>]+(?:\([^()\s]*\))?[^()\s<>]*)>?(?: +"[^"\n]*")? *\)/g, (_, label, target) => {
      const shown = emphasis(esc(label));
      if (WEB.test(target)) return hold(anchor(target, shown));
      // A path on the machine that wrote the message: a browser can't open it, so show it to copy.
      return hold(plain(label) === target ? '<code class="ref">'+esc(target)+'</code>' : shown+' <code class="ref">'+esc(target)+'</code>');
    });
    out = out.replace(/\bhttps?:\/\/[^\s<>"'`\u0000]+/gi, url => {
      let tail = '';
      while (/[.,;:!?'"]$/.test(url) || (url.endsWith(')') && (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length)) {
        tail = url.slice(-1)+tail; url = url.slice(0, -1);
      }
      return hold(anchor(url, esc(url)))+tail;
    });
    out = emphasis(esc(out));
    for (let depth = 0; depth < 4 && out.includes('\u0000'); depth++) out = out.replace(/\u0000(\d+)\u0000/g, (_, n) => kept[n]);
    return out;
  }

  function emphasis(html) {
    return html
      .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
      // Single * or _ only between spaces or punctuation, so paths like /_forever_/ and a*b*c stay literal.
      .replace(/(^|[\s(])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?=$|[\s).,;:!?])/g, '$1<em>$2</em>')
      .replace(/(^|[\s(])_(?=[^\s_])([^_\n]*?[^\s_])_(?=$|[\s).,;:!?])/g, '$1<em>$2</em>')
      .replace(/~~(?=\S)([^~\n]*?\S)~~/g, '<del>$1</del>');
  }

  function cells(row) {
    return row.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
  }

  function table(lines, i) {
    const align = cells(lines[i+1]).map(c => c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '');
    const row = (tag, values) => '<tr>'+align.map((a, n) => '<'+tag+(a ? ' class="'+a+'"' : '')+'>'+inline(values[n] ?? '')+'</'+tag+'>').join('')+'</tr>';
    let html = '<div class="table"><table><thead>'+row('th', cells(lines[i]))+'</thead><tbody>';
    for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) html += row('td', cells(lines[i]));
    return [html+'</tbody></table></div>', i];
  }

  function list(lines, i) {
    const head = lines[i].match(LIST), base = head[1].length, ordered = /\d/.test(head[2]);
    const items = [];
    let offset = 0;
    while (i < lines.length) {
      const line = lines[i], m = line.match(LIST);
      if (m && m[1].length >= base && m[1].length <= base+1) {
        if (/\d/.test(m[2]) !== ordered) break;
        items.push({lines: [m[3]], loose: false});
        offset = m[1].length+m[2].length+1;
        i++;
        continue;
      }
      const item = items[items.length-1];
      if (!line.trim()) {
        let next = i+1;
        while (next < lines.length && !lines[next].trim()) next++;
        const after = lines[next] || '', sibling = after.match(LIST);
        if (next < lines.length && (indent(after) >= base+2 || (sibling && sibling[1].length <= base+1 && sibling[1].length >= base))) {
          item.lines.push('');
          item.loose = item.loose || indent(after) >= base+2;
          i++;
          continue;
        }
        break;
      }
      if (indent(line) >= base+2) {
        item.lines.push(line.slice(Math.min(offset, indent(line))));
        i++;
        continue;
      }
      // A lazy continuation of the item's paragraph.
      if (item.lines[item.lines.length-1].trim() && !starts(line)) {
        item.lines.push(line.trim());
        i++;
        continue;
      }
      break;
    }
    while (items.length && !items[items.length-1].lines[items[items.length-1].lines.length-1].trim()) items[items.length-1].lines.pop();
    const number = ordered ? parseInt(head[2], 10) : 1;
    const open = ordered ? '<ol'+(number !== 1 ? ' start="'+number+'"' : '')+'>' : '<ul>';
    const body = items.map(item => {
      const html = render(item.lines.join('\n'));
      return '<li>'+(item.loose ? html : html.replace(/^<p>([\s\S]*?)<\/p>/, '$1'))+'</li>';
    }).join('');
    return [open+body+(ordered ? '</ol>' : '</ul>'), i];
  }

  function render(source) {
    const lines = String(source ?? '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n');
    const paragraph = [];
    let html = '', i = 0, m;
    const flush = () => {
      if (paragraph.length) html += '<p>'+paragraph.map(inline).join('<br>')+'</p>';
      paragraph.length = 0;
    };
    while (i < lines.length) {
      const line = lines[i];
      if ((m = line.match(FENCE))) {
        flush();
        const marker = m[2], depth = m[1].length, code = [];
        for (i++; i < lines.length && !(lines[i].trim().startsWith(marker) && /^[`~]+$/.test(lines[i].trim())); i++) {
          code.push(lines[i].slice(Math.min(depth, indent(lines[i]))));
        }
        i++;
        html += '<pre><code>'+esc(code.join('\n'))+'</code></pre>';
      } else if (!line.trim()) {
        flush();
        i++;
      } else if ((m = line.match(HEADING))) {
        flush();
        const level = Math.min(6, Math.max(3, m[1].length+1));
        html += '<h'+level+'>'+inline(m[2])+'</h'+level+'>';
        i++;
      } else if (RULE.test(line)) {
        flush();
        html += '<hr>';
        i++;
      } else if (QUOTE.test(line)) {
        flush();
        const quoted = [];
        for (; i < lines.length && (m = lines[i].match(QUOTE)); i++) quoted.push(m[1]);
        html += '<blockquote>'+render(quoted.join('\n'))+'</blockquote>';
      } else if (line.includes('|') && i+1 < lines.length && DIVIDER.test(lines[i+1]) && lines[i+1].includes('-')) {
        flush();
        [m, i] = table(lines, i);
        html += m;
      } else if (LIST.test(line) && (!paragraph.length || !/^ *\d/.test(line) || /^ *1[.)]/.test(line))) {
        flush();
        [m, i] = list(lines, i);
        html += m;
      } else {
        paragraph.push(line.trim());
        i++;
      }
    }
    flush();
    return html;
  }

  root.renderMarkdown = render;
  if (typeof module === 'object' && module.exports) module.exports = {render};
})(typeof window === 'undefined' ? globalThis : window);
