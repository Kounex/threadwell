// Run with: node tests/markdown.test.js (tests/smoke.py runs it when node is installed).
const assert = require('assert');
const {render} = require('../board/static/markdown.js');
const eq = (source, html) => assert.strictEqual(render(source), html, source);
const has = (source, part) => assert.ok(render(source).includes(part), render(source));
const lacks = (source, part) => assert.ok(!render(source).includes(part), render(source));

// Paragraphs keep single line breaks (launcher diagnostics) and split on blank lines.
eq('one\ntwo\n\nthree', '<p>one<br>two</p><p>three</p>');
// Headings sit below the page's h1/h2.
eq('## Peer check\n### 0. Blocker', '<h3>Peer check</h3><h4>0. Blocker</h4>');
// Inline code, bold, italics; snake_case and paths stay literal.
eq('Use `a<b>` and **this**, *maybe* not snake_case or /_forever_/WTF', '<p>Use <code>a&lt;b&gt;</code> and <strong>this</strong>, <em>maybe</em> not snake_case or /_forever_/WTF</p>');
// Web links open; local references stay copyable code with their line number.
eq('[Blizzard support](https://example.org/t/1) · [Polling loop](/home/u/agent/app/x.dart:489)',
   '<p><a href="https://example.org/t/1" target="_blank" rel="noopener noreferrer">Blizzard support</a> · Polling loop <code class="ref">/home/u/agent/app/x.dart:489</code></p>');
eq('[`store.py:95`](store.py:95)', '<p><code class="ref">store.py:95</code></p>');
has('See https://example.org/a_(b). Next', '<a href="https://example.org/a_(b)" target="_blank" rel="noopener noreferrer">https://example.org/a_(b)</a>. Next');
// Lists: nested, ordered with a start, lazy continuation, loose items.
eq('1. **Claim** (`x`): moves\n   - lease kept\n   - pulse\n2. Close', '<ol><li><strong>Claim</strong> (<code>x</code>): moves<ul><li>lease kept</li><li>pulse</li></ul></li><li>Close</li></ol>');
eq('3. third\n4. fourth', '<ol start="3"><li>third</li><li>fourth</li></ol>');
eq('- a\ncontinued\n- b', '<ul><li>a<br>continued</li><li>b</li></ul>');
eq('- a\n\n  more about a\n- b', '<ul><li><p>a</p><p>more about a</p></li><li>b</li></ul>');
eq('Steps:\n- one\n- two', '<p>Steps:</p><ul><li>one</li><li>two</li></ul>');
eq('Version\n2026. was a year', '<p>Version<br>2026. was a year</p>');
// Fences keep indentation, including the first line.
eq('```py\n    indented()\nback()\n```', '<pre><code>    indented()\nback()</code></pre>');
eq('- item\n\n  ```\n  code\n  ```', '<ul><li><p>item</p><pre><code>code</code></pre></li></ul>');
// Quotes, rules, tables.
eq('> quoted **bold**\n> more', '<blockquote><p>quoted <strong>bold</strong><br>more</p></blockquote>');
eq('a\n\n---\n\nb', '<p>a</p><hr><p>b</p>');
eq('| A | B |\n|---|--:|\n| `x` | 2 |', '<div class="table"><table><thead><tr><th>A</th><th class="right">B</th></tr></thead><tbody><tr><td><code>x</code></td><td class="right">2</td></tr></tbody></table></div>');

// Nothing in the source becomes markup of its own.
for (const evil of ['<img src=x onerror=alert(1)>', '[x](javascript:alert(1))', '[x](https://a.org/"onmouseover="alert(1))',
  '**<script>alert(1)</script>**', '`</code><script>`', '[<b>x</b>](https://a.org)', '\u00000\u0000', '| <i>x</i> |\n|---|', '```\n<script>\n```']) {
  const html = render(evil);
  assert.ok(!/<(script|img|i|b)[\s>]/i.test(html), evil+' -> '+html);
  assert.ok(!/href="(?!https?:)/i.test(html) && !/"on\w+=/i.test(html), evil+' -> '+html);
}
lacks('[x](javascript:alert(1))', '<a ');
console.log('Passed markdown rendering checks.');
