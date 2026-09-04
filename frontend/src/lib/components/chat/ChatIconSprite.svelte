<!-- Icon sprite ported verbatim from design-proposals/llm-chat-concept.html
     (the <symbol> block just above <main class="chat-shell">). Rendered once
     inside the chat shell; every icon in the ported markup references it via
     <svg class="icon"><use href="#i-…"/></svg>. -->
<svg aria-hidden="true" width="0" height="0" style="position:absolute">
	<symbol id="i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16" /></symbol>
	<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></symbol>
	<symbol id="i-search" viewBox="0 0 24 24"
		><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></symbol
	>
	<symbol id="i-edit" viewBox="0 0 24 24"
		><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z" /></symbol
	>
	<symbol id="i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></symbol>
	<symbol id="i-more" viewBox="0 0 24 24"
		><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle
			cx="12"
			cy="12"
			r="1"
			fill="currentColor"
			stroke="none"
		/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></symbol
	>
	<symbol id="i-chevron" viewBox="0 0 24 24"><path d="m8 10 4 4 4-4" /></symbol>
	<symbol id="i-sparkles" viewBox="0 0 24 24"
		><path
			d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2ZM6 14l.8 2.2L9 17l-2.2.8L6 20l-.8-2.2L3 17l2.2-.8Zm12-1 1 2.8 2.8 1-2.8 1-1 2.8-1-2.8-2.8-1 2.8-1Z"
		/></symbol
	>
	<symbol id="i-image" viewBox="0 0 24 24"
		><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="9" r="2" /><path
			d="m21 15-4.5-4.5L6 20"
		/></symbol
	>
	<symbol id="i-tools" viewBox="0 0 24 24"
		><path
			d="m14.7 6.3 3-3a4 4 0 0 1-5 5l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a4 4 0 0 1 5-5l-3 3Z"
		/><path d="m15 13 6 6-2 2-6-6" /></symbol
	>
	<symbol id="i-memory" viewBox="0 0 24 24"
		><path
			d="M9 5a3 3 0 0 0-5.4 1.8A3 3 0 0 0 3 12a3 3 0 0 0 1 5.8A3 3 0 0 0 9 20ZM15 5a3 3 0 0 1 5.4 1.8A3 3 0 0 1 21 12a3 3 0 0 1-1 5.8A3 3 0 0 1 15 20ZM9 3v18M15 3v18"
		/></symbol
	>
	<symbol id="i-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5m-6 6 6-6 6 6" /></symbol>
	<symbol id="i-copy" viewBox="0 0 24 24"
		><rect x="9" y="9" width="11" height="11" rx="2" /><path
			d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"
		/></symbol
	>
	<symbol id="i-good" viewBox="0 0 24 24"
		><path
			d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Zm0 9 2 2h7.5a2 2 0 0 0 1.9-1.4l2.3-7A2 2 0 0 0 18.8 10H14l.7-3.3A3 3 0 0 0 12 3L7 10"
		/></symbol
	>
	<symbol id="i-bad" viewBox="0 0 24 24"
		><path
			d="M7 14V3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2Zm0-9 2-2h7.5a2 2 0 0 1 1.9 1.4l2.3 7a2 2 0 0 1-1.9 2.6H14l.7 3.3A3 3 0 0 1 12 21l-5-7"
		/></symbol
	>
	<symbol id="i-refresh" viewBox="0 0 24 24"
		><path d="M20 7v5h-5M4 17v-5h5" /><path
			d="M6.1 9a7 7 0 0 1 11.3-2L20 9M4 15l2.6 2a7 7 0 0 0 11.3-2"
		/></symbol
	>
	<symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></symbol>
	<symbol id="i-file" viewBox="0 0 24 24"
		><path d="M6 3h8l4 4v14H6Z" /><path d="M14 3v5h5" /></symbol
	>
	<symbol id="i-wand" viewBox="0 0 24 24"
		><path d="m15 4 5 5L8 21l-5-5Z" /><path
			d="m6 13 5 5M6 3v3M4.5 4.5h3M17 16v4M15 18h4M17 2v3M15.5 3.5h3"
		/></symbol
	>
	<symbol id="i-sliders" viewBox="0 0 24 24"
		><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" /><circle
			cx="16"
			cy="6"
			r="2"
		/><circle cx="8" cy="12" r="2" /><circle cx="13" cy="18" r="2" /></symbol
	>
	<symbol id="i-clock" viewBox="0 0 24 24"
		><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></symbol
	>
	<symbol id="i-info" viewBox="0 0 24 24"
		><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></symbol
	>
	<symbol id="i-sun" viewBox="0 0 24 24"
		><circle cx="12" cy="12" r="4" /><path
			d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"
		/></symbol
	>
	<symbol id="i-lock" viewBox="0 0 24 24"
		><rect x="5" y="10" width="14" height="11" rx="2" /><path
			d="M8 10V7a4 4 0 0 1 8 0v3"
		/></symbol
	>
	<symbol id="i-upload" viewBox="0 0 24 24"><path d="M12 16V4m-5 5 5-5 5 5M5 20h14" /></symbol>
	<symbol id="i-library" viewBox="0 0 24 24"
		><rect x="4" y="4" width="16" height="16" rx="2" /><path
			d="m4 15 4-4 3 3 3-3 6 6"
		/><circle cx="15.5" cy="8.5" r="1.5" /></symbol
	>
	<symbol id="i-paste" viewBox="0 0 24 24"
		><path d="M9 5h6M9 3h6v4H9Z" /><path
			d="M9 5H6a2 2 0 0 0-2 2v13h11v-3M15 11h5v10h-8v-7Z"
		/></symbol
	>
	<symbol id="i-pin" viewBox="0 0 24 24"
		><path d="m14 4 6 6-3 1-4 4-1 4-7-7 4-1 4-4Z" /><path d="m9 16-5 5" /></symbol
	>
	<symbol id="i-trash" viewBox="0 0 24 24"
		><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6" /></symbol
	>
</svg>
