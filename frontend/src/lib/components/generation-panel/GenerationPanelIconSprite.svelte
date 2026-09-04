<!-- Icon sprite ported verbatim from design-proposals/generation-panel-concept.html
     (the <symbol> block just above <header class="prototype-bar">). Rendered
     once inside the panel's scoped root; every icon in the ported markup
     references it via <svg class="icon"><use href="#i-…"/></svg>.
     i-chevron-left is an addition (borrowed from generation-three-pane-integration.html's
     own sprite, same stroke idiom): the session-history back button is real
     functionality the concept mock's popover demo never shows. -->
<svg width="0" height="0" aria-hidden="true" style="position:absolute">
	<symbol id="i-x" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></symbol>
	<symbol id="i-loop" viewBox="0 0 24 24"
		><path d="M20 12a8 8 0 1 1-2.34-5.66" /><path d="M20 4v6h-6" /></symbol
	>
	<symbol id="i-pause" viewBox="0 0 24 24"><path d="M9 6v12M15 6v12" /></symbol>
	<symbol id="i-hourglass" viewBox="0 0 24 24"
		><path
			d="M7 3h10M7 21h10M8 3c0 4 1.5 6 4 9-2.5 3-4 5-4 9M16 3c0 4-1.5 6-4 9 2.5 3 4 5 4 9"
		/></symbol
	>
	<symbol id="i-sliders" viewBox="0 0 24 24"
		><path d="M4 7h5M15 7h5M4 17h9M17 17h3M9 4v6M13 14v6" /></symbol
	>
	<symbol id="i-history" viewBox="0 0 24 24"
		><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6" /><path d="M4 4v4.6h4.6M12 8v4l3 2" /></symbol
	>
	<symbol id="i-session" viewBox="0 0 24 24"><path d="M6 4h12v16l-6-3-6 3Z" /></symbol>
	<symbol id="i-save" viewBox="0 0 24 24"
		><path d="M5 4h12l2 2v14H5Z" /><path d="M8 4v6h8V4M8 20v-6h8v6" /></symbol
	>
	<symbol id="i-chevron" viewBox="0 0 24 24"><path d="m8 10 4 4 4-4" /></symbol>
	<symbol id="i-chevron-left" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></symbol>
	<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></symbol>
	<symbol id="i-trash" viewBox="0 0 24 24"
		><path
			d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"
		/></symbol
	>
	<symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h14M14 7l5 5-5 5" /></symbol>
	<!-- Addition: plugin `generation.panel.modes` drawer tabs are real
	     functionality the concept mock's drawer-trigger pair (settings,
	     history) never depicts. Path reused verbatim from the app's own
	     IconLibrary 'extension' entry for visual consistency. -->
	<symbol id="i-extension" viewBox="0 0 24 24"
		><path
			d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z"
		/></symbol
	>
</svg>
