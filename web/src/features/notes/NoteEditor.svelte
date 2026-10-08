<script lang="ts">
  import { Editor } from "@tiptap/core";
  import StarterKit from "@tiptap/starter-kit";
  import { Markdown } from "@tiptap/markdown";
  import { TableKit } from "@tiptap/extension-table";
  import Image from "@tiptap/extension-image";
  import { Bold, Code, Heading2, Italic, Link, List, ListOrdered, Quote, Redo2, Undo2 } from "lucide-svelte";
  import { onMount } from "svelte";

  let { value, onchange, disabled = false }: { value: string; onchange: (value: string) => void; disabled?: boolean } = $props();
  let element: HTMLDivElement;
  let editor = $state<Editor>();
  let revision = $state(0);
  let linkForm = $state(false);
  let linkUrl = $state("");
  let linkError = $state("");
  const active = (name: string) => { void revision; return editor?.isActive(name) ?? false; };
  $effect(() => { editor?.setEditable(!disabled); });

  onMount(() => {
    const instance = new Editor({
      element,
      extensions: [StarterKit.configure({ link: { openOnClick: true, autolink: true, defaultProtocol: "https", HTMLAttributes: { target: "_blank", rel: "noopener noreferrer" } } }), Markdown, TableKit, Image],
      content: value,
      contentType: "markdown",
      editorProps: { attributes: { class: "markdown note-document", role: "textbox", "aria-label": "Note body", "aria-multiline": "true" } },
      onUpdate: ({ editor: updated }) => onchange(updated.getMarkdown()),
      onTransaction: () => revision++
    });
    editor = instance;
    return () => instance.destroy();
  });

  function showLink() {
    linkUrl = String(editor?.getAttributes("link").href ?? "");
    linkError = ""; linkForm = true;
  }
  function setLink() {
    if (!linkUrl.trim()) { editor?.chain().focus().extendMarkRange("link").unsetLink().run(); linkForm = false; return; }
    try {
      const url = new URL(linkUrl.includes(":") ? linkUrl : `https://${linkUrl}`);
      if (!["https:", "http:", "mailto:"].includes(url.protocol)) throw new Error();
      const chain = editor?.chain().focus().extendMarkRange("link");
      if (editor?.state.selection.empty && !editor.isActive("link")) chain?.insertContent({ type: "text", text: url.href, marks: [{ type: "link", attrs: { href: url.href, target: "_blank", rel: "noopener noreferrer" } }] }).run();
      else chain?.setLink({ href: url.href, target: "_blank", rel: "noopener noreferrer" }).run();
      linkForm = false;
    } catch { linkError = "Enter a valid HTTP, HTTPS or email link"; }
  }
</script>

<fieldset class="note-editor" {disabled}>
  <div class="format-bar" role="toolbar" aria-label="Text formatting">
    <button type="button" title="Bold" aria-pressed={active("bold")} onmousedown={(event) => event.preventDefault()} onclick={() => editor?.chain().focus().toggleBold().run()}><Bold size={16} /></button>
    <button type="button" title="Italic" aria-pressed={active("italic")} onmousedown={(event) => event.preventDefault()} onclick={() => editor?.chain().focus().toggleItalic().run()}><Italic size={16} /></button>
    <button type="button" title="Heading" aria-pressed={active("heading")} onclick={() => editor?.chain().focus().toggleHeading({level: 2}).run()}><Heading2 size={16} /></button>
    <button type="button" title="Bullet list" aria-pressed={active("bulletList")} onclick={() => editor?.chain().focus().toggleBulletList().run()}><List size={16} /></button>
    <button type="button" title="Numbered list" aria-pressed={active("orderedList")} onclick={() => editor?.chain().focus().toggleOrderedList().run()}><ListOrdered size={16} /></button>
    <button type="button" title="Quote" aria-pressed={active("blockquote")} onclick={() => editor?.chain().focus().toggleBlockquote().run()}><Quote size={16} /></button>
    <button type="button" title="Code" aria-pressed={active("code")} onclick={() => editor?.chain().focus().toggleCode().run()}><Code size={16} /></button>
    <button type="button" title="Link" aria-pressed={active("link")} onclick={showLink}><Link size={16} /></button>
    <span class="separator"></span>
    <button type="button" title="Undo" onclick={() => editor?.chain().focus().undo().run()}><Undo2 size={16} /></button>
    <button type="button" title="Redo" onclick={() => editor?.chain().focus().redo().run()}><Redo2 size={16} /></button>
  </div>
  {#if linkForm}<div class="link-form"><input aria-label="Link URL" placeholder="https://" bind:value={linkUrl} onkeydown={(event) => { if (event.key === "Enter") { event.preventDefault(); setLink(); } }} /><button type="button" onclick={setLink}>Apply</button><button type="button" onclick={() => linkForm = false}>Cancel</button></div>{#if linkError}<p role="alert">{linkError}</p>{/if}{/if}
  <div class="document-scroll"><div bind:this={element}></div></div>
</fieldset>

<style>
  .note-editor { display:flex; flex-direction:column; min-width:0; min-height:0; flex:1; border:1px solid var(--color-border); border-radius:6px; background:var(--color-surface); overflow:hidden; }
  .format-bar { display:flex; flex-wrap:wrap; gap:3px; padding:8px; border-bottom:1px solid var(--color-border); }
  button { display:inline-flex; align-items:center; justify-content:center; min-width:32px; height:32px; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:12px; }
  button:hover,button[aria-pressed=true] { background:var(--color-accent-soft); color:var(--color-accent); }
  .separator { width:1px; background:var(--color-border); margin:4px; }
  .document-scroll { overflow:auto; min-height:0; flex:1; }
  .document-scroll :global(.note-document) { min-height:300px; padding:24px; outline:none; max-width:850px; margin:auto; }
  :global(.note-document strong) { font-weight:700; }
  :global(.note-document table) { border-collapse:collapse; width:100%; }
  :global(.note-document td),:global(.note-document th) { border:1px solid var(--color-border); padding:8px; }
  .link-form { display:flex; flex-wrap:wrap; gap:8px; padding:8px; border-bottom:1px solid var(--color-border); }
  input { flex:1; min-width:140px; background:var(--color-surface); border:1px solid var(--color-border); padding:6px; border-radius:4px; }
  p { color:var(--color-danger); padding:8px; font-size:12px; }
</style>
