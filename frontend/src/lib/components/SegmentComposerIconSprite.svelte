<!--
	The mock's <symbol> sprite, trimmed to the glyphs the segment composer
	actually uses (library/template/braces/more/grip/copy/eye/eye-off/pencil/
	save/trash/break/chevrons/plus/info). Rendered once per SegmentedPromptEditor
	instance — a hidden, zero-size <svg> — and referenced everywhere else in this
	surface as `<svg class="icon"><use href="#i-name"/></svg>`, exactly as the
	mock does. Symbol ids can legitimately repeat across multiple editor
	instances on one page (Video Director mounts several); every instance
	defines the identical markup, so a duplicate id resolves to indistinguishable
	content and <use> never renders wrong.
-->
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
	<symbol id="i-library" viewBox="0 0 24 24"><path d="M4 5h5v14H4ZM9 7h5v12H9ZM14 4h6v15h-6Z" /></symbol>
	<symbol id="i-template" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 10v10" /></symbol>
	<symbol id="i-braces" viewBox="0 0 24 24"><path d="M9 3H7a2 2 0 0 0-2 2v4l-2 3 2 3v4a2 2 0 0 0 2 2h2M15 3h2a2 2 0 0 1 2 2v4l2 3-2 3v4a2 2 0 0 1-2 2h-2" /></symbol>
	<symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></symbol>
	<symbol id="i-grip" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1" fill="currentColor" stroke="none" /><circle cx="15" cy="6" r="1" fill="currentColor" stroke="none" /><circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="15" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="9" cy="18" r="1" fill="currentColor" stroke="none" /><circle cx="15" cy="18" r="1" fill="currentColor" stroke="none" /></symbol>
	<symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" /></symbol>
	<symbol id="i-eye-off" viewBox="0 0 24 24"><path d="m3 3 18 18M10.7 10.7a2 2 0 0 0 2.6 2.6M9.9 5.2A10 10 0 0 1 12 5c5 0 8.5 4.5 9.5 7a13 13 0 0 1-2.1 3.3M6.2 6.2A13 13 0 0 0 2.5 12c1 2.5 4.5 7 9.5 7 1.4 0 2.7-.3 3.8-.8" /></symbol>
	<symbol id="i-eye" viewBox="0 0 24 24"><path d="M2.5 12C3.5 9.5 7 5 12 5s8.5 4.5 9.5 7c-1 2.5-4.5 7-9.5 7s-8.5-4.5-9.5-7Z" /><circle cx="12" cy="12" r="2.5" /></symbol>
	<symbol id="i-chevron-down" viewBox="0 0 24 24"><path d="m7 9 5 5 5-5" /></symbol>
	<symbol id="i-chevron-up" viewBox="0 0 24 24"><path d="m7 15 5-5 5 5" /></symbol>
	<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></symbol>
	<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></symbol>
	<symbol id="i-file" viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6Z" /><path d="M14 3v5h5" /></symbol>
	<symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></symbol>
	<symbol id="i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></symbol>
	<symbol id="i-sliders" viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" /><circle cx="16" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="13" cy="18" r="2" /></symbol>
	<symbol id="i-shuffle" viewBox="0 0 24 24"><path d="M4 7h3c5 0 5 10 10 10h3M17 4l3 3-3 3M4 17h3c2 0 3.3-1.6 4.5-3.5M16 7h4M17 14l3 3-3 3" /></symbol>
	<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></symbol>
	<symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 6h7l2 2h9v11H3Z" /></symbol>
	<symbol id="i-pencil" viewBox="0 0 24 24"><path d="m4 20 4-1 11-11-3-3L5 16Z" /><path d="m14 7 3 3" /></symbol>
	<symbol id="i-break" viewBox="0 0 24 24"><path d="M4 6h16M4 18h16M9 9l3 3-3 3M15 9l-3 3 3 3" /></symbol>
	<symbol id="i-save" viewBox="0 0 24 24"><path d="M5 3h12l2 2v16H5Z" /><path d="M8 3v6h8V3M8 21v-7h8v7" /></symbol>
	<symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" /></symbol>
</svg>
