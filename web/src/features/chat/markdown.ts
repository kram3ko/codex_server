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
      const fragment = DOMPurify.sanitize(html, { RETURN_DOM_FRAGMENT: true });
      for (const link of fragment.querySelectorAll("a[href]")) {
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
      }
      const container = document.createElement("div");
      container.append(fragment);
      return container.innerHTML;
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
