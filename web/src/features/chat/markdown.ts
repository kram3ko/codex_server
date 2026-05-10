import DOMPurify from "dompurify";
import hljs from "highlight.js";
import { Marked } from "marked";

const marked = new Marked({
  async: false,
  gfm: true,
  breaks: true
});

marked.use({
  hooks: {
    postprocess(html) {
      return DOMPurify.sanitize(html);
    }
  },
  renderer: {
    code({ text, lang }) {
      const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      const value = hljs.highlight(text, { language }).value;
      return `<pre><code class="hljs language-${language}">${value}</code></pre>`;
    }
  }
});

export function renderMarkdown(source: string): string {
  return marked.parse(source) as string;
}
